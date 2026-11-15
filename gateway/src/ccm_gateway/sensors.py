"""Cabin sensor health, and reading values with the simulator filling in for a failed sensor.

When a cabin sensor fails, the fallback service (``fallback.py``) stores the simulator's values
for its metrics under the rollup subject ``"<node>:sim"``. Real data always wins: for each minute
and metric, :func:`minute_rows` uses the real subject when it has data and the simulated one only
to fill a hole. A dead Geiger counter keeps reporting zero counts instead of going quiet, so a
real radiation minute with zero counts is treated as missing when a simulated value exists. At the
usual ~18 counts per minute, a whole minute of zeros essentially never happens on a working tube.

A decision-support prototype, not a medical device.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

State = Literal["ok", "offline"]
DEFAULT_NODE = "C1"
SIM_SUFFIX = ":sim"


@dataclass(frozen=True)
class Sensor:
    name: str  # as shown to people
    metrics: tuple[str, ...]  # rollup metrics it produces
    probe: str  # the raw_cabin column it always fills while working
    readings: str  # what goes missing, for sentences
    domains: tuple[str, ...]  # cards it feeds


SENSORS: dict[str, Sensor] = {
    "scd41": Sensor(
        "SCD41", ("co2", "temp_c", "rh"), "co2", "CO2, temperature and humidity", ("air", "thermal")
    ),
    "sgp40": Sensor("SGP40", ("voc",), "voc", "VOC index", ("air",)),
    "pms5003": Sensor("PMS5003", ("pm1", "pm25", "pm10"), "pm25", "particulates", ("air",)),
    # cpm appears only after a full minute of counting; c10s is in every message
    "geiger": Sensor(
        "Geiger counter", ("cpm", "c10s"), "c10s", "radiation proxy counts", ("radiation",)
    ),
}
METRIC_SENSOR = {m: key for key, s in SENSORS.items() for m in s.metrics}
COUNT_METRICS = ("cpm", "c10s")  # zero means "no data" when a simulated value exists
GEIGER_SILENT_S = 120.0


def sim_subject(node: str) -> str:
    return node + SIM_SUFFIX


def cabin_node(conn: sqlite3.Connection) -> str:
    """The cabin node with the most recent data, or ``C1`` before any has reported."""
    row = conn.execute("SELECT id FROM raw_cabin ORDER BY t_meas DESC LIMIT 1").fetchone()
    return str(row["id"]) if row else DEFAULT_NODE


# --------------------------------------------------------------------------- health
def _last_real(conn: sqlite3.Connection, node: str, sensor: Sensor) -> float | None:
    """Measured time of the newest real reading from this sensor."""
    row = conn.execute(
        f"SELECT MAX(t_meas) AS t FROM raw_cabin WHERE id = ? AND {sensor.probe} IS NOT NULL",
        (node,),
    ).fetchone()
    return None if row is None or row["t"] is None else float(row["t"])


def _geiger_silent(conn: sqlite3.Connection, node: str, now: float) -> bool:
    """True when the counter has reported for the last two minutes but counted nothing."""
    row = conn.execute(
        "SELECT COUNT(c10s) AS n, COALESCE(SUM(c10s), 0) AS counts FROM raw_cabin "
        "WHERE id = ? AND t_meas > ? AND t_meas <= ?",
        (node, now - GEIGER_SILENT_S, now),
    ).fetchone()
    return int(row["n"]) >= GEIGER_SILENT_S / 10 - 2 and float(row["counts"]) == 0.0


def _state_now(conn: sqlite3.Connection, node: str, key: str, now: float, stale_s: float) -> State:
    sensor = SENSORS[key]
    last = _last_real(conn, node, sensor)
    if last is None or now - last > stale_s:
        return "offline"
    if key == "geiger" and _geiger_silent(conn, node, now):
        return "offline"
    return "ok"


def assess(
    conn: sqlite3.Connection, node: str, now: float, stale_s: float = 60.0
) -> dict[str, State]:
    """Every sensor's state now. Records changes in ``sensor_status`` and as events.

    The caller commits.
    """
    states: dict[str, State] = {}
    for key, sensor in SENSORS.items():
        state = _state_now(conn, node, key, now, stale_s)
        states[key] = state
        row = conn.execute(
            "SELECT state, since_t FROM sensor_status WHERE node = ? AND sensor = ?",
            (node, key),
        ).fetchone()
        if row is not None and row["state"] == state:
            continue
        conn.execute(
            "INSERT INTO sensor_status (node, sensor, state, since_t) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (node, sensor) DO UPDATE SET state = excluded.state, "
            "since_t = excluded.since_t",
            (node, key, state, now),
        )
        if row is None and state == "ok":
            continue  # first sight of a working sensor is not news
        if state == "offline":
            sentence = f"{sensor.name} offline; {sensor.readings} are simulated until it returns."
        else:
            minutes = max(round((now - float(row["since_t"])) / 60), 1)
            sentence = f"{sensor.name} back online after {minutes} min; real readings resumed."
        conn.execute(
            "INSERT INTO events (t, kind, domain, subject, metric, score, reason, simulated) "
            "VALUES (?, 'node', ?, ?, NULL, NULL, ?, 0)",
            (now, sensor.domains[0], node, sentence),
        )
    return states


def offline(conn: sqlite3.Connection, node: str) -> dict[str, float]:
    """Offline sensors and since when, as last recorded by :func:`assess`."""
    rows = conn.execute(
        "SELECT sensor, since_t FROM sensor_status WHERE node = ? AND state = 'offline'",
        (node,),
    )
    return {str(r["sensor"]): float(r["since_t"]) for r in rows}


# --------------------------------------------------------------------------- reading
@dataclass(frozen=True)
class Minute:
    t_min: int
    total: float
    min: float
    max: float
    n: int
    simulated: bool


def _usable(metric: str, total: float, n: int) -> bool:
    if n <= 0:
        return False
    return not (metric in COUNT_METRICS and total == 0.0)


def minute_rows(
    conn: sqlite3.Connection, node: str, metric: str, t_from: float, t_to: float
) -> list[Minute]:
    """One row per minute in ``t_from <= t_min < t_to``: real data, else the simulator's."""
    rows = conn.execute(
        "SELECT t_min, subject, total, min, max, n FROM rollup_1m "
        "WHERE subject IN (?, ?) AND metric = ? AND t_min >= ? AND t_min < ? ORDER BY t_min",
        (node, sim_subject(node), metric, int(t_from), int(t_to)),
    ).fetchall()
    real: dict[int, sqlite3.Row] = {}
    sim: dict[int, sqlite3.Row] = {}
    for row in rows:
        (sim if str(row["subject"]).endswith(SIM_SUFFIX) else real)[int(row["t_min"])] = row
    out = []
    for t_min in sorted(set(real) | set(sim)):
        r = real.get(t_min)
        if r is not None and (_usable(metric, float(r["total"]), int(r["n"])) or t_min not in sim):
            chosen, simulated = r, False
        else:
            chosen, simulated = sim[t_min], True
        out.append(
            Minute(
                t_min,
                float(chosen["total"]),
                float(chosen["min"]),
                float(chosen["max"]),
                int(chosen["n"]),
                simulated,
            )
        )
    return out


def window_mean(
    conn: sqlite3.Connection, node: str, metric: str, t_from: float, t_to: float
) -> tuple[float | None, bool]:
    """Mean over the window and whether any of it was simulated."""
    minutes = minute_rows(conn, node, metric, t_from, t_to)
    total = sum(m.total for m in minutes)
    n = sum(m.n for m in minutes)
    if n == 0:
        return None, False
    return total / n, any(m.simulated for m in minutes)


def window_sum(
    conn: sqlite3.Connection, node: str, metric: str, t_from: float, t_to: float
) -> tuple[float, int, bool]:
    """Sum of samples, sample count, and whether any of it was simulated."""
    minutes = minute_rows(conn, node, metric, t_from, t_to)
    return (
        sum(m.total for m in minutes),
        sum(m.n for m in minutes),
        any(m.simulated for m in minutes),
    )
