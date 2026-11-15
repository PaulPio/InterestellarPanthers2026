"""Sensor health, the real-else-simulated reader, the fallback sink, and cards with a fallback."""

from __future__ import annotations

import sqlite3
from typing import Any

from ccm_sim import contracts
from ccm_sim.mockapi.copyrules import violations
from ccm_sim.thresholds import load_thresholds
from ccm_sim.units import mmhg_to_ppm

from ccm_gateway import sensors
from ccm_gateway.analytics import STATUS_KEY, Params, _kv_get, process_pending
from ccm_gateway.db import add_sample
from ccm_gateway.fallback import FallbackSink

T0 = 1_794_700_800  # a whole minute and a whole 10-minute window
TH = load_thresholds()
SIM_MSG = {
    "co2": 812.0,
    "temp_c": 22.1,
    "rh": 41.0,
    "voc": 105.0,
    "pm1": 2.0,
    "pm25": 4.0,
    "pm10": 6.0,
    "cpm": 18.0,
    "c10s": 3.0,
}


def real(conn: sqlite3.Connection, t: float, seq: int, **fields: float | None) -> None:
    """One real cabin message, stored the way ingest does (raw row plus rollups)."""
    cols = ("co2", "temp_c", "rh", "voc", "pm1", "pm25", "pm10", "cpm", "c10s")
    values = [fields.get(c) for c in cols]
    conn.execute(
        "INSERT INTO raw_cabin (id, seq, t_meas, src, t_node, t_recv, co2, temp_c, rh, voc, pm1, "
        "pm25, pm10, cpm, c10s) VALUES ('C1', ?, ?, 'hw', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (seq, t, t, t, *values),
    )
    for col, value in zip(cols, values, strict=True):
        if value is not None and col in ("co2", "temp_c", "rh", "voc", "pm25", "cpm", "c10s"):
            add_sample(conn, t, "C1", col, float(value))


def healthy(**overrides: float | None) -> dict[str, float | None]:
    fields: dict[str, float | None] = {
        "co2": 812.0,
        "temp_c": 22.1,
        "rh": 41.0,
        "voc": 105.0,
        "pm1": 2.0,
        "pm25": 4.0,
        "pm10": 6.0,
        "cpm": 18.0,
        "c10s": 3.0,
    }
    fields.update(overrides)
    return fields


def test_states_fresh_stale_and_never_seen(conn: sqlite3.Connection) -> None:
    real(conn, T0, 1, **healthy(voc=None))
    states = sensors.assess(conn, "C1", T0 + 10)
    assert states == {"scd41": "ok", "sgp40": "offline", "pms5003": "ok", "geiger": "ok"}
    assert sensors.assess(conn, "C1", T0 + 120)["scd41"] == "offline"  # 120 s with nothing


def test_a_geiger_counting_nothing_is_offline(conn: sqlite3.Connection) -> None:
    for i in range(13):
        real(conn, T0 + i * 10, i, **healthy(c10s=0.0, cpm=0.0))
    assert sensors.assess(conn, "C1", T0 + 120)["geiger"] == "offline"


def test_going_offline_and_back_is_logged(conn: sqlite3.Connection) -> None:
    real(conn, T0, 1, **healthy())
    sensors.assess(conn, "C1", T0 + 5)
    # The other sensors keep reporting; only the SCD41 goes quiet.
    real(conn, T0 + 40, 2, **healthy(co2=None, temp_c=None, rh=None))
    real(conn, T0 + 70, 3, **healthy(co2=None, temp_c=None, rh=None))
    sensors.assess(conn, "C1", T0 + 75)
    assert sensors.offline(conn, "C1") == {"scd41": T0 + 75}
    real(conn, T0 + 300, 4, **healthy())
    sensors.assess(conn, "C1", T0 + 305)
    events = [r["reason"] for r in conn.execute("SELECT reason FROM events ORDER BY id")]
    assert events == [
        "SCD41 offline; CO2, temperature and humidity are simulated until it returns.",
        "SCD41 back online after 4 min; real readings resumed.",
    ]
    assert sensors.offline(conn, "C1") == {}


def test_real_data_wins_and_simulation_fills_holes(conn: sqlite3.Connection) -> None:
    add_sample(conn, T0, "C1", "co2", 800.0)
    add_sample(conn, T0, "C1:sim", "co2", 900.0)
    add_sample(conn, T0 + 60, "C1:sim", "co2", 950.0)
    rows = sensors.minute_rows(conn, "C1", "co2", T0, T0 + 120)
    assert [(m.total, m.simulated) for m in rows] == [(800.0, False), (950.0, True)]
    # zero real counts give way to simulated counts, but stand when there is nothing else
    add_sample(conn, T0, "C1", "c10s", 0.0)
    add_sample(conn, T0, "C1:sim", "c10s", 3.0)
    add_sample(conn, T0 + 60, "C1", "c10s", 0.0)
    rows = sensors.minute_rows(conn, "C1", "c10s", T0, T0 + 120)
    assert [(m.total, m.simulated) for m in rows] == [(3.0, True), (0.0, False)]


def test_the_sink_stores_only_offline_sensors(conn: sqlite3.Connection) -> None:
    real(conn, T0, 1, **healthy(voc=None, c10s=0.0, cpm=0.0))
    down = FallbackSink(conn).fill(SIM_MSG, T0 + 10)
    assert down == ["sgp40"]
    stored = {
        r["metric"] for r in conn.execute("SELECT metric FROM rollup_1m WHERE subject='C1:sim'")
    }
    assert stored == {"voc"}


def test_with_no_cabin_node_everything_is_simulated(conn: sqlite3.Connection) -> None:
    down = FallbackSink(conn).fill(SIM_MSG, T0)
    assert sorted(down) == ["geiger", "pms5003", "scd41", "sgp40"]


def card(status: dict[str, Any], domain: str) -> dict[str, Any]:
    return next(c for c in status["cards"] if c["domain"] == domain)


def test_a_failed_co2_sensor_is_simulated_and_flagged(conn: sqlite3.Connection) -> None:
    sink = FallbackSink(conn)
    ppm = mmhg_to_ppm(0.6, TH.cabin_pressure_mmhg)
    seq = 0
    for i in range(6 * 8):  # 8 minutes healthy
        seq += 1
        real(conn, T0 + i * 10, seq, **healthy(co2=ppm))
        sink.fill(SIM_MSG, T0 + i * 10)
    for i in range(6 * 8, 6 * 20):  # then the SCD41 stops answering for 12 minutes
        seq += 1
        real(conn, T0 + i * 10, seq, **healthy(co2=None, temp_c=None, rh=None))
        sink.fill(dict(SIM_MSG, co2=mmhg_to_ppm(2.7, TH.cabin_pressure_mmhg)), T0 + i * 10)
    process_pending(conn, Params(), TH)
    status = _kv_get(conn, STATUS_KEY)
    assert contracts.errors("status", status) == []
    air, thermal, radiation = (
        card(status, "air"),
        card(status, "thermal"),
        card(status, "radiation"),
    )
    assert air["numbers"][0]["value"] == 2.7  # the simulated CO2
    assert air["level"] == "yellow"  # levels follow the values shown
    assert air["fallback"]["sensors"] == ["SCD41"]
    assert "simulated" in air["fallback"]["sentence"]
    assert violations(air["fallback"]["sentence"]) == []
    assert thermal["fallback"]["sensors"] == ["SCD41"]
    assert radiation["fallback"] is None
    assert card(status, "cardio")["fallback"] is None


def test_the_warning_clears_when_the_sensor_returns(conn: sqlite3.Connection) -> None:
    sink = FallbackSink(conn)
    for i in range(30):
        real(conn, T0 + i * 10, i, **healthy(co2=None, temp_c=None, rh=None))
        sink.fill(SIM_MSG, T0 + i * 10)
    for i in range(30, 90):
        real(conn, T0 + i * 10, i, **healthy())
        sink.fill(SIM_MSG, T0 + i * 10)
    process_pending(conn, Params(), TH)
    status = _kv_get(conn, STATUS_KEY)
    assert card(status, "air")["fallback"] is None
    assert card(status, "air")["numbers"][0]["value"] == 0.6  # real 812 ppm again


def test_a_missing_co2_reading_is_never_shown_as_zero(conn: sqlite3.Connection) -> None:
    for i in range(60):
        real(conn, T0 + i * 10, i, **healthy(co2=None, temp_c=None, rh=None))
    process_pending(conn, Params(), TH)  # no fallback service running
    air = card(_kv_get(conn, STATUS_KEY), "air")
    assert "0.0 mmHg" not in air["reason"]
    assert air["reason"].startswith("No CO2 reading in the last 5 min; VOC index 105")


def test_history_merges_real_and_simulated_minutes(conn: sqlite3.Connection) -> None:
    from fastapi.testclient import TestClient

    from ccm_gateway.api import create_app
    from ccm_gateway.config import load_settings

    real(conn, T0, 1, **healthy())
    add_sample(conn, T0 + 60, "C1:sim", "co2", mmhg_to_ppm(1.0, TH.cabin_pressure_mmhg))
    conn.execute(
        "INSERT INTO node_status (node, id, state, src, t, last_seen_t) "
        "VALUES ('cabin', 'C1', 'online', 'hw', ?, ?)",
        (T0 + 120, T0 + 120),
    )
    conn.commit()
    body = (
        TestClient(create_app(conn, load_settings()))
        .get("/api/history?metric=co2_mmhg&hours=1")
        .json()
    )
    assert contracts.errors("history", body) == []
    assert [round(p["mean"], 2) for p in body["points"]] == [0.62, 1.0]


def test_firmware_without_its_co2_sensor_end_to_end(conn: sqlite3.Connection) -> None:
    """Real firmware messages with no SCD41 fields -> adapter -> ingest -> fallback -> API."""
    import json

    from fastapi.testclient import TestClient

    from ccm_gateway.adapter import normalize
    from ccm_gateway.api import create_app
    from ccm_gateway.config import load_settings
    from ccm_gateway.ingest import store

    sink = FallbackSink(conn)
    msg = {
        "node": "cabin",
        "id": "C1",
        "voc": 105,
        "pm1": 2,
        "pm25": 4,
        "pm10": 6,
        "cpm": 18,
        "c10s": 3,
    }
    for i in range(48):  # 8 minutes, every 10 s
        t = T0 + i * 10
        payload = json.dumps(dict(msg, seq=i, t_node=t)).encode()
        record = normalize("ccm/cabin/telemetry", payload, t + 0.3)
        assert record is not None
        store(conn, record)
        sink.fill(SIM_MSG, t)
    conn.commit()
    process_pending(conn, Params(), TH)
    status = TestClient(create_app(conn, load_settings())).get("/api/status").json()
    assert contracts.errors("status", status) == []
    air = card(status, "air")
    assert air["fallback"]["sensors"] == ["SCD41"]
    assert air["numbers"][0]["value"] == 0.6  # 812 ppm from the simulator
    assert air["numbers"][1]["value"] == 105  # the real VOC sensor still counts
    assert card(status, "radiation")["fallback"] is None
