"""The always-on public API: the Pi's live data when it is pushing, the simulator otherwise.

- ``POST /api/uplink`` receives a snapshot from the Pi (``ccm-gateway uplink``), checks the bearer
  key and the contract, and stores it.
- ``GET /api/*`` serves that snapshot while it is fresh (``source_mode: "live"``). When the Pi has
  been quiet for longer than ``CCM_LIVE_MAX_AGE_S`` (default 60 s), every route falls through to
  the simulator demo (``source_mode: "mock"``), so the site never goes dark.
- In live mode the site is a read-only mirror: approvals, check-ins and settings are made on the
  Pi's own dashboard, so those POST/PUT routes answer 409.

The dashboard's static files are served by the host (Vercel), not by this app.
"""

from __future__ import annotations

import gzip
import hmac
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ccm_sim import contracts
from ccm_sim.mockapi.app import create_app as create_mock_app
from ccm_sim.mockapi.state import METRICS
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ccm_cloud.store import MemoryStore, SnapshotStore, Stored, SupabaseStore
from ccm_cloud.wallclock import DEFAULT_ROTATION, WallClockMock

log = logging.getLogger("ccm.cloud")
MAX_BODY_BYTES = 5_000_000
SNAPSHOT_KINDS = {
    "status": "status",
    "events": "events",
    "earth_queue": "earth_queue",
    "settings": "settings",
}
WRITE_PATHS = ("/api/earth-queue/", "/api/settings", "/api/checkin")


def store_from_env() -> SnapshotStore:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    log.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not set: snapshots kept in memory only")
    return MemoryStore()


def check_snapshot(body: Any) -> list[str]:
    """Contract problems in an uplink snapshot (empty when it is good)."""
    if not isinstance(body, dict) or body.get("v") != 1:
        return ["snapshot must be an object with v = 1"]
    problems = []
    for field, kind in SNAPSHOT_KINDS.items():
        problems += [f"{field}: {e}" for e in contracts.errors(kind, body.get(field))]
    history = body.get("history", {})
    if not isinstance(history, dict):
        return problems + ["history must be an object"]
    for key, value in history.items():
        problems += [f"history[{key}]: {e}" for e in contracts.errors("history", value)]
    return problems


def empty_history(metric: str, crew: str | None) -> dict[str, Any]:
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


def live_response(snapshot: dict[str, Any], request: Request) -> Response | None:
    """The answer to a GET from the snapshot, or None when the snapshot has no such route."""
    path = request.url.path
    if path == "/api/status":
        return JSONResponse(snapshot["status"])
    if path == "/api/events":
        events = snapshot["events"]["events"]
        since = request.query_params.get("since")
        if since is not None:
            try:
                events = [e for e in events if e["t"] > float(since)]
            except ValueError:
                return JSONResponse({"detail": "since must be a number"}, status_code=422)
        return JSONResponse({"events": events})
    if path == "/api/earth-queue":
        return JSONResponse(snapshot["earth_queue"])
    if path == "/api/settings":
        return JSONResponse(snapshot["settings"])
    if path == "/api/history":
        metric = request.query_params.get("metric", "")
        crew = request.query_params.get("crew")
        if metric not in METRICS:
            return JSONResponse({"detail": f"unknown metric {metric!r}"}, status_code=422)
        entry = snapshot.get("history", {}).get(f"{metric}|{crew or '-'}")
        return JSONResponse(entry if entry is not None else empty_history(metric, crew))
    return None


def create_app(
    store: SnapshotStore | None = None,
    wall: WallClockMock | None = None,
    uplink_key: str | None = None,
    max_age_s: float | None = None,
) -> FastAPI:
    store = store if store is not None else store_from_env()
    wall = wall or WallClockMock(
        os.environ.get("CCM_ROTATION", DEFAULT_ROTATION),
        int(os.environ.get("CCM_SEED", "1")),
    )
    key = uplink_key if uplink_key is not None else os.environ.get("CCM_UPLINK_KEY", "")
    max_age = (
        max_age_s if max_age_s is not None else float(os.environ.get("CCM_LIVE_MAX_AGE_S", "60"))
    )

    app = FastAPI(title="Crew Context Monitor public API", version="1.0.0")
    mock = create_mock_app(wall.state(), dist=Path("/nonexistent"))  # no static files here

    def fresh() -> Stored | None:
        latest = store.get()
        if latest is None or time.time() - latest.received_at > max_age:
            return None
        return latest

    @app.post("/api/uplink", include_in_schema=False)
    async def uplink(request: Request) -> dict[str, Any]:
        given = request.headers.get("authorization", "")
        if not key or not hmac.compare_digest(given, f"Bearer {key}"):
            raise HTTPException(status_code=401, detail="bad or missing uplink key")
        raw = await request.body()
        if request.headers.get("content-encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError) as exc:
                raise HTTPException(status_code=400, detail="body is not valid gzip") from exc
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="snapshot too large")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="body is not JSON") from exc
        problems = check_snapshot(body)
        if problems:
            raise HTTPException(status_code=422, detail=problems[:20])
        store.put(body)
        return {"ok": True}

    @app.get("/api/_cloud/info", include_in_schema=False)
    def info() -> dict[str, Any]:
        latest = store.get()
        age = None if latest is None else round(time.time() - latest.received_at, 1)
        return {
            "mode": "live" if fresh() is not None else "mock",
            "snapshot_age_s": age,
            "live_max_age_s": max_age,
            "rotation": [s.scenario for s in wall.steps],
        }

    @app.middleware("http")
    async def live_or_mock(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path in ("/api/uplink", "/api/_cloud/info"):
            return await call_next(request)
        latest = fresh()
        if latest is not None:
            if request.method == "GET":
                answer = live_response(latest.body, request)
                if answer is not None:
                    return answer
            elif path.startswith(WRITE_PATHS):
                return JSONResponse(
                    {"detail": "read-only mirror of the live system; change it on the Pi"},
                    status_code=409,
                )
        mock.state.mock = wall.state()
        return await call_next(request)

    app.mount("/", mock)
    return app
