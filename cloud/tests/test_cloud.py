from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ccm_sim import contracts
from ccm_sim.mockapi.copyrules import status_strings, violations
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


def make_client(fake: FakePi, clock: Clock, key: str = KEY, backoff_s: float = 5.0) -> TestClient:
    pi = PiSource(
        "https://pi.example.ts.net",
        key,
        backoff_s=backoff_s,
        transport=httpx.MockTransport(fake.handler),
        clock=clock,
    )
    return TestClient(create_app(pi, WallClockMock(ROTATION, clock=clock)))


def assert_no_data(status: dict[str, Any]) -> None:
    """Live with no Pi: valid, labelled live, every card says no data, nothing simulated."""
    assert contracts.errors("status", status) == []
    assert status["source_mode"] == "live"
    assert status["crew"] == []
    for card in status["cards"]:
        assert card["reason"].startswith("No data from the Pi")
        assert card["numbers"] == []
    for where, text, is_reason in status_strings(status):
        assert violations(text, reason=is_reason) == [], where


def test_without_a_pi_live_shows_no_data(clock: Clock) -> None:
    client = TestClient(create_app(None, WallClockMock(ROTATION, clock=clock)))
    assert_no_data(client.get("/api/status").json())
    info = client.get("/api/_cloud/info").json()
    assert (info["mode"], info["pi_configured"]) == ("no_data", False)
    # Demo still plays the simulator for the browser that chose it.
    demo = client.get("/api/status", headers={"ccm-source": "demo"}).json()
    assert demo["source_mode"] == "mock"


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


def test_writes_are_always_refused(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock)
    body = {"crew": "W1", "mood": 3, "sleep": 3}
    assert client.post("/api/checkin", json=body).status_code == 409  # Pi live
    fake.online = False
    client.get("/api/status")  # fails, the Pi is now backing off
    assert client.post("/api/checkin", json=body).status_code == 503  # Pi not answering
    assert all(r.method == "GET" for r in fake.requests)  # nothing was ever sent to the Pi


def test_an_offline_pi_gives_no_data_never_the_simulator(fake: FakePi, clock: Clock) -> None:
    fake.online = False
    client = make_client(fake, clock)
    assert_no_data(client.get("/api/status").json())
    tried = len(fake.requests)
    assert_no_data(client.get("/api/status").json())
    assert len(fake.requests) == tried  # backing off: the Pi was not asked again
    info = client.get("/api/_cloud/info").json()
    assert info["mode"] == "no_data" and "ConnectError" in info["pi_error"]


def test_a_hiccup_is_covered_for_20_s_then_no_data(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock)
    live = client.get("/api/status").json()
    client.get("/api/settings")
    fake.online = False
    clock.t += 10  # one failed request: the real answer from 10 s ago covers it
    assert client.get("/api/status").json() == live
    clock.t += 15  # 25 s without the Pi: no data, nodes from the last real status shown offline
    status = client.get("/api/status").json()
    assert_no_data(status)
    assert "since" in status["crew_status"]["sentence"]
    assert status["nodes"] and all(n["state"] == "offline" for n in status["nodes"])
    # Settings keep the last known values; the rest is empty but valid.
    assert contracts.errors("settings", client.get("/api/settings").json()) == []
    assert client.get("/api/events").json() == {"events": []}
    assert contracts.errors("earth_queue", client.get("/api/earth-queue").json()) == []
    history = client.get("/api/history?metric=co2_mmhg&hours=6").json()
    assert contracts.errors("history", history) == [] and history["points"] == []
    assert client.get("/api/history?metric=nope").status_code == 422


def test_the_pi_is_used_again_once_it_returns(fake: FakePi, clock: Clock) -> None:
    fake.online = False
    client = make_client(fake, clock, backoff_s=0.0)
    assert_no_data(client.get("/api/status").json())
    fake.online = True
    assert client.get("/api/status").json()["crew_status"]["domain"] == "air"


def test_a_wrong_key_gives_no_data(fake: FakePi, clock: Clock) -> None:
    client = make_client(fake, clock, key="stale")
    assert_no_data(client.get("/api/status").json())
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
