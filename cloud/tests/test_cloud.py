from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from ccm_sim import contracts
from fastapi.testclient import TestClient

from ccm_cloud.app import check_snapshot, create_app
from ccm_cloud.store import MemoryStore, Stored
from ccm_cloud.wallclock import ANCHOR, WallClockMock

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "api" / "valid"
KEY = "test-key"


def example(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    return body


def snapshot() -> dict[str, Any]:
    status = example("status.air_red_cabin")
    status["source_mode"] = "live"
    return {
        "v": 1,
        "t": status["t"],
        "status": status,
        "events": example("events.mixed"),
        "earth_queue": example("earth_queue.pending"),
        "settings": example("settings.default"),
        "history": {"co2_mmhg|-": example("history.co2_cabin")},
    }


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock(ANCHOR + 1000.0)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def client(store: MemoryStore, clock: Clock) -> TestClient:
    wall = WallClockMock("scrubber_failure:60,solar_particle_event:60", clock=clock)
    return TestClient(create_app(store, wall, uplink_key=KEY, max_age_s=60))


def push(client: TestClient, body: Any, key: str = KEY, zipped: bool = True) -> int:
    raw = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if zipped:
        raw = gzip.compress(raw)
        headers["Content-Encoding"] = "gzip"
    return client.post("/api/uplink", content=raw, headers=headers).status_code


def test_the_example_snapshot_is_valid() -> None:
    assert check_snapshot(snapshot()) == []


def test_without_a_snapshot_the_simulator_answers(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert contracts.errors("status", status) == []
    assert status["source_mode"] == "mock"
    assert client.get("/api/_cloud/info").json()["mode"] == "mock"


def test_a_fresh_snapshot_is_served_live(client: TestClient) -> None:
    assert push(client, snapshot()) == 200
    status = client.get("/api/status").json()
    assert status["source_mode"] == "live"
    assert status["crew_status"]["domain"] == "air"
    assert client.get("/api/history?metric=co2_mmhg").json()["unit"] == "mmHg"
    missing = client.get("/api/history?metric=hr&crew=W1").json()
    assert contracts.errors("history", missing) == [] and missing["points"] == []
    events = client.get("/api/events").json()["events"]
    later = client.get(f"/api/events?since={events[0]['t']}").json()["events"]
    assert len(later) == len([e for e in events if e["t"] > events[0]["t"]])
    assert (
        client.post("/api/checkin", json={"crew": "W1", "mood": 3, "sleep": 3}).status_code == 409
    )


def test_a_stale_snapshot_falls_back_to_the_simulator(
    store: MemoryStore, client: TestClient
) -> None:
    store._latest = Stored(snapshot(), 0.0)  # received long ago
    assert client.get("/api/status").json()["source_mode"] == "mock"


def test_bad_uploads_are_refused(client: TestClient) -> None:
    assert push(client, snapshot(), key="wrong") == 401
    broken = snapshot()
    del broken["status"]["cards"]
    assert push(client, broken) == 422
    assert push(client, {"v": 2}) == 422
    assert client.get("/api/status").json()["source_mode"] == "mock"


def test_the_demo_follows_the_wall_clock(clock: Clock) -> None:
    wall = WallClockMock("scrubber_failure:60,solar_particle_event:60", clock=clock)
    first = wall.state()
    t_first = first.t_now
    clock.t += 30  # 30 real seconds = 30 simulated minutes at speed 60
    assert wall.state().t_now - t_first == pytest.approx(1800, abs=60)

    # Two instances at the same moment agree.
    other = WallClockMock("scrubber_failure:60,solar_particle_event:60", clock=clock)
    assert other.state().status()["crew_status"] == wall.state().status()["crew_status"]

    # Past the first scenario (4 h at 60x = 240 s) the rotation moves on, and it loops.
    clock.t = ANCHOR + 250
    assert wall.state().scenario_name == "solar_particle_event"
    clock.t = ANCHOR + wall.period + 10
    assert wall.state().scenario_name == "scrubber_failure"
