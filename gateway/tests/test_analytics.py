from __future__ import annotations

import sqlite3
from typing import Any

from ccm_sim import contracts
from ccm_sim.mockapi.copyrules import status_strings, violations
from ccm_sim.thresholds import load_thresholds
from ccm_sim.units import mmhg_to_ppm

from ccm_gateway.analytics import (
    STATUS_KEY,
    CabinView,
    CrewView,
    Params,
    Tick,
    _kv_get,
    attribute,
    process_pending,
    rmssd,
)
from ccm_gateway.db import add_sample

T0 = 1_794_700_800  # a whole minute and a whole 10-minute window
TH = load_thresholds()


def cabin_minute(
    conn: sqlite3.Connection,
    t_min: int,
    *,
    co2_mmhg: float = 0.6,
    temp: float = 22.0,
    voc: float = 100.0,
    counts_10s: int = 3,
    seq0: int = 0,
) -> None:
    """Six cabin messages (one every 10 s) for the minute starting at ``t_min``."""
    ppm = mmhg_to_ppm(co2_mmhg, TH.cabin_pressure_mmhg)
    for i in range(6):
        t = t_min + i * 10
        conn.execute(
            "INSERT INTO raw_cabin (id, seq, t_meas, src, t_node, t_recv, co2, temp_c, rh, voc, "
            "pm1, pm25, pm10, cpm, c10s) VALUES ('C1',?,?,'hw',?,?,?,?,41,?,2,4,6,?,?)",
            (seq0 + (t - T0) // 10, t, t, t, ppm, temp, voc, counts_10s * 6, counts_10s),
        )
        for metric, value in (
            ("co2", ppm),
            ("temp_c", temp),
            ("voc", voc),
            ("cpm", counts_10s * 6),
        ):
            add_sample(conn, t, "C1", metric, float(value))


def run(conn: sqlite3.Connection) -> dict[str, Any]:
    process_pending(conn, Params(), TH)
    status = _kv_get(conn, STATUS_KEY)
    assert isinstance(status, dict)
    return status


def card(status: dict[str, Any], domain: str) -> dict[str, Any]:
    return next(c for c in status["cards"] if c["domain"] == domain)


def assert_clean(status: dict[str, Any]) -> None:
    assert contracts.errors("status", status) == []
    for where, text, is_reason in status_strings(status):
        assert violations(text, reason=is_reason) == [], f"{where}: {text}"


def test_no_data_does_nothing(conn: sqlite3.Connection) -> None:
    assert process_pending(conn, Params(), TH) == 0
    assert _kv_get(conn, STATUS_KEY) is None


def test_quiet_cabin_only_is_green_and_valid(conn: sqlite3.Connection) -> None:
    for m in range(12):
        cabin_minute(conn, T0 + m * 60)
    status = run(conn)
    assert_clean(status)
    assert status["source_mode"] == "live"
    assert status["crew_status"]["level"] == "green"
    assert [c["level"] for c in status["cards"]] == ["green"] * 4
    assert status["crew"] == []


def test_co2_crosses_yellow_then_red(conn: sqlite3.Connection) -> None:
    for m in range(10):
        cabin_minute(conn, T0 + m * 60)
    for m in range(10, 20):
        cabin_minute(conn, T0 + m * 60, co2_mmhg=2.7)
    status = run(conn)
    assert_clean(status)
    air = card(status, "air")
    assert air["level"] == "yellow"
    assert air["attribution"] == "cabin"
    assert status["crew_status"]["domain"] == "air"
    assert air["numbers"][0]["value"] == 2.7
    assert air["numbers"][0]["baseline"] == 0.6  # the baseline did not learn the high readings

    for m in range(20, 30):
        cabin_minute(conn, T0 + m * 60, co2_mmhg=3.4)
    status = run(conn)
    assert_clean(status)
    assert card(status, "air")["level"] == "red"
    kinds = [r["domain"] for r in conn.execute("SELECT domain FROM events WHERE kind = 'spike'")]
    assert kinds == ["air", "air"]  # green to yellow, yellow to red
    queued = conn.execute("SELECT tier, approved FROM earth_queue").fetchall()
    assert [(q["tier"], q["approved"]) for q in queued] == [("flight_surgeon", 0)]


def test_voc_alone_makes_air_yellow(conn: sqlite3.Connection) -> None:
    for m in range(8):
        cabin_minute(conn, T0 + m * 60)
    for m in range(8, 16):
        cabin_minute(conn, T0 + m * 60, voc=300.0)
    status = run(conn)
    assert_clean(status)
    air = card(status, "air")
    assert air["level"] == "yellow"
    assert "VOC index 300" in air["reason"]


def test_cabin_outside_band_is_thermal_red(conn: sqlite3.Connection) -> None:
    for m in range(8):
        cabin_minute(conn, T0 + m * 60)
    for m in range(8, 16):
        cabin_minute(conn, T0 + m * 60, temp=24.5)
    assert card(run(conn), "thermal")["level"] == "yellow"
    for m in range(16, 24):
        cabin_minute(conn, T0 + m * 60, temp=28.0)
    status = run(conn)
    assert_clean(status)
    assert card(status, "thermal")["level"] == "red"


def test_radiation_yellow_after_one_window_red_after_two(conn: sqlite3.Connection) -> None:
    def window(index: int, counts_10s: int) -> None:
        for m in range(10):
            cabin_minute(conn, T0 + index * 600 + m * 60, counts_10s=counts_10s)

    window(0, 3)  # sets the baseline: 180 counts per window
    window(1, 3)
    window(2, 40)  # far above 180 + 4 * sqrt(180)
    cabin_minute(conn, T0 + 3 * 600, counts_10s=40)
    cabin_minute(conn, T0 + 3 * 600 + 60, counts_10s=40)
    status = run(conn)
    assert_clean(status)
    assert card(status, "radiation")["level"] == "yellow"

    for m in range(2, 10):
        cabin_minute(conn, T0 + 3 * 600 + m * 60, counts_10s=40)
    cabin_minute(conn, T0 + 4 * 600, counts_10s=3)
    cabin_minute(conn, T0 + 4 * 600 + 60, counts_10s=3)
    status = run(conn)
    assert card(status, "radiation")["level"] == "red"
    assert status["crew_status"]["domain"] == "radiation"

    for m in range(2, 10):
        cabin_minute(conn, T0 + 4 * 600 + m * 60, counts_10s=3)
    cabin_minute(conn, T0 + 5 * 600, counts_10s=3)
    cabin_minute(conn, T0 + 5 * 600 + 60, counts_10s=3)
    assert card(run(conn), "radiation")["level"] == "green"


def test_partial_hardware_messages_do_not_break_the_status(conn: sqlite3.Connection) -> None:
    """A cabin node with only the Geiger counter attached still gives a valid status."""
    for m in range(6):
        for i in range(6):
            t = T0 + m * 60 + i * 10
            conn.execute(
                "INSERT INTO raw_cabin (id, seq, t_meas, src, t_recv, c10s) "
                "VALUES ('C1',?,?,'hw',?,2)",
                (m * 6 + i, t, t),
            )
        add_sample(conn, T0 + m * 60, "C1", "cpm", 12.0)
    status = run(conn)
    assert_clean(status)
    assert card(status, "air")["numbers"][0]["value"] is None


def test_processing_twice_adds_nothing(conn: sqlite3.Connection) -> None:
    for m in range(6):
        cabin_minute(conn, T0 + m * 60)
    assert process_pending(conn, Params(), TH) == 5  # the newest minute may still be filling
    assert process_pending(conn, Params(), TH) == 0


def test_rmssd() -> None:
    assert rmssd([[1000.0, 1010.0, 1000.0]]) == (10.0, 2)
    assert rmssd([[1000.0]]) is None


def _tick(levels: dict[str, str], crew_levels: list[str], **cabin: float) -> Tick:
    tick = Tick(t=float(T0))
    tick.crew = [
        CrewView(id=f"W{i + 1}", name=f"W{i + 1}", ready=True, learning_ends_t=None, level=level)
        for i, level in enumerate(crew_levels)
    ]
    tick.cabin = CabinView(id="C1", **cabin)
    tick.levels = {"cardio": "yellow", "air": "green", "thermal": "green", "radiation": "green"}
    tick.levels.update(levels)
    return tick


def test_attribution_cabin_individual_unclear() -> None:
    p = Params()
    crew_all = ["yellow", "yellow", "yellow"]
    assert attribute(_tick({"air": "red"}, crew_all), p) == ("cabin", ["W1", "W2", "W3"])
    assert attribute(_tick({}, ["green", "yellow", "green"]), p) == ("individual", ["W2"])
    # everyone moved but the cabin did not: no call
    assert attribute(_tick({}, crew_all), p)[0] == "unclear"
    # one person moved while the cabin changed too: no call
    assert attribute(_tick({"air": "yellow"}, ["yellow", "green", "green"]), p)[0] == "unclear"
    # a CO2 rise below the yellow line still counts as the cabin changing
    risen = _tick({}, crew_all, co2=1.3, co2_0=0.6)
    assert attribute(risen, p)[0] == "cabin"
