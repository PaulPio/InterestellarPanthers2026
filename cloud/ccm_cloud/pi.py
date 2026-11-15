"""Reads the Pi's public read-only API (``ccm-gateway public``) over its Tailscale Funnel URL.

The Pi is the only database. When it is off or offline, a request fails fast and the Pi is
skipped for a short back-off, so later visitors do not each wait for the same timeout. The last
good answer for each request is kept, so a single slow request can be covered by it for a few
seconds instead of blanking the screen.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("ccm.cloud.pi")


@dataclass(frozen=True)
class Answer:
    status_code: int
    body: Any
    at: float  # wall-clock seconds when the Pi gave it


class PiSource:
    def __init__(
        self,
        url: str,
        key: str,
        timeout_s: float = 3.0,
        backoff_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}"}
        self.backoff_s = backoff_s
        self.client = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self.clock = clock
        self.down_until = 0.0
        self.last_error: str | None = None
        self.last_ok: float | None = None  # wall-clock seconds of the last good answer
        self._answers: dict[str, Answer] = {}

    @property
    def up(self) -> bool:
        """False while backing off after a failure."""
        return self.clock() >= self.down_until

    def _fail(self, reason: str) -> None:
        self.down_until = self.clock() + self.backoff_s
        if reason != self.last_error:
            log.warning("Pi unreachable: %s", reason)
        self.last_error = reason

    @staticmethod
    def _key(path: str, query: str) -> str:
        return f"{path}?{query}" if query else path

    def recent(self, path: str, query: str, max_age_s: float) -> Answer | None:
        """The last good answer for this request, if it is at most ``max_age_s`` old."""
        answer = self._answers.get(self._key(path, query))
        if answer is None or self.clock() - answer.at > max_age_s:
            return None
        return answer

    def latest(self, path: str, query: str = "") -> Answer | None:
        """The last good answer for this request, however old."""
        return self._answers.get(self._key(path, query))

    async def get(self, path: str, query: str) -> Answer | None:
        """The Pi's answer, or None when it cannot answer now.

        A 4xx other than 401 is a real answer about the request (unknown crew, bad metric) and is
        passed through. A 401 means the keys disagree, which is a setup fault.
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
        answer = Answer(response.status_code, body, self.clock())
        if response.status_code == 200:
            self.last_ok = answer.at
            self._answers[self._key(path, query)] = answer
        return answer
