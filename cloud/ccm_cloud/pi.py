"""Reads the Pi's public read-only API (``ccm-gateway public``) over its Tailscale Funnel URL.

The Pi is the only database. When it is off or offline, a request fails fast, the Pi is skipped
for a short back-off so later visitors do not each wait for the same timeout, and the caller
falls back to the simulator.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("ccm.cloud.pi")


class PiSource:
    def __init__(
        self,
        url: str,
        key: str,
        timeout_s: float = 3.0,
        backoff_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}"}
        self.backoff_s = backoff_s
        self.client = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self.down_until = 0.0
        self.last_error: str | None = None

    @property
    def up(self) -> bool:
        """False while backing off after a failure."""
        return time.monotonic() >= self.down_until

    def _fail(self, reason: str) -> None:
        self.down_until = time.monotonic() + self.backoff_s
        if reason != self.last_error:
            log.warning("Pi unreachable, using the simulator: %s", reason)
        self.last_error = reason

    async def get(self, path: str, query: str) -> tuple[int, Any] | None:
        """``(status_code, json)`` from the Pi, or None when it cannot answer.

        A 4xx other than 401 is a real answer about the request (unknown crew, bad metric) and is
        passed through. A 401 means the keys disagree, which is a setup fault, so it falls back.
        """
        if not self.up:
            return None
        target = f"{self.url}{path}" + (f"?{query}" if query else "")
        try:
            response = await self.client.get(target, headers=self.headers)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return None
        if response.status_code >= 500 or response.status_code == 401:
            self._fail(f"HTTP {response.status_code}")
            return None
        self.last_error = None
        return response.status_code, body
