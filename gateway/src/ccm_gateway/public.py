"""The read-only API the public website reads from the Pi.

The Pi's own dashboard (port 8000) stays on the hotspot. This second listener (port 8001 by
default) is the only thing exposed to the internet, through Tailscale Funnel, and the public site
on Vercel forwards its visitors' GET requests here. It answers only ``GET /api/*`` requests that
carry ``Authorization: Bearer <CCM_PUBLIC_KEY>``; everything else is refused, so nobody on the
internet can approve, check in or change settings on the crew's system.
"""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ccm_gateway.api import create_app
from ccm_gateway.config import Settings


def create_public_app(conn: sqlite3.Connection, settings: Settings, key: str) -> FastAPI:
    if not key:
        raise ValueError("the public API needs a key (CCM_PUBLIC_KEY)")
    # Same routes and responses as the local API, without the dashboard's static files.
    app = create_app(conn, replace(settings, dashboard_dist=Path("/nonexistent")))
    expected = f"Bearer {key}"

    @app.middleware("http")
    async def read_only_with_key(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse({"detail": "missing or wrong key"}, status_code=401)
        if request.method != "GET" or not request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "read-only: GET /api/* only"}, status_code=405)
        return await call_next(request)

    return app
