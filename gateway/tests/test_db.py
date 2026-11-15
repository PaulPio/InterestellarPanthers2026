from __future__ import annotations

import sqlite3
from pathlib import Path

from ccm_gateway.db import add_sample, connect

TABLES = {
    "raw_wearable",
    "raw_cabin",
    "rollup_1m",
    "node_status",
    "baseline",
    "events",
    "risk",
    "earth_queue",
    "crew",
    "kv",
}


def test_connect_creates_tables(conn: sqlite3.Connection) -> None:
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_connect_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "ccm.sqlite3"
    connect(path).close()
    connect(path).close()


def test_add_sample_rolls_up_by_measured_minute(conn: sqlite3.Connection) -> None:
    add_sample(conn, 120.0, "C1", "co2", 800.0)
    add_sample(conn, 179.9, "C1", "co2", 900.0)
    add_sample(conn, 180.0, "C1", "co2", 1000.0)
    rows = conn.execute("SELECT * FROM rollup_1m ORDER BY t_min").fetchall()
    assert [(r["t_min"], r["total"], r["min"], r["max"], r["n"]) for r in rows] == [
        (120, 1700.0, 800.0, 900.0, 2),
        (180, 1000.0, 1000.0, 1000.0, 1),
    ]
