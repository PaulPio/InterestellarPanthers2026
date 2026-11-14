"""Dashboard API over the gateway's SQLite store.

``create_app`` serves the contract routes under ``/api``. Analytics writes a full status body to
``kv['status']``; this module refreshes the live fields and, when that row is missing, builds an
all-green learning status with :func:`learning_status`.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from typing import Any, Literal

from ccm_sim.contracts import ContractError, validate
from ccm_sim.mockapi.static import mount_dashboard
from ccm_sim.mockapi.templates import Facts, checkin_sentence, detector, learning_sentence
from ccm_sim.thresholds import Thresholds, ThresholdsError, load_thresholds
from ccm_sim.units import DEFAULT_CABIN_PRESSURE_MMHG, ppm_to_mmhg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ccm_gateway.config import Settings

LEARNING_S = 7200.0
COMMS_WINDOW_S = 1800.0
CHECKIN_PERIOD_S = 86400.0
DOMAINS = ("cardio", "air", "thermal", "radiation")
CREW_ID = re.compile(r"^W[0-9]+$")
NODE_ID = re.compile(r"^[WC][0-9]+$")
MetricName = Literal[
    "hr", "hr_resid", "prv", "spo2", "skin_c", "act",
    "co2_mmhg", "temp_c", "rh", "voc", "pm25", "cpm",
]  # fmt: skip

# API metric: (unit, crew-scoped, rollup metric). CO2 is stored in ppm.
METRICS: dict[str, tuple[str, bool, str]] = {
    "hr": ("bpm", True, "hr"),
    "hr_resid": ("bpm", True, "hr_resid"),
    "prv": ("ms", True, "prv"),
    "spo2": ("%", True, "spo2"),
    "skin_c": ("C", True, "skin_c"),
    "act": ("g", True, "act"),
    "co2_mmhg": ("mmHg", False, "co2"),
    "temp_c": ("C", False, "temp_c"),
    "rh": ("%", False, "rh"),
    "voc": ("index", False, "voc"),
    "pm25": ("ug/m3", False, "pm25"),
    "cpm": ("counts/min", False, "cpm"),
}
_METRIC_DOMAIN = {
    "hr": "cardio",
    "hr_resid": "cardio",
    "prv": "cardio",
    "spo2": "cardio",
    "act": "cardio",
    "skin_c": "thermal",
    "temp_c": "thermal",
    "co2": "air",
    "co2_mmhg": "air",
    "voc": "air",
    "rh": "air",
    "pm25": "air",
    "pm1": "air",
    "pm10": "air",
    "cpm": "radiation",
}
_DEFAULT_AUTO = {"crew_only": True, "flight_surgeon": False, "urgent": False}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AutoApprove(_Strict):
    crew_only: bool
    flight_surgeon: bool
    urgent: bool


class Enroll(_Strict):
    id: str = Field(pattern=r"^W[0-9]+$")
    name: str = Field(min_length=1, max_length=40)


class ResetBaseline(_Strict):
    crew: str = Field(pattern=r"^W[0-9]+$")
    metric: str | None


class Checkin(_Strict):
    crew: str = Field(pattern=r"^W[0-9]+$")
    mood: int = Field(ge=1, le=5)
    sleep: int = Field(ge=1, le=5)
    note: str | None = Field(max_length=500)


def learning_status(conn: sqlite3.Connection, *, now: float | None = None) -> dict[str, Any]:
    """All-green learning status from ``crew`` and ``node_status``.

    Analytics can call this for the same body the API serves before the first ``kv['status']``
    snapshot exists. Every crew member is still learning; no measured numbers are invented.
    """
    t = _clock(conn) if now is None else now
    ends = t + LEARNING_S
    wearable = _wearable_nodes(conn)
    crew = []
    for row in conn.execute("SELECT id, name FROM crew ORDER BY id"):
        node = wearable.get(str(row["id"]))
        src = node["src"] if node and node["src"] in ("hw", "sim") else "sim"
        crew.append(
            {
                "id": row["id"],
                "name": row["name"],
                "src": src,
                "online": bool(node and node["state"] == "online"),
                "level": "green",
                "baseline": "learning",
                "learning_ends_t": ends,
                "last_seen_t": node["last_seen_t"] if node else None,
            }
        )
    checked = conn.execute(
        "SELECT 1 FROM events WHERE kind = 'checkin' AND t >= ? LIMIT 1",
        (t - CHECKIN_PERIOD_S,),
    ).fetchone()
    thresholds = _thresholds()
    return {
        "t": t,
        "source_mode": "live",
        "thresholds_status": _thresholds_status(thresholds),
        "crew_status": {
            "level": "green",
            "domain": None,
            "sentence": learning_sentence(LEARNING_S),
            "attribution": None,
            "action": None,
        },
        "crew": crew,
        "cards": [_green_card(domain) for domain in DOMAINS],
        "nodes": _nodes(conn),
        "correlation": None,
        "checkin_due": bool(crew) and checked is None,
    }


def create_app(conn: sqlite3.Connection, settings: Settings) -> FastAPI:
    """Build the API around an open store. The dashboard mount, if any, is added last."""
    app = FastAPI(title="Crew Context Monitor gateway", version="0.1.0")
    app.state.conn = conn
    thresholds = _thresholds()
    pressure = (
        thresholds.cabin_pressure_mmhg if thresholds is not None else DEFAULT_CABIN_PRESSURE_MMHG
    )

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        stored = _kv_json(conn, "status")
        if isinstance(stored, dict):
            body = _refresh_live(conn, stored)
            try:
                validate("status", body)
            except ContractError:
                body = learning_status(conn)
        else:
            body = learning_status(conn)
        return body

    @app.get("/api/history")
    def get_history(
        metric: MetricName,
        crew: str | None = Query(default=None, pattern=r"^W[0-9]+$"),
        hours: float = Query(6.0, gt=0, le=168),
        step: float = Query(60.0, ge=60, le=86400),
    ) -> dict[str, Any]:
        _unit, is_crew, _rollup = METRICS[metric]
        if is_crew and crew is None:
            raise HTTPException(status_code=422, detail=f"crew is required for {metric}")
        if is_crew and crew is not None and not _crew_exists(conn, crew):
            raise HTTPException(status_code=404, detail=f"unknown crew member {crew!r}")
        return _history(conn, metric, crew, hours, step, pressure, thresholds)

    @app.get("/api/events")
    def get_events(
        since: float | None = None, limit: int = Query(200, ge=1, le=1000)
    ) -> dict[str, Any]:
        return {"events": _events(conn, since, limit)}

    @app.get("/api/earth-queue")
    def get_earth_queue() -> dict[str, Any]:
        return _earth_queue(conn)

    @app.post("/api/earth-queue/{item_id}/approve")
    def approve(item_id: str) -> dict[str, Any]:
        item = _approve(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"no Earth queue item {item_id!r}")
        return item

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return _settings(conn)

    @app.put("/api/settings")
    def put_settings(body: AutoApprove) -> dict[str, Any]:
        current = _kv_json(conn, "settings")
        stored = dict(current) if isinstance(current, dict) else {}
        stored["auto_approve"] = body.model_dump()
        _kv_set(conn, "settings", stored)
        conn.commit()
        return _settings(conn)

    @app.post("/api/settings/enroll")
    def enroll(body: Enroll) -> dict[str, Any]:
        now = _clock(conn)
        updated = conn.execute(
            "UPDATE crew SET name = ? WHERE id = ?", (body.name, body.id)
        ).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO crew (id, name, enrolled_t) VALUES (?, ?, ?)",
                (body.id, body.name, now),
            )
        conn.commit()
        return _settings(conn)

    @app.post("/api/settings/reset-baseline")
    def reset_baseline(body: ResetBaseline) -> dict[str, Any]:
        if not _crew_exists(conn, body.crew):
            raise HTTPException(status_code=404, detail=f"unknown crew member {body.crew!r}")
        if body.metric is None:
            conn.execute("DELETE FROM baseline WHERE subject = ?", (body.crew,))
        else:
            conn.execute(
                "DELETE FROM baseline WHERE subject = ? AND metric = ?",
                (body.crew, body.metric),
            )
        conn.commit()
        return _settings(conn)

    @app.post("/api/checkin")
    def post_checkin(body: Checkin) -> dict[str, Any]:
        if not _crew_exists(conn, body.crew):
            raise HTTPException(status_code=404, detail=f"unknown crew member {body.crew!r}")
        sentence = checkin_sentence(body.crew, body.mood, body.sleep)
        if body.note:
            sentence = f"{sentence[:-1]}. Note: {body.note}."
        event_id = _insert_event(
            conn,
            t=_clock(conn),
            kind="checkin",
            domain=None,
            subject=body.crew,
            metric=None,
            score=None,
            reason=sentence,
            simulated=0,
        )
        conn.commit()
        return {"ok": True, "event_id": str(event_id)}

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def unknown_api(path: str) -> None:
        raise HTTPException(status_code=404, detail=f"Not Found: /api/{path}")

    mount_dashboard(app, settings.dashboard_dist)
    return app


def _green_card(domain: str) -> dict[str, Any]:
    name, state, detail = detector(Facts(domain=domain, level="green"), 0.0)
    return {
        "domain": domain,
        "level": "green",
        "reason": "Learning the baseline.",
        "action": None,
        "attribution": None,
        "crew": [],
        "numbers": [],
        "detector": {"name": name, "state": state, "detail": detail},
        "model": None,
    }


def _refresh_live(conn: sqlite3.Connection, stored: dict[str, Any]) -> dict[str, Any]:
    """Overwrite the fields that change between analytics snapshots."""
    body = dict(stored)
    body["source_mode"] = "live"
    body["t"] = _clock(conn)
    wearable = _wearable_nodes(conn)
    crew_out = []
    for member in stored.get("crew", []):
        if not isinstance(member, dict):
            continue
        refreshed = dict(member)
        node = wearable.get(str(member.get("id")))
        if node is not None:
            refreshed["online"] = node["state"] == "online"
            refreshed["last_seen_t"] = node["last_seen_t"]
            if node["src"] in ("hw", "sim"):
                refreshed["src"] = node["src"]
        crew_out.append(refreshed)
    body["crew"] = crew_out
    body["nodes"] = _nodes(conn)
    return body


def _history(
    conn: sqlite3.Connection,
    metric: str,
    crew: str | None,
    hours: float,
    step: float,
    pressure: float,
    thresholds: Thresholds | None,
) -> dict[str, Any]:
    unit, is_crew, rollup_metric = METRICS[metric]
    subject = crew if is_crew else _cabin_subject(conn, rollup_metric)
    t_hi = _clock(conn)
    t_lo = t_hi - hours * 3600.0
    points = _points(conn, subject, rollup_metric, t_lo, t_hi, step) if subject else []
    if metric == "co2_mmhg":
        points = [
            {
                **point,
                "mean": ppm_to_mmhg(point["mean"], pressure),
                "min": ppm_to_mmhg(point["min"], pressure),
                "max": ppm_to_mmhg(point["max"], pressure),
            }
            for point in points
        ]
    return {
        "metric": metric,
        "unit": unit,
        "crew": crew if is_crew else None,
        "points": points,
        "baseline": _baseline_band(conn, subject, metric, rollup_metric, points, pressure),
        "cusum": None,
        "limits": _limits(metric, thresholds),
    }


def _points(
    conn: sqlite3.Connection, subject: str, metric: str, t_lo: float, t_hi: float, step: float
) -> list[dict[str, float]]:
    rows = conn.execute(
        """
        SELECT t_min, total, min, max, n FROM rollup_1m
        WHERE subject = ? AND metric = ? AND t_min >= ? AND t_min <= ?
        ORDER BY t_min
        """,
        (subject, metric, t_lo, t_hi),
    ).fetchall()
    buckets: dict[float, list[sqlite3.Row]] = {}
    for row in rows:
        start = math.floor(float(row["t_min"]) / step) * step
        buckets.setdefault(start, []).append(row)
    points = []
    for start, group in buckets.items():
        total = sum(float(row["total"]) for row in group)
        n = sum(int(row["n"]) for row in group)
        points.append(
            {
                "t": start,
                "mean": total / n,
                "min": min(float(row["min"]) for row in group),
                "max": max(float(row["max"]) for row in group),
            }
        )
    return points


def _baseline_band(
    conn: sqlite3.Connection,
    subject: str | None,
    metric: str,
    rollup_metric: str,
    points: list[dict[str, float]],
    pressure: float,
) -> list[dict[str, float]] | None:
    """Constant band from the baseline row, or null when analytics has not learned one.

    The table stores one mean and variance, not a CUSUM, so ``cusum`` stays null.
    """
    if subject is None or not points:
        return None
    row = None
    for name in (metric, rollup_metric):
        row = conn.execute(
            "SELECT metric, ewm_mean, ewm_var FROM baseline WHERE subject = ? AND metric = ?",
            (subject, name),
        ).fetchone()
        if row is not None:
            break
    if row is None:
        return None
    mean = float(row["ewm_mean"])
    spread = 2.0 * math.sqrt(max(float(row["ewm_var"]), 0.0))
    lo, hi = mean - spread, mean + spread
    if row["metric"] == "co2":
        mean, lo, hi = (ppm_to_mmhg(v, pressure) for v in (mean, lo, hi))
    return [{"t": point["t"], "mean": mean, "lo": lo, "hi": hi} for point in points]


def _limits(metric: str, thresholds: Thresholds | None) -> dict[str, float | None]:
    yellow: float | None = None
    red: float | None = None
    if thresholds is not None and metric == "co2_mmhg":
        yellow, red = thresholds.co2_mmhg("yellow"), thresholds.co2_mmhg("red")
    elif thresholds is not None and metric == "temp_c":
        red = thresholds.value("thermal.cabin_temp_c.hi")
    elif thresholds is not None and metric == "voc":
        yellow = thresholds.value("air.voc_index.yellow")
    return {"yellow": yellow, "red": red}


def _events(conn: sqlite3.Connection, since: float | None, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT * FROM events
            WHERE (? IS NULL OR t > ?)
            ORDER BY t DESC, id DESC
            LIMIT ?
        ) ORDER BY t ASC, id ASC
        """,
        (since, since, limit),
    ).fetchall()
    return [_event_body(row) for row in rows]


def _event_body(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    stored = row["domain"] if "domain" in keys else None
    metric = row["metric"]
    kind = str(row["kind"])
    if kind == "sim":
        kind = "sim_event"  # ingest stores the short name; the contract says sim_event
    subject = row["subject"]
    crew = str(subject) if isinstance(subject, str) and CREW_ID.fullmatch(subject) else None
    return {
        "id": str(row["id"]),
        "t": float(row["t"]),
        "kind": kind,
        "domain": _event_domain(kind, metric if isinstance(metric, str) else None, stored),
        "metric": metric,
        "crew": crew,
        "score": row["score"],
        "sentence": str(row["reason"]),
        "simulated": bool(row["simulated"]),
    }


def _event_domain(kind: str, metric: str | None, stored: Any) -> str | None:
    if kind in ("checkin", "node"):
        return None
    if stored in DOMAINS:
        return str(stored)
    if kind == "correlation":
        return "cardio"
    if metric is not None:
        return _METRIC_DOMAIN.get(metric)
    return None


def _earth_queue(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM earth_queue ORDER BY t, id").fetchall()
    return {
        "next_window_t": _next_window(_status_clock(conn)),
        "items": [_queue_item(row) for row in rows],
    }


def _approve(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT id FROM earth_queue WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE earth_queue SET approved = 1 WHERE id = ?", (item_id,))
    conn.commit()
    stored = conn.execute("SELECT * FROM earth_queue WHERE id = ?", (item_id,)).fetchone()
    if stored is None:
        return None
    return _queue_item(stored)


def _queue_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return {
        "id": row["id"],
        "t": float(row["t"]),
        "tier": row["tier"],
        "approved": bool(row["approved"]),
        "sent": bool(row["sent"]),
        "bytes": int(row["bytes"]),
        "summary": row["summary"],
        "payload": payload,
    }


def _settings(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = _kv_json(conn, "settings")
    auto = _DEFAULT_AUTO.copy()
    if isinstance(stored, dict) and isinstance(stored.get("auto_approve"), dict):
        for key in auto:
            if isinstance(stored["auto_approve"].get(key), bool):
                auto[key] = stored["auto_approve"][key]
    crew = [
        {"id": row["id"], "name": row["name"], "enrolled_t": float(row["enrolled_t"])}
        for row in conn.execute("SELECT id, name, enrolled_t FROM crew ORDER BY id")
    ]
    return {"crew": crew, "auto_approve": auto}


def _insert_event(
    conn: sqlite3.Connection,
    *,
    t: float,
    kind: str,
    domain: str | None,
    subject: str | None,
    metric: str | None,
    score: float | None,
    reason: str,
    simulated: int,
) -> int:
    columns = _table_columns(conn, "events")
    if "domain" in columns:
        cur = conn.execute(
            "INSERT INTO events (t, kind, domain, subject, metric, score, reason, simulated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (t, kind, domain, subject, metric, score, reason, simulated),
        )
    else:
        cur = conn.execute(
            "INSERT INTO events (t, kind, subject, metric, score, reason, simulated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t, kind, subject, metric, score, reason, simulated),
        )
    if cur.lastrowid is None:
        raise HTTPException(status_code=500, detail="event was not stored")
    return int(cur.lastrowid)


def _cabin_subject(conn: sqlite3.Connection, rollup_metric: str) -> str | None:
    rows = conn.execute("SELECT id FROM node_status WHERE node = 'cabin' ORDER BY id").fetchall()
    for row in rows:
        hit = conn.execute(
            "SELECT 1 FROM rollup_1m WHERE subject = ? AND metric = ? LIMIT 1",
            (row["id"], rollup_metric),
        ).fetchone()
        if hit is not None:
            return str(row["id"])
    if rows:
        return str(rows[0]["id"])
    found = conn.execute(
        """
        SELECT subject FROM rollup_1m
        WHERE metric = ? AND subject NOT LIKE 'W%'
        ORDER BY t_min DESC LIMIT 1
        """,
        (rollup_metric,),
    ).fetchone()
    return None if found is None else str(found["subject"])


def _wearable_nodes(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM node_status WHERE node = 'wearable'")
    return {str(row["id"]): row for row in rows}


def _nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    nodes = []
    rows = conn.execute("SELECT node, id, state, last_seen_t FROM node_status ORDER BY node, id")
    for row in rows:
        if row["node"] not in ("wearable", "cabin"):
            continue
        if not isinstance(row["id"], str) or NODE_ID.fullmatch(row["id"]) is None:
            continue
        if row["state"] not in ("online", "offline"):
            continue
        nodes.append(
            {
                "node": row["node"],
                "id": row["id"],
                "state": row["state"],
                "last_seen_t": row["last_seen_t"],
            }
        )
    return nodes


def _clock(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT MAX(t) AS t FROM node_status").fetchone()
    if row is not None and row["t"] is not None:
        return float(row["t"])
    row = conn.execute("SELECT MAX(t_min) AS t FROM rollup_1m").fetchone()
    if row is not None and row["t"] is not None:
        return float(row["t"]) + 60.0
    row = conn.execute("SELECT MAX(t) AS t FROM events").fetchone()
    if row is not None and row["t"] is not None:
        return float(row["t"])
    return time.time()


def _status_clock(conn: sqlite3.Connection) -> float:
    """Comms windows follow ``kv['status'].t``, or the wall clock before the first snapshot."""
    stored = _kv_json(conn, "status")
    if isinstance(stored, dict):
        t = stored.get("t")
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            return float(t)
    return time.time()


def _next_window(t: float) -> float:
    return math.floor(t / COMMS_WINDOW_S) * COMMS_WINDOW_S + COMMS_WINDOW_S


def _crew_exists(conn: sqlite3.Connection, crew_id: str) -> bool:
    return conn.execute("SELECT 1 FROM crew WHERE id = ?", (crew_id,)).fetchone() is not None


def _kv_json(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return None


def _kv_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, separators=(",", ":"))),
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _thresholds() -> Thresholds | None:
    try:
        return load_thresholds()
    except (OSError, ThresholdsError):
        return None


def _thresholds_status(thresholds: Thresholds | None) -> str:
    if thresholds is not None and thresholds.status in ("provisional", "final"):
        return thresholds.status
    return "provisional"
