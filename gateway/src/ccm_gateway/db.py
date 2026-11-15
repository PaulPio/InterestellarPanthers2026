"""SQLite store shared by ingest, analytics and the APIs.

Every service opens its own connection with ``connect``. WAL mode lets the API read while ingest
writes. All times are unix seconds. ``t_meas`` is the measured time and is what analysis uses;
``t_recv`` is when the gateway received the message.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_wearable (
  id TEXT NOT NULL, seq INTEGER NOT NULL, t_meas REAL NOT NULL,
  crew TEXT NOT NULL, src TEXT NOT NULL, t_node REAL, t_recv REAL NOT NULL,
  hr REAL, spo2 REAL, skin_c REAL, act REAL, motion INTEGER, ibi TEXT, batt REAL,
  PRIMARY KEY (id, seq, t_meas)
);
CREATE INDEX IF NOT EXISTS raw_wearable_t ON raw_wearable (t_meas);

CREATE TABLE IF NOT EXISTS raw_cabin (
  id TEXT NOT NULL, seq INTEGER NOT NULL, t_meas REAL NOT NULL,
  src TEXT NOT NULL, t_node REAL, t_recv REAL NOT NULL,
  co2 REAL, temp_c REAL, rh REAL, voc REAL, pm1 REAL, pm25 REAL, pm10 REAL,
  cpm REAL, c10s REAL,
  PRIMARY KEY (id, seq, t_meas)
);
CREATE INDEX IF NOT EXISTS raw_cabin_t ON raw_cabin (t_meas);

-- One row per minute, subject and metric. subject is a crew id (W1) or a cabin node id (C1).
-- total is the sum of the samples, so mean = total / n and late samples can be folded in.
CREATE TABLE IF NOT EXISTS rollup_1m (
  t_min INTEGER NOT NULL, subject TEXT NOT NULL, metric TEXT NOT NULL,
  total REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (t_min, subject, metric)
);

CREATE TABLE IF NOT EXISTS node_status (
  node TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, src TEXT,
  t REAL NOT NULL, last_seen_t REAL,
  PRIMARY KEY (node, id)
);

-- Health of each cabin sensor, kept by the fallback service (sensors.assess). While a sensor is
-- offline the simulator's values for it are stored under the rollup subject '<node>:sim'.
CREATE TABLE IF NOT EXISTS sensor_status (
  node TEXT NOT NULL, sensor TEXT NOT NULL, state TEXT NOT NULL, since_t REAL NOT NULL,
  PRIMARY KEY (node, sensor)
);

CREATE TABLE IF NOT EXISTS crew (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, enrolled_t REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS baseline (
  subject TEXT NOT NULL, metric TEXT NOT NULL,
  ewm_mean REAL NOT NULL, ewm_var REAL NOT NULL, n INTEGER NOT NULL,
  ref_mean REAL, ref_var REAL, updated REAL NOT NULL,
  PRIMARY KEY (subject, metric)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  t REAL NOT NULL, kind TEXT NOT NULL, domain TEXT, subject TEXT, metric TEXT,
  score REAL, reason TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_t ON events (t);

-- The four cards per minute. subject is a crew id, or 'cabin' for the shared cards.
CREATE TABLE IF NOT EXISTS risk (
  t_min INTEGER NOT NULL, subject TEXT NOT NULL, domain TEXT NOT NULL,
  level TEXT NOT NULL, reason TEXT NOT NULL, action TEXT, attribution TEXT, detail TEXT,
  PRIMARY KEY (t_min, subject, domain)
);

CREATE TABLE IF NOT EXISTS earth_queue (
  id TEXT PRIMARY KEY, t REAL NOT NULL, tier TEXT NOT NULL, summary TEXT NOT NULL,
  payload TEXT NOT NULL, bytes INTEGER NOT NULL,
  approved INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the store, creating the file and the tables if needed."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO kv (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def add_sample(
    conn: sqlite3.Connection, t_meas: float, subject: str, metric: str, value: float
) -> None:
    """Fold one sample into its minute's rollup. The caller commits."""
    t_min = int(t_meas // 60) * 60
    conn.execute(
        """
        INSERT INTO rollup_1m (t_min, subject, metric, total, min, max, n)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT (t_min, subject, metric) DO UPDATE SET
          total = total + excluded.total,
          min = MIN(min, excluded.min),
          max = MAX(max, excluded.max),
          n = n + 1
        """,
        (t_min, subject, metric, value, value, value),
    )
