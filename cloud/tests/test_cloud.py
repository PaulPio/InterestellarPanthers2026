from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ccm_sim import contracts
from fastapi.testclient import TestClient

from ccm_cloud.app import create_app
from ccm_cloud.pi import PiSource
from ccm_cloud.wallclock import ANCHOR, WallClockMock

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "api" / "valid"
KEY = "pi-key"
ROTATION = "scrubber_failure:60,solar_particle_event:60"


def example(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    return body


class FakePi:
    """Stands in for the Pi's public API behind httpx.MockTransport."""

    def __init__(self) -> None:
        self.online = True
        self.requests: list[httpx.Request] = []
        status = example("status.air_red_cabin")
        status["source_mode"] = "live"
        self.routes = {"/api/status": status, "/api/events": example("events.mixed")}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.online:
            raise httpx.ConnectError("Pi is off")
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"detail": "missing or wrong key"})
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=body)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock(ANCHOR + 1000.0)


@pytest.fixture
def fake() -> FakePi:
    return FakePi()


def make_client(fake: FakePi, clock: Clock, key: str = KEY, backoff_s: float = 15.0) -> TestClient:
    pi = PiSource(
        "https://pi.example.ts.net",
        key,
        backoff_s=backoff_s,
        transport=httpx.MockTransport(fake.handler),
    )
    return TestClient(create_app(pi, WallClockMock(ROTATION, clock=clock)))


def test_without_a_pi_the_simulator_answers(clock: Clock) -> None:
    client = TestClient(create_app(None, WallClockMock(ROTATION, clock=clock)))
    status = client.get("/api/status").json()
    assert contracts.errors("status", status) == []
    assert status["source_mode"] == "mock"
    assert client.get("/api/_cloud/info").json()["pi_configured"] is False


def test_a_reachable_pi_is_served_live(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock)
    status = client.get("/api/status").json()
    assert status["source_mode"] == "live"
    assert status["crew_status"]["domain"] == "air"
    client.get("/api/events?since=5&limit=10")
    assert fake.requests[-1].url.query == b"since=5&limit=10"  # the query is forwarded
    assert client.get("/api/history?metric=hr&crew=W9").status_code == 404  # Pi's answer kept
    assert client.get("/api/_cloud/info").json()["mode"] == "live"


def test_a_demo_header_skips_a_live_pi(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock)
    status = client.get("/api/status", headers={"ccm-source": "demo"}).json()
    assert status["source_mode"] == "mock"
    assert fake.requests == []
    body = {"crew": "W1", "mood": 3, "sleep": 3}
    assert client.post("/api/checkin", json=body, headers={"ccm-source": "demo"}).status_code == 409


def test_writes_are_refused_while_the_pi_is_live(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock)
    body = {"crew": "W1", "mood": 3, "sleep": 3}
    assert client.post("/api/checkin", json=body).status_code == 409
    assert all(r.method == "GET" for r in fake.requests)  # nothing was sent to the Pi


def test_an_offline_pi_falls_back_and_is_skipped_for_a_while(fake: FakePi, clock: Clock) -> None:
    fake.online = False
    client = make_client(fake, clock)
    assert client.get("/api/status").json()["source_mode"] == "mock"
    tried = len(fake.requests)
    assert client.get("/api/status").json()["source_mode"] == "mock"
    assert len(fake.requests) == tried  # backing off: the Pi was not asked again
    info = client.get("/api/_cloud/info").json()
    assert info["mode"] == "mock" and "ConnectError" in info["pi_error"]


def test_the_pi_is_used_again_once_it_returns(fake: FakePi, clock: Clock) -> None:
    fake.online = False
    client = make_client(fake, clock, backoff_s=0.0)
    assert client.get("/api/status").json()["source_mode"] == "mock"
    fake.online = True
    assert client.get("/api/status").json()["source_mode"] == "live"


def test_a_wrong_key_falls_back(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock, key="stale")
    assert client.get("/api/status").json()["source_mode"] == "mock"
    assert client.get("/api/_cloud/info").json()["pi_error"] == "HTTP 401"


def test_the_demo_follows_the_wall_clock(clock: Clock) -> None:
    wall = WallClockMock(ROTATION, clock=clock)
    t_first = wall.state().t_now
    clock.t += 30  # 30 real seconds = 30 simulated minutes at speed 60
    assert wall.state().t_now - t_first == pytest.approx(1800, abs=60)

    # Two instances at the same moment agree.
    other = WallClockMock(ROTATION, clock=clock)
    assert other.state().status()["crew_status"] == wall.state().status()["crew_status"]

    # Past the first scenario (4 h at 60x = 240 s) the rotation moves on, and it loops.
    clock.t = ANCHOR + 250
    assert wall.state().scenario_name == "solar_particle_event"
    clock.t = ANCHOR + wall.period + 10
    assert wall.state().scenario_name == "scrubber_failure"
