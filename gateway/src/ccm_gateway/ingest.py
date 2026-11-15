"""MQTT ingest: one SQLite writer, deduplicated raw samples and minute rollups."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from ccm_gateway.adapter import (
    CabinTelemetry,
    Node,
    NodeStatus,
    Normalized,
    SimEvent,
    normalize,
)
from ccm_gateway.config import Settings
from ccm_gateway.db import add_sample, connect

LOG = logging.getLogger(__name__)
CABIN_ROLLUPS = ("co2", "temp_c", "rh", "voc", "pm25", "cpm")
WEARABLE_ROLLUPS = ("hr", "spo2", "skin_c", "act")


def _status(conn: sqlite3.Connection, record: NodeStatus) -> None:
    conn.execute(
        """INSERT INTO node_status (node, id, state, src, t, last_seen_t)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (node, id) DO UPDATE SET
          state = excluded.state, t = excluded.t,
          src = COALESCE(excluded.src, node_status.src),
          last_seen_t = MAX(COALESCE(node_status.last_seen_t, 0), excluded.last_seen_t)
        """,
        (record.node, record.id, record.state, record.src, record.t, record.t_recv),
    )


def _store(conn: sqlite3.Connection, record: Normalized) -> bool:
    rollups: tuple[str, ...]
    node: Node
    if isinstance(record, NodeStatus):
        _status(conn, record)
        return True
    if isinstance(record, SimEvent):
        conn.execute(
            "INSERT INTO events (t, kind, subject, reason, simulated) VALUES (?, 'sim', ?, ?, 1)",
            (record.t_meas, record.crew, record.label),
        )
        return True
    if isinstance(record, CabinTelemetry):
        metrics = ("co2", "temp_c", "rh", "voc", "pm1", "pm25", "pm10", "cpm", "c10s")
        cursor = conn.execute(
            """INSERT OR IGNORE INTO raw_cabin
            (id, seq, t_meas, src, t_node, t_recv, co2, temp_c, rh, voc, pm1, pm25, pm10, cpm, c10s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.seq,
                record.t_meas,
                record.src,
                record.t_node,
                record.t_recv,
                *(getattr(record, metric) for metric in metrics),
            ),
        )
        subject, rollups, node = record.id, CABIN_ROLLUPS, "cabin"
    else:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO raw_wearable
            (id, seq, t_meas, crew, src, t_node, t_recv, hr, spo2, skin_c, act, motion, ibi, batt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.seq,
                record.t_meas,
                record.crew,
                record.src,
                record.t_node,
                record.t_recv,
                record.hr,
                record.spo2,
                record.skin_c,
                record.act,
                int(record.motion),
                json.dumps(record.ibi),
                record.batt,
            ),
        )
        subject, rollups, node = record.crew, WEARABLE_ROLLUPS, "wearable"
    inserted = cursor.rowcount == 1
    if inserted:
        for metric in rollups:
            value = getattr(record, metric)
            if metric == "skin_c" and not getattr(record, "on_skin", True):
                continue  # no finger: the board's temperature, not the skin's
            if value is not None:
                add_sample(conn, record.t_meas, subject, metric, value)
    _status(conn, NodeStatus(node, record.id, "online", record.t_recv, record.t_recv, record.src))
    return inserted


def store(conn: sqlite3.Connection, record: Normalized) -> bool:
    """Store one record atomically. Return False for duplicate telemetry; caller commits."""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT ingest_record")
    try:
        inserted = _store(conn, record)
    except Exception:
        conn.execute("ROLLBACK TO ingest_record")
        conn.execute("RELEASE ingest_record")
        raise
    conn.execute("RELEASE ingest_record")
    return inserted


def prune(conn: sqlite3.Connection, cutoff: float) -> int:
    """Remove expired raw samples by measurement time; retain all rollups and events."""
    return sum(
        conn.execute(f"DELETE FROM {table} WHERE t_meas < ?", (cutoff,)).rowcount
        for table in ("raw_cabin", "raw_wearable")
    )


def run(settings: Settings, *, stop_event: threading.Event | None = None) -> None:
    """Reconnect indefinitely; commit every second and prune raw data hourly.

    Paho's background thread only queues packets. All database operations happen in this thread.
    ``stop_event`` permits a graceful service/test shutdown, including the final pending batch.
    """
    if settings.raw_retention_days < 0:
        raise ValueError("raw_retention_days must be nonnegative")
    stop = stop_event if stop_event is not None else threading.Event()
    packets: queue.Queue[tuple[str, bytes, float]] = queue.Queue(maxsize=10000)
    client = mqtt.Client(CallbackAPIVersion.VERSION2)

    def on_connect(
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            LOG.warning("MQTT connection refused: %s", reason_code)
            return
        result, _ = client.subscribe("ccm/#", qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOG.error("MQTT subscription failed: %s", result)

    def on_message(client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage) -> None:
        try:
            packets.put_nowait((message.topic, bytes(message.payload), time.time()))
        except queue.Full:
            LOG.error("Ingest queue full; dropping packet on %s", message.topic)

    def consume(packet: tuple[str, bytes, float]) -> None:
        record = normalize(*packet)
        if record is None:
            return
        try:
            store(conn, record)
        except sqlite3.Error as exc:  # one bad write must not stop the service
            LOG.error("Could not store message on %s: %s", packet[0], exc)

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.enable_logger(LOG)
    conn = connect(settings.db_path)
    try:
        client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        client.loop_start()
        next_commit, next_prune = time.monotonic() + 1, time.monotonic()
        while not stop.is_set():
            try:
                consume(packets.get(timeout=max(0.01, min(next_commit - time.monotonic(), 1))))
            except queue.Empty:
                pass
            now = time.monotonic()
            if now >= next_prune:
                prune(conn, time.time() - settings.raw_retention_days * 86400)
                next_prune = now + 3600
            if now >= next_commit:
                conn.commit()
                next_commit = now + 1
    except KeyboardInterrupt:
        LOG.info("Stopping MQTT ingest")
    finally:
        try:
            client.disconnect()
            client.loop_stop()
            while True:
                try:
                    consume(packets.get_nowait())
                except queue.Empty:
                    break
            conn.commit()
        finally:
            conn.close()
