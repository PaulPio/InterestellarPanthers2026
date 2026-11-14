"""Where the Pi's latest snapshot is kept between requests.

Serverless instances share no memory, so production uses a Supabase table (free tier) through its
REST API. ``MemoryStore`` is for local runs and tests.

Supabase table (run once in the SQL editor)::

    create table ccm_snapshot (
      id int primary key,
      body jsonb not null,
      received_at timestamptz not null default now()
    );
    alter table ccm_snapshot enable row level security;  -- only the service key can read or write
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

log = logging.getLogger("ccm.cloud.store")


@dataclass(frozen=True)
class Stored:
    body: dict[str, Any]
    received_at: float  # unix seconds, server clock


class SnapshotStore(Protocol):
    def put(self, body: dict[str, Any]) -> None: ...

    def get(self) -> Stored | None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._latest: Stored | None = None

    def put(self, body: dict[str, Any]) -> None:
        self._latest = Stored(body, time.time())

    def get(self) -> Stored | None:
        return self._latest


class SupabaseStore:
    """One row (``id = 1``) in ``ccm_snapshot``. Reads are cached briefly per instance."""

    def __init__(
        self,
        url: str,
        service_key: str,
        cache_s: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = url.rstrip("/") + "/rest/v1/ccm_snapshot"
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self.cache_s = cache_s
        self.client = client or httpx.Client(timeout=5.0)
        self._cached: tuple[float, Stored | None] | None = None

    def put(self, body: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        response = self.client.post(
            self.endpoint,
            params={"on_conflict": "id"},
            headers={
                **self.headers,
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json={"id": 1, "body": body, "received_at": now},
        )
        response.raise_for_status()
        self._cached = None

    def get(self) -> Stored | None:
        if self._cached is not None and time.monotonic() - self._cached[0] < self.cache_s:
            return self._cached[1]
        result: Stored | None = None
        try:
            response = self.client.get(
                self.endpoint,
                params={"id": "eq.1", "select": "body,received_at"},
                headers=self.headers,
            )
            response.raise_for_status()
            rows = response.json()
            if rows:
                received = datetime.fromisoformat(rows[0]["received_at"]).timestamp()
                result = Stored(rows[0]["body"], received)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("could not read the snapshot: %s", exc)  # fall back to the simulator
        self._cached = (time.monotonic(), result)
        return result
