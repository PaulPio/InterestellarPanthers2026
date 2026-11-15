"""Contract-valid "no data" answers, for Live mode while the Pi is not answering.

Live never shows simulated data: when the Pi is unreachable the dashboard gets these bodies, so
every card says there is no data and nothing invented appears. The simulator answers only a
browser that chose Demo.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any

from ccm_sim.mockapi.state import METRICS

DOMAINS = ("cardio", "air", "thermal", "radiation")
COMMS_WINDOW_S = 1800.0
DEFAULT_AUTO_APPROVE = {"crew_only": True, "flight_surgeon": False, "urgent": False}


def _since(last_ok: float | None) -> str:
    if last_ok is None:
        return "No data from the Pi; 0 readings received by the public site yet."
    clock = datetime.fromtimestamp(last_ok, UTC).strftime("%H:%M")
    return f"No data from the Pi since {clock} UTC."


def status(last_ok: float | None, last_status: dict[str, Any] | None, now: float) -> dict[str, Any]:
    """Every card says no data; nodes from the last real status are shown offline."""
    sentence = _since(last_ok)
    nodes = []
    for node in (last_status or {}).get("nodes", []):
        if isinstance(node, dict):
            nodes.append({**node, "state": "offline"})
    return {
        "t": now,
        "source_mode": "live",
        "thresholds_status": (last_status or {}).get("thresholds_status", "provisional"),
        "crew_status": {
            "level": "green",
            "domain": None,
            "sentence": sentence,
            "attribution": None,
            "action": None,
        },
        "crew": [],
        "cards": [
            {
                "domain": domain,
                "level": "green",
                "reason": sentence,
                "action": None,
                "attribution": None,
                "crew": [],
                "numbers": [],
                "detector": {"name": "no data", "state": "green", "detail": "waiting for the Pi"},
                "model": None,
            }
            for domain in DOMAINS
        ],
        "nodes": nodes,
        "correlation": None,
        "checkin_due": False,
    }


def history(metric: str, crew: str | None) -> dict[str, Any] | None:
    """An empty series for a known metric, or None for an unknown one."""
    if metric not in METRICS:
        return None
    unit, is_crew = METRICS[metric]
    return {
        "metric": metric,
        "unit": unit,
        "crew": crew if is_crew else None,
        "points": [],
        "baseline": None,
        "cusum": None,
        "limits": None,
    }


def events() -> dict[str, Any]:
    return {"events": []}


def earth_queue(now: float) -> dict[str, Any]:
    return {
        "next_window_t": math.floor(now / COMMS_WINDOW_S) * COMMS_WINDOW_S + COMMS_WINDOW_S,
        "items": [],
    }


def settings(last_settings: dict[str, Any] | None) -> dict[str, Any]:
    """Settings change rarely, so the last known ones are kept; else an empty roster."""
    if isinstance(last_settings, dict):
        return last_settings
    return {"crew": [], "auto_approve": dict(DEFAULT_AUTO_APPROVE)}


def now() -> float:
    return time.time()
