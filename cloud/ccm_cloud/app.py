"""The always-on public API: the Pi's live data, or the simulator for a browser that chose Demo.

The Pi keeps the only database (SQLite) and exposes a read-only API (``ccm-gateway public``)
through Tailscale Funnel. This app, running on Vercel:

- **Live** (the default): forwards each ``GET /api/*`` to the Pi. It **never** shows simulated
  data. If one request is slow or fails, the last real answer for it (at most 20 s old) covers
  the gap; after that the dashboard gets contract-valid "no data" answers (``nodata.py``) until
  the Pi answers again. The same applies when no Pi is configured.
- **Demo**: a GET carrying ``ccm-source: demo`` is answered by the wall-clock simulator, for that
  browser only.
- Writes are refused (409 while the Pi answers, 503 while it doesn't): approvals, check-ins and
  settings are made on the Pi's own dashboard, on the crew's hotspot.

Environment: ``CCM_PI_URL`` (the Pi's Funnel URL), ``CCM_PI_KEY`` (the Pi's ``CCM_PUBLIC_KEY``),
optional ``CCM_ROTATION`` and ``CCM_SEED`` for the demo. The dashboard's static files are served
by the host (Vercel), not by this app.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ccm_sim.mockapi.app import create_app as create_mock_app
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ccm_cloud import nodata
from ccm_cloud.pi import PiSource
from ccm_cloud.wallclock import DEFAULT_ROTATION, WallClockMock

INFO_PATH = "/api/_cloud/info"
GRACE_S = 20.0  # a slow or failed request may be covered by a real answer this old


def pi_from_env() -> PiSource | None:
    url, key = os.environ.get("CCM_PI_URL"), os.environ.get("CCM_PI_KEY")
    return PiSource(url, key) if url and key else None


def _no_data(request: Request, pi: PiSource | None) -> Response:
    """The Live answer while the Pi cannot answer: no data, never simulated data."""
    path, now = request.url.path, nodata.now()
    last_ok = None if pi is None else pi.last_ok
    if path == "/api/status":
        last = None if pi is None else pi.latest("/api/status")
        return JSONResponse(nodata.status(last_ok, None if last is None else last.body, now))
    if path == "/api/history":
        body = nodata.history(
            request.query_params.get("metric", ""), request.query_params.get("crew")
        )
        if body is None:
            return JSONResponse({"detail": "unknown metric"}, status_code=422)
        return JSONResponse(body)
    if path == "/api/events":
        return JSONResponse(nodata.events())
    if path == "/api/earth-queue":
        return JSONResponse(nodata.earth_queue(now))
    if path == "/api/settings":
        last = None if pi is None else pi.latest("/api/settings")
        return JSONResponse(nodata.settings(None if last is None else last.body))
    return JSONResponse({"detail": "no data: the Pi is not answering"}, status_code=503)


def create_app(pi: PiSource | None = None, wall: WallClockMock | None = None) -> FastAPI:
    pi = pi if pi is not None else pi_from_env()
    wall = wall or WallClockMock(
        os.environ.get("CCM_ROTATION", DEFAULT_ROTATION),
        int(os.environ.get("CCM_SEED", "1")),
    )
    app = FastAPI(title="Crew Context Monitor public API", version="1.0.0")
    mock = create_mock_app(wall.state(), dist=Path("/nonexistent"))  # no static files here

    @app.get(INFO_PATH, include_in_schema=False)
    async def info() -> dict[str, Any]:
        live = pi is not None and await pi.get("/api/status", "") is not None
        return {
            "mode": "live" if live else "no_data",
            "pi_configured": pi is not None,
            "pi_error": None if pi is None else pi.last_error,
            "rotation": [s.scenario for s in wall.steps],
        }

    @app.middleware("http")
    async def live_or_demo(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path == INFO_PATH:
            return await call_next(request)
        demo = request.method == "GET" and request.headers.get("ccm-source", "").lower() == "demo"
        if demo:  # the simulator, for this browser only
            mock.state.mock = wall.state()
            return await call_next(request)
        if request.method != "GET":  # never change the crew's system from the internet
            if pi is not None and pi.up:
                detail, code = "read-only view of the live system; change it on the Pi", 409
            else:
                detail, code = "no data: the Pi is not answering", 503
            return JSONResponse({"detail": detail}, status_code=code)
        if pi is not None:
            query = request.url.query
            answer = await pi.get(path, query) or pi.recent(path, query, GRACE_S)
            if answer is not None:
                return JSONResponse(answer.body, status_code=answer.status_code)
        return _no_data(request, pi)

    app.mount("/", mock)
    return app
