"""The wearable firmware's messages (firmware/ccm_wearable), from MQTT to the dashboard API."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from ccm_sim import contracts
from fastapi.testclient import TestClient

from ccm_gateway.adapter import NodeStatus, WearableTelemetry, normalize
from ccm_gateway.analytics import Params, process_pending
from ccm_gateway.api import create_app
from ccm_gateway.config import load_settings
from ccm_gateway.ingest import store

FIXTURES = Path(__file__).parent / "fixtures"
TOPIC = "ccm/wearable/telemetry"
T0 = 1_794_715_200  # a whole minute


def lines() -> list[dict[str, Any]]:
    text = (FIXTURES / "firmware_wearable.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def norm(msg: dict[str, Any], t_recv: float = T0) -> WearableTelemetry:
    record = normalize(TOPIC, json.dumps(msg).encode(), t_recv)
    assert isinstance(record, WearableTelemetry), msg
    return record


def test_finger_on_message() -> None:
    r = norm(lines()[0], 1_794_715_000.4)
    assert (r.id, r.crew, r.src, r.seq) == ("W1", "W1", "hw", 12)
    assert r.t_meas == 1_794_715_000  # the node's clock is set, so its measured time is kept
    assert (r.hr, r.spo2, r.skin_c) == (72, 97, 33.41)
    assert r.act == pytest.approx(0.214 / 9.80665)  # m/s^2 to g
    assert r.ibi == (830, 845, 812, 861, 840)
    assert r.batt is None and r.motion is False
    assert r.on_skin is True


def test_finger_off_message() -> None:
    r = norm(lines()[1])
    assert (r.hr, r.spo2, r.ibi) == (None, None, ())
    assert r.skin_c == 33.38
    assert r.on_skin is False  # no finger: that is the board's temperature


def test_clock_not_set_and_a_missing_sensor() -> None:
    r = norm(lines()[2], T0 + 0.5)
    assert r.t_node == 37 and r.t_meas == T0 + 0.5  # seconds since power-on: use receive time
    assert r.skin_c is None  # TMP117 missing
    assert r.on_skin is True  # no pulse sensor to say otherwise
    assert r.motion is True and r.act == pytest.approx(5.1 / 9.80665)


def test_health_fields_are_accepted_and_ignored() -> None:
    """The hardened firmware adds rssi, up and drops; the Pi stores the reading as before."""
    r = norm(dict(lines()[0], rssi=-67, up=342, drops=1))
    assert (r.hr, r.seq) == (72, 12)
    assert norm(dict(lines()[0], rssi=None, up=5, drops=0)).hr == 72


def test_bad_values_are_dropped_or_rejected() -> None:
    msg = dict(lines()[0], ibi=[100, 830, 2500, True, 845])
    assert norm(msg).ibi == (830, 845)  # out-of-range and non-integer beat gaps dropped
    for bad in ({"id": "C1"}, {"motion": "yes"}, {"hr": 400}, {"node": "cabin"}):
        assert normalize(TOPIC, json.dumps(dict(lines()[0], **bad)).encode(), T0) is None


def test_plain_text_status() -> None:
    for state in ("online", "offline"):
        r = normalize("ccm/wearable/status", state.encode(), T0)
        assert isinstance(r, NodeStatus)
        assert (r.node, r.id, r.state, r.src) == ("wearable", "W1", state, "hw")


def wearable(
    conn: sqlite3.Connection, seq: int, t: float, finger: bool = True, skin: float = 33.4
) -> None:
    beat = 920 + (seq % 7) * 6  # a resting heart around 65 bpm with some variability
    msg: dict[str, Any] = {
        "node": "wearable",
        "id": "W1",
        "seq": seq,
        "t_node": int(t),
        "hr": 65 if finger else None,
        "spo2": 97 if finger else None,
        "skin_c": skin,
        "act": 0.15,
        "motion": False,
        "batt": None,
        "ibi": [beat, beat + 12, beat - 9, beat + 4, beat - 7] if finger else [],
    }
    if not finger:
        msg["finger"] = False
    record = normalize(TOPIC, json.dumps(msg).encode(), t + 0.3)
    assert record is not None
    store(conn, record)


def status(conn: sqlite3.Connection, learning_s: float = 900) -> dict[str, Any]:
    process_pending(conn, Params(learning_s=learning_s))
    settings = replace(load_settings(), learning_s=learning_s)
    body: dict[str, Any] = TestClient(create_app(conn, settings)).get("/api/status").json()
    assert contracts.errors("status", body) == []
    return body


def cardio(body: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in body["cards"] if c["domain"] == "cardio")


def test_the_real_wearable_reaches_the_dashboard(conn: sqlite3.Connection) -> None:
    store(conn, normalize("ccm/wearable/status", b"online", T0))  # type: ignore[arg-type]
    for i in range(12 * 20):  # 20 minutes, one message every 5 s
        wearable(conn, i, T0 + i * 5)
    conn.commit()
    body = status(conn)
    [w1] = body["crew"]
    assert (w1["id"], w1["src"], w1["online"]) == ("W1", "hw", True)  # the Hardware badge
    assert w1["baseline"] == "ready"  # 15 min of learning are over
    hr = next(n for n in cardio(body)["numbers"] if n["label"].startswith("Resting HR"))
    assert hr["value"] == 65


def test_still_learning_before_fifteen_minutes(conn: sqlite3.Connection) -> None:
    for i in range(12 * 10):  # 10 minutes
        wearable(conn, i, T0 + i * 5)
    conn.commit()
    body = status(conn)
    assert body["crew"][0]["baseline"] == "learning"
    assert cardio(body)["reason"].startswith("Learning the baseline; resting HR 65")


def test_no_finger_is_said_plainly(conn: sqlite3.Connection) -> None:
    for i in range(12 * 20):
        wearable(conn, i, T0 + i * 5)
    for i in range(12 * 20, 12 * 27):  # then 7 minutes with the finger off
        wearable(conn, i, T0 + i * 5, finger=False)
    conn.commit()
    assert (
        cardio(status(conn))["reason"] == "W1 connected; no finger on the pulse sensor for 5 min."
    )


def live_hr(body: dict[str, Any]) -> Any:
    return next(n["value"] for n in cardio(body)["numbers"] if n["label"] == "Heart rate now")


def test_heart_rate_now_is_live_and_goes_blank_when_stale(conn: sqlite3.Connection) -> None:
    for i in range(12 * 3):  # 3 minutes, steady at 65
        wearable(conn, i, T0 + i * 5)
    last = T0 + 36 * 5
    msg = {
        "node": "wearable",
        "id": "W1",
        "seq": 99,
        "t_node": int(last),
        "hr": 88,
        "spo2": 97,
        "skin_c": 33.4,
        "act": 0.15,
        "motion": False,
        "ibi": [680, 690],
        "batt": None,
    }
    store(conn, normalize(TOPIC, json.dumps(msg).encode(), last + 0.3))  # type: ignore[arg-type]
    conn.commit()
    body = status(conn)
    assert live_hr(body) == 88  # the newest message, not the 5-minute average
    rest = next(n for n in cardio(body)["numbers"] if n["label"].startswith("Resting HR"))
    assert rest["value"] != 88

    # 20 s later the cabin node still reports but the wearable is silent: no live value
    store(conn, normalize("ccm/cabin/status", b"online", last + 20.3))  # type: ignore[arg-type]
    conn.commit()
    assert live_hr(status(conn)) is None


def thermal(body: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in body["cards"] if c["domain"] == "thermal")


def test_board_heat_without_a_finger_is_not_skin(conn: sqlite3.Connection) -> None:
    # 20 min lying on the desk (the board warms to 29 C), then a finger on at 33.6 C
    for i in range(12 * 20):
        wearable(conn, i, T0 + i * 5, finger=False, skin=27.7 + i * 0.008)
    for i in range(12 * 20, 12 * 30):
        wearable(conn, i, T0 + i * 5, skin=33.6)
    conn.commit()
    first_on = T0 + 12 * 20 * 5
    rows = conn.execute(
        "SELECT MIN(t_min) FROM rollup_1m WHERE subject = 'W1' AND metric = 'skin_c'"
    ).fetchone()
    assert rows[0] >= first_on - 60  # no skin rollups while the finger was off
    stored = conn.execute("SELECT COUNT(skin_c) FROM raw_wearable").fetchone()[0]
    assert stored == 12 * 30  # the raw readings are all kept
    body = status(conn)
    assert thermal(body)["level"] == "green"


def test_one_wearer_alone_does_not_change_the_cabin_card(conn: sqlite3.Connection) -> None:
    for i in range(12 * 20):  # learn 33.4 C
        wearable(conn, i, T0 + i * 5)
    for i in range(12 * 20, 12 * 45):  # then 25 min a full 4 C warmer
        wearable(conn, i, T0 + i * 5, skin=37.4)
    conn.commit()
    assert thermal(status(conn))["level"] == "green"
