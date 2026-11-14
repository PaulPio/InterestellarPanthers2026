"""The gateway API matches the dashboard contract on an empty store and a seeded one."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from ccm_sim.contracts import validate
from ccm_sim.thresholds import load_thresholds
from ccm_sim.units import ppm_to_mmhg
from fastapi.testclient import TestClient

from ccm_gateway.api import learning_status
from ccm_gateway.config import Settings
from ccm_gateway.db import add_sample, connect

SCHEMAS = {
    "/api/status": "status",
    "/api/history": "history",
    "/api/events": "events",
    "/api/earth-queue": "earth_queue",
    "/api/settings": "settings",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "ccm.sqlite3",
        mqtt_host="127.0.0.1",
        mqtt_port=1883,
        api_host="127.0.0.1",
        api_port=8000,
        dashboard_dist=tmp_path / "dist",
        raw_retention_days=7,
        uplink_url=None,
        uplink_key=None,
        uplink_period_s=10.0,
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    store = connect(":memory:")
    yield store
    store.close()


@pytest.fixture
def client(conn: sqlite3.Connection, tmp_path: Path) -> TestClient:
    from ccm_gateway.api import create_app

    return TestClient(create_app(conn, _settings(tmp_path)))


def _enroll(conn: sqlite3.Connection, crew_id: str, name: str, t: float) -> None:
    conn.execute("INSERT INTO crew (id, name, enrolled_t) VALUES (?, ?, ?)", (crew_id, name, t))


def _node(
    conn: sqlite3.Connection,
    node: str,
    node_id: str,
    state: str,
    src: str,
    t: float,
) -> None:
    conn.execute(
        "INSERT INTO node_status (node, id, state, src, t, last_seen_t) VALUES (?, ?, ?, ?, ?, ?)",
        (node, node_id, state, src, t, t),
    )


def test_empty_store_routes_validate(client: TestClient) -> None:
    for path, kind in SCHEMAS.items():
        params = {"metric": "co2_mmhg"} if path == "/api/history" else None
        response = client.get(path, params=params)
        assert response.status_code == 200, path
        body = response.json()
        validate(kind, body)
        if path == "/api/status":
            assert body["source_mode"] == "live"
            assert body["crew"] == []
            assert [card["level"] for card in body["cards"]] == ["green"] * 4


def test_learning_status_uses_crew_and_nodes(conn: sqlite3.Connection) -> None:
    _enroll(conn, "W1", "Ada", 1_000.0)
    _node(conn, "wearable", "W1", "online", "hw", 5_000.0)
    _node(conn, "cabin", "C1", "offline", "sim", 5_000.0)
    body = learning_status(conn)
    validate("status", body)
    assert body["t"] == 5_000.0
    assert body["crew"] == [
        {
            "id": "W1",
            "name": "Ada",
            "src": "hw",
            "online": True,
            "level": "green",
            "baseline": "learning",
            "learning_ends_t": 5_000.0 + 7200.0,
            "last_seen_t": 5_000.0,
        }
    ]
    assert body["nodes"][0]["state"] == "offline"
    assert body["checkin_due"] is True


def test_stored_status_refreshes_live_fields(client: TestClient, conn: sqlite3.Connection) -> None:
    _enroll(conn, "W1", "Ada", 1_000.0)
    _node(conn, "wearable", "W1", "offline", "sim", 1_000.0)
    stored = learning_status(conn)
    stored["cards"][0]["reason"] = "Stored by analytics."
    stored["source_mode"] = "mock"
    stored["crew"][0]["online"] = True
    conn.execute("DELETE FROM node_status")
    _node(conn, "wearable", "W1", "online", "hw", 9_000.0)
    conn.execute(
        "INSERT INTO kv (key, value) VALUES ('status', ?)",
        (json.dumps(stored),),
    )
    conn.commit()
    body = client.get("/api/status").json()
    validate("status", body)
    assert body["source_mode"] == "live"
    assert body["t"] == 9_000.0
    assert body["cards"][0]["reason"] == "Stored by analytics."
    assert body["crew"][0]["online"] is True
    assert body["crew"][0]["src"] == "hw"
    assert body["crew"][0]["last_seen_t"] == 9_000.0


def test_co2_history_converts_ppm_rollups(client: TestClient, conn: sqlite3.Connection) -> None:
    add_sample(conn, 1_000.0, "C1", "co2", 800.0)
    add_sample(conn, 1_060.0, "C1", "co2", 1_200.0)
    conn.execute(
        "INSERT INTO baseline (subject, metric, ewm_mean, ewm_var, n, updated) "
        "VALUES ('C1', 'co2_mmhg', 0.5, 0.01, 10, 1000.0)"
    )
    conn.commit()
    pressure = load_thresholds().cabin_pressure_mmhg
    body = client.get(
        "/api/history", params={"metric": "co2_mmhg", "hours": 6, "step": 3600}
    ).json()
    validate("history", body)
    assert body["crew"] is None
    assert body["unit"] == "mmHg"
    assert len(body["points"]) == 1
    assert body["points"][0]["mean"] == pytest.approx(ppm_to_mmhg(1_000.0, pressure))
    assert body["points"][0]["min"] == pytest.approx(ppm_to_mmhg(800.0, pressure))
    assert body["points"][0]["max"] == pytest.approx(ppm_to_mmhg(1_200.0, pressure))
    assert body["baseline"][0]["mean"] == pytest.approx(0.5)
    assert body["baseline"][0]["lo"] == pytest.approx(0.5 - 0.2)
    assert body["cusum"] is None
    assert body["limits"] == {"yellow": 2.5, "red": 3.0}


def test_history_unknown_crew_is_404(client: TestClient, conn: sqlite3.Connection) -> None:
    _enroll(conn, "W1", "Ada", 1.0)
    conn.commit()
    missing = client.get("/api/history", params={"metric": "hr", "crew": "W9"})
    assert missing.status_code == 404
    omitted = client.get("/api/history", params={"metric": "hr"})
    assert omitted.status_code == 422


def test_events_are_oldest_first_and_checkin_does_not_change_a_level(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _enroll(conn, "W1", "Ada", 1_000.0)
    _node(conn, "wearable", "W1", "online", "hw", 2_000.0)
    conn.execute(
        "INSERT INTO events (t, kind, subject, metric, score, reason, simulated) "
        "VALUES (1500.0, 'spike', 'W1', 'prv', 2.5, 'Pulse-rate variability moved.', 0)"
    )
    stored = learning_status(conn)
    stored["cards"][0]["level"] = "yellow"
    stored["cards"][0]["reason"] = "Stored by analytics."
    conn.execute("INSERT INTO kv (key, value) VALUES ('status', ?)", (json.dumps(stored),))
    conn.commit()

    created = client.post("/api/checkin", json={"crew": "W1", "mood": 4, "sleep": 3, "note": None})
    assert created.status_code == 200
    validate("checkin_response", created.json())
    body = client.get("/api/events").json()
    validate("events", body)
    assert [event["kind"] for event in body["events"]] == ["spike", "checkin"]
    assert body["events"][0]["domain"] == "cardio"
    assert body["events"][1]["domain"] is None
    assert body["events"][1]["id"] == created.json()["event_id"]
    assert "mood 4 of 5" in body["events"][1]["sentence"]
    conn.execute(
        "INSERT INTO events (t, kind, domain, subject, metric, score, reason, simulated) "
        "VALUES (2100.0, 'sim', 'air', NULL, 'co2', NULL, 'Simulated: CO2 passes 2.5 mmHg.', 1)"
    )
    conn.commit()
    kinds = [event["kind"] for event in client.get("/api/events").json()["events"]]
    assert kinds[-1] == "sim_event"
    again = client.get("/api/status").json()
    assert again["cards"][0]["level"] == "yellow"
    assert again["cards"][0]["reason"] == "Stored by analytics."


def test_earth_queue_approve_is_idempotent(client: TestClient, conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO earth_queue (id, t, tier, summary, payload, bytes, approved, sent) "
        "VALUES ('q-1', 1000.0, 'urgent', 'Cabin CO2 is high.', '{\"domain\":\"air\"}', 18, 0, 0)"
    )
    conn.commit()
    first = client.post("/api/earth-queue/q-1/approve")
    second = client.post("/api/earth-queue/q-1/approve")
    assert first.status_code == 200
    assert first.json() == second.json()
    validate("earth_queue_item", first.json())
    assert first.json()["approved"] is True
    assert first.json()["sent"] is False
    missing = client.post("/api/earth-queue/missing/approve")
    assert missing.status_code == 404
    listing = client.get("/api/earth-queue").json()
    validate("earth_queue", listing)
    assert listing["items"][0]["approved"] is True
    conn.execute(
        "INSERT INTO kv (key, value) VALUES ('status', ?)",
        (json.dumps({"t": 1000}),),
    )
    conn.commit()
    window = client.get("/api/earth-queue").json()
    assert window["next_window_t"] == 1800.0


def test_settings_enroll_update_and_reset(client: TestClient, conn: sqlite3.Connection) -> None:
    empty = client.get("/api/settings").json()
    validate("settings", empty)
    assert empty["auto_approve"]["crew_only"] is True

    enrolled = client.post("/api/settings/enroll", json={"id": "W4", "name": "Bea"})
    assert enrolled.status_code == 200
    validate("settings", enrolled.json())
    assert enrolled.json()["crew"][0]["id"] == "W4"
    renamed = client.post("/api/settings/enroll", json={"id": "W4", "name": "Beatrice"})
    assert renamed.json()["crew"][0]["name"] == "Beatrice"
    assert renamed.json()["crew"][0]["enrolled_t"] == enrolled.json()["crew"][0]["enrolled_t"]

    updated = client.put(
        "/api/settings",
        json={"crew_only": False, "flight_surgeon": True, "urgent": False},
    )
    validate("settings", updated.json())
    assert updated.json()["auto_approve"]["flight_surgeon"] is True

    conn.execute(
        "INSERT INTO baseline (subject, metric, ewm_mean, ewm_var, n, updated) "
        "VALUES ('W4', 'hr', 60.0, 1.0, 3, 1.0), ('W4', 'prv', 40.0, 1.0, 3, 1.0)"
    )
    conn.commit()
    one = client.post("/api/settings/reset-baseline", json={"crew": "W4", "metric": "hr"})
    assert one.status_code == 200
    left = conn.execute("SELECT metric FROM baseline WHERE subject = 'W4'").fetchall()
    assert [row["metric"] for row in left] == ["prv"]
    client.post("/api/settings/reset-baseline", json={"crew": "W4", "metric": None})
    assert conn.execute("SELECT COUNT(*) AS n FROM baseline").fetchone()["n"] == 0
    assert (
        client.post("/api/settings/reset-baseline", json={"crew": "W9", "metric": None}).status_code
        == 404
    )


def test_bad_bodies_are_422_and_unknown_api_paths_are_json_404(client: TestClient) -> None:
    mood = client.post("/api/checkin", json={"crew": "W1", "mood": 0, "sleep": 3, "note": None})
    extra = client.post(
        "/api/checkin", json={"crew": "W1", "mood": 3, "sleep": 3, "note": None, "extra": 1}
    )
    assert mood.status_code == 422
    assert extra.status_code == 422
    missing = client.get("/api/not-a-route")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")
    assert isinstance(missing.json(), dict)
