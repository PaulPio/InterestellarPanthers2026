"""The always-on public API: the Pi's live data when it is reachable, the simulator otherwise.

The Pi keeps the only database (SQLite) and exposes a read-only API (``ccm-gateway public``)
through Tailscale Funnel. This app, running on Vercel:

- forwards each ``GET /api/*`` to the Pi (``source_mode: "live"``);
- serves the simulator demo for a GET that carries ``ccm-source: demo``, so one browser can
  watch the scripted scenario while everyone else stays on the Pi;
- falls back to the simulator demo (``source_mode: "mock"``) when the Pi is off, offline or not
  configured, so the site never goes dark;
- refuses writes while the Pi is live (409): approvals, check-ins and settings are made on the
  Pi's own dashboard, on the crew's hotspot.

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

from ccm_cloud.pi import PiSource
from ccm_cloud.wallclock import DEFAULT_ROTATION, WallClockMock

WRITE_PATHS = ("/api/earth-queue/", "/api/settings", "/api/checkin")
INFO_PATH = "/api/_cloud/info"


def pi_from_env() -> PiSource | None:
    url, key = os.environ.get("CCM_PI_URL"), os.environ.get("CCM_PI_KEY")
    return PiSource(url, key) if url and key else None


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
            "mode": "live" if live else "mock",
            "pi_configured": pi is not None,
            "pi_error": None if pi is None else pi.last_error,
            "rotation": [s.scenario for s in wall.steps],
        }

    @app.middleware("http")
    async def live_or_mock(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path == INFO_PATH:
            return await call_next(request)
        # A browser on Demo asks for the scripted scenario. Writes still go to the
        # live-Pi rule below, so one viewer's choice cannot change the Pi.
        demo = request.method == "GET" and request.headers.get("ccm-source", "").lower() == "demo"
        if pi is not None and not demo:
            if request.method == "GET":
                answer = await pi.get(path, request.url.query)
                if answer is not None:
                    return JSONResponse(answer[1], status_code=answer[0])
            elif pi.up and path.startswith(WRITE_PATHS):
                return JSONResponse(
                    {"detail": "read-only view of the live system; change it on the Pi"},
                    status_code=409,
                )
        mock.state.mock = wall.state()
        return await call_next(request)

    app.mount("/", mock)
    return app
