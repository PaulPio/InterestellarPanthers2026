from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from amqtt.broker import Broker
from ccm_sim.engine import build_world, run_world
from ccm_sim.scenario import load_scenario
from ccm_sim.sinks import MqttSink, Record
from ccm_sim.timeutil import DEFAULT_START

from ccm_gateway.adapter import CabinTelemetry, NodeStatus, normalize
from ccm_gateway.config import load_settings
from ccm_gateway.db import connect
from ccm_gateway.ingest import prune, run, store

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts/examples/mqtt/valid"
T0 = DEFAULT_START


def cabin(**changes: Any) -> CabinTelemetry:
    data = {
        "node": "cabin",
        "id": "C1",
        "seq": 1,
        "t_node": T0 + 125,
        "co2": 800,
        "temp_c": 22.1,
        "rh": 41,
        **changes,
    }
    record = normalize("ccm/cabin/telemetry", json.dumps(data).encode(), T0 + 1000)
    assert isinstance(record, CabinTelemetry)
    return record


def test_duplicate_raw_rows_do_not_change_rollups(conn: sqlite3.Connection) -> None:
    record = cabin()
    assert store(conn, record)
    assert not store(conn, replace(record, t_recv=record.t_recv + 5))
    store(conn, cabin(seq=2, t_node=T0 + 140, co2=1000))
    assert conn.execute("SELECT COUNT(*) FROM raw_cabin").fetchone()[0] == 2
    row = conn.execute("SELECT * FROM rollup_1m WHERE metric='co2'").fetchone()
    assert (row["subject"], row["t_min"], row["total"], row["min"], row["max"], row["n"]) == (
        "C1",
        T0 + 120,
        1800,
        800,
        1000,
        2,
    )
    last_seen = conn.execute("SELECT last_seen_t FROM node_status").fetchone()[0]
    assert last_seen == record.t_recv + 5


def test_late_samples_use_measured_minute_and_skip_missing_sensors(
    conn: sqlite3.Connection,
) -> None:
    store(conn, cabin(t_node=T0 + 245))
    store(conn, cabin(seq=2, t_node=T0 + 125, co2=None, rh=None))
    rows = conn.execute("SELECT * FROM rollup_1m WHERE metric='temp_c' ORDER BY t_min").fetchall()
    assert [(r["t_min"], r["n"]) for r in rows] == [(T0 + 120, 1), (T0 + 240, 1)]
    assert conn.execute("SELECT COUNT(*) FROM rollup_1m WHERE metric='co2'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rollup_1m WHERE metric='cpm'").fetchone()[0] == 0


def test_wearable_uses_crew_subject_and_json_ibi(conn: sqlite3.Connection) -> None:
    data = json.loads((EXAMPLES / "wearable_telemetry.normal.json").read_bytes())
    data.update(crew="W2", t_meas=T0 + 125)
    record = normalize("ccm/wearable/telemetry", json.dumps(data).encode(), T0 + 300)
    assert record is not None
    assert store(conn, record) and not store(conn, record)
    row = conn.execute("SELECT * FROM raw_wearable").fetchone()
    assert row["id"] == "W1" and row["crew"] == "W2"
    assert json.loads(row["ibi"]) == data["ibi"]
    rows = conn.execute("SELECT subject, metric, n FROM rollup_1m").fetchall()
    assert {(r["subject"], r["metric"], r["n"]) for r in rows} == {
        ("W2", metric, 1) for metric in ("hr", "spo2", "skin_c", "act")
    }
    status = conn.execute("SELECT * FROM node_status").fetchone()
    assert status["node"] == "wearable" and status["src"] == data["src"]


def test_no_finger_skips_only_missing_vitals(conn: sqlite3.Connection) -> None:
    payload = (EXAMPLES / "wearable_telemetry.no_finger.json").read_bytes()
    record = normalize("ccm/wearable/telemetry", payload, T0 + 100)
    assert record is not None
    store(conn, record)
    assert {r[0] for r in conn.execute("SELECT metric FROM rollup_1m")} == {"skin_c", "act"}
    assert conn.execute("SELECT hr, spo2 FROM raw_wearable").fetchone()[:] == (None, None)


def test_json_and_plain_status_upsert_and_keep_known_source(conn: sqlite3.Connection) -> None:
    record = normalize("ccm/cabin/status", b"online", T0)
    assert record is not None
    store(conn, record)
    payload = json.dumps({"node": "cabin", "id": "C1", "state": "offline", "t": T0 + 5})
    record = normalize("ccm/cabin/status/C1", payload.encode(), T0 + 10)
    assert record is not None
    store(conn, record)
    rows = conn.execute("SELECT * FROM node_status").fetchall()
    assert len(rows) == 1
    assert (rows[0]["state"], rows[0]["src"], rows[0]["t"], rows[0]["last_seen_t"]) == (
        "offline",
        "hw",
        T0 + 5,
        T0 + 10,
    )
    store(conn, cabin())
    assert conn.execute("SELECT state FROM node_status").fetchone()[0] == "online"


def test_sim_event_stores_label_subject_and_simulated_flag(conn: sqlite3.Connection) -> None:
    payload = (EXAMPLES / "sim_event.start_crew.json").read_bytes()
    record = normalize("ccm/sim/event", payload, T0 + 100)
    assert record is not None
    store(conn, record)
    event = conn.execute("SELECT * FROM events").fetchone()
    data = json.loads(payload)
    assert (event["kind"], event["reason"], event["subject"], event["t"], event["simulated"]) == (
        "sim",
        data["label"],
        data["crew"],
        data["t_meas"],
        1,
    )


def test_retention_prunes_raw_only_and_keeps_boundary(conn: sqlite3.Connection) -> None:
    store(conn, cabin(t_node=T0 + 10))
    store(conn, cabin(seq=2, t_node=T0 + 60))
    assert prune(conn, T0 + 60) == 1
    assert conn.execute("SELECT t_meas FROM raw_cabin").fetchone()[0] == T0 + 60
    assert conn.execute("SELECT SUM(n) FROM rollup_1m WHERE metric='co2'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM node_status").fetchone()[0] == 1


def test_store_leaves_commit_to_caller(conn: sqlite3.Connection) -> None:
    store(conn, cabin())
    assert conn.in_transaction
    conn.rollback()
    for table in ("raw_cabin", "rollup_1m", "node_status"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_failed_rollup_rolls_back_its_raw_row(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TRIGGER fail_rollup BEFORE INSERT ON rollup_1m "
        "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        store(conn, cabin())
    assert conn.execute("SELECT COUNT(*) FROM raw_cabin").fetchone()[0] == 0


@pytest.fixture
def broker() -> Iterator[tuple[str, int]]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ready: list[BaseException | bool] = []
    stop = threading.Event()

    async def serve() -> None:
        server = Broker(
            {
                "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                "plugins": {
                    "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}
                },
            }
        )
        try:
            await server.start()
            ready.append(True)
            while not stop.is_set():
                await asyncio.sleep(0.05)
        finally:
            await server.shutdown()

    def serve_thread() -> None:
        try:
            asyncio.run(serve())
        except BaseException as exc:
            ready.append(exc)

    thread = threading.Thread(target=serve_thread, daemon=True)
    thread.start()
    try:
        wait_for(lambda: bool(ready))
        assert ready == [True], ready
        yield "127.0.0.1", port
    finally:
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()


def wait_for(condition: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    pytest.fail("Timed out waiting for MQTT ingest")


def test_scenario_through_broker_to_sqlite(broker: tuple[str, int], tmp_path: Path) -> None:
    settings = replace(
        load_settings(),
        db_path=tmp_path / "ingest.sqlite3",
        mqtt_host=broker[0],
        mqtt_port=broker[1],
    )
    stop = threading.Event()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            run(settings, stop_event=stop)
        except Exception as exc:
            errors.append(exc)

    observer = connect(settings.db_path)  # create the schema before the ingest thread opens it
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    sink = MqttSink(*broker)
    try:
        # Repeated status probes wait for SUBACK without relying on a fixed startup sleep.
        probe = NodeStatus("cabin", "C1", "online", T0, T0)

        def subscribed() -> bool:
            sink.publish(
                Record(
                    "ccm/cabin/status/C1",
                    {"node": probe.node, "id": probe.id, "state": probe.state, "t": T0},
                    qos=1,
                    node="C1",
                )
            )
            return observer.execute("SELECT COUNT(*) FROM node_status").fetchone()[0] > 0

        wait_for(subscribed)
        world = build_world(load_scenario("exercise_block"), 1, T0, ["W1"], duration=60)
        run_world(world, sink)
        # QoS 0 telemetry still in flight is lost if the publisher disconnects now, so wait first.
        wait_for(lambda: observer.execute("SELECT COUNT(*) FROM raw_cabin").fetchone()[0] == 6)
        wait_for(lambda: observer.execute("SELECT COUNT(*) FROM raw_wearable").fetchone()[0] == 12)
        assert (
            observer.execute(
                "SELECT SUM(n) FROM rollup_1m WHERE subject='C1' AND metric='co2'"
            ).fetchone()[0]
            == 6
        )
        assert (
            observer.execute(
                "SELECT SUM(n) FROM rollup_1m WHERE subject='W1' AND metric='hr'"
            ).fetchone()[0]
            == 12
        )
        assert not errors
    finally:
        sink.close()
        stop.set()
        thread.join(timeout=10)
        observer.close()
    assert not thread.is_alive() and not errors
