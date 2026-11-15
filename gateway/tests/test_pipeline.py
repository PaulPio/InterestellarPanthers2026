"""End to end without a broker: simulator messages -> adapter -> ingest -> analytics -> API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from ccm_sim import contracts
from ccm_sim.engine import build_world, run_world
from ccm_sim.mockapi.copyrules import status_strings, violations
from ccm_sim.scenario import load_scenario
from ccm_sim.sinks import MemorySink
from ccm_sim.timeutil import DEFAULT_START
from fastapi.testclient import TestClient

from ccm_gateway.adapter import normalize
from ccm_gateway.analytics import process_pending
from ccm_gateway.api import create_app
from ccm_gateway.config import load_settings
from ccm_gateway.db import connect
from ccm_gateway.ingest import store

FIXTURES = Path(__file__).parent / "fixtures"


def feed(conn: sqlite3.Connection, topic: str, payload: bytes, t_recv: float) -> None:
    record = normalize(topic, payload, t_recv)
    if record is not None:
        store(conn, record)


def play(conn: sqlite3.Connection, scenario: str) -> None:
    world = build_world(load_scenario(scenario), 1, DEFAULT_START)
    sink = MemorySink()
    run_world(world, sink, "max")
    for r in sink.records:
        t = float(r.payload.get("t_meas", r.payload.get("t", DEFAULT_START)))
        feed(conn, r.topic, json.dumps(r.payload).encode(), t + 0.5)
    conn.commit()


@pytest.fixture(scope="module")
def scrubber(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path = tmp_path_factory.mktemp("pipeline") / "ccm.sqlite3"
    conn = connect(path)
    play(conn, "scrubber_failure")
    assert process_pending(conn) > 200
    settings = load_settings()
    app = create_app(conn, settings)
    return TestClient(app)


def get(client: TestClient, url: str, kind: str) -> dict[str, Any]:
    response = client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert contracts.errors(kind, body) == [], url
    return body


def test_status_at_the_end_of_a_scrubber_failure(scrubber: TestClient) -> None:
    status = get(scrubber, "/api/status", "status")
    for where, text, is_reason in status_strings(status):
        assert violations(text, reason=is_reason) == [], f"{where}: {text}"
    cards = {c["domain"]: c for c in status["cards"]}
    assert status["source_mode"] == "live"
    assert cards["air"]["level"] == "red"
    assert cards["air"]["attribution"] == "cabin"
    assert status["crew_status"]["domain"] == "air"
    assert cards["radiation"]["level"] == "green"
    assert [c["id"] for c in status["crew"]] == ["W1", "W2", "W3"]
    assert all(c["src"] == "sim" for c in status["crew"])


def test_history_events_and_queue_come_from_the_same_run(scrubber: TestClient) -> None:
    co2 = get(scrubber, "/api/history?metric=co2_mmhg&hours=6", "history")
    assert co2["unit"] == "mmHg" and len(co2["points"]) > 100
    assert max(p["max"] for p in co2["points"]) > 3.0  # past the red line, in mmHg not ppm

    hr = get(scrubber, "/api/history?metric=hr&crew=W2&hours=6", "history")
    assert 50 < hr["points"][0]["mean"] < 90

    events = get(scrubber, "/api/events?limit=1000", "events")["events"]
    kinds = {(e["kind"], e["domain"]) for e in events}
    assert any(e["kind"] == "sim_event" for e in events)
    assert ("spike", "air") in kinds

    queue = get(scrubber, "/api/earth-queue", "earth_queue")
    assert any(i["tier"] == "flight_surgeon" for i in queue["items"])


def test_real_firmware_messages_reach_the_dashboard(tmp_path: Path) -> None:
    conn = connect(tmp_path / "hw.sqlite3")
    lines = (FIXTURES / "firmware_cabin.jsonl").read_text(encoding="utf-8").splitlines()
    t0 = 1_794_690_000.0
    feed(conn, "ccm/cabin/status", b"online", t0)
    # Repeat the fixture's sensor values every 10 s for 6 minutes, as the node would.
    sample = json.loads(lines[0])
    for i in range(36):
        msg = dict(sample, seq=1000 + i, t_node=t0 + i * 10)
        feed(conn, "ccm/cabin/telemetry", json.dumps(msg).encode(), t0 + i * 10 + 0.2)
    feed(conn, "ccm/cabin/info", b'{"id":"C1","fw":"0.3.0"}', t0 + 1)
    conn.commit()
    process_pending(conn)
    client = TestClient(create_app(conn, load_settings()))

    status = get(client, "/api/status", "status")
    assert status["nodes"] == [
        {"node": "cabin", "id": "C1", "state": "online", "last_seen_t": t0 + 350.2}
    ]
    air = next(c for c in status["cards"] if c["domain"] == "air")
    assert air["level"] == "green"
    assert air["numbers"][0]["value"] == pytest.approx(0.6, abs=0.05)  # 812 ppm in mmHg
    co2 = get(client, "/api/history?metric=co2_mmhg&hours=1", "history")
    assert co2["points"] and co2["points"][0]["mean"] == pytest.approx(0.617, abs=0.01)
