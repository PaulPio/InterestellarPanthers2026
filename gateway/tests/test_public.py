from __future__ import annotations

import sqlite3

import pytest
from ccm_sim import contracts
from fastapi.testclient import TestClient

from ccm_gateway.config import load_settings
from ccm_gateway.public import create_public_app

KEY = "pi-key"


@pytest.fixture
def client(conn: sqlite3.Connection) -> TestClient:
    return TestClient(create_public_app(conn, load_settings(), KEY))


def test_get_with_the_key_is_answered(client: TestClient) -> None:
    response = client.get("/api/status", headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 200
    assert contracts.errors("status", response.json()) == []


def test_without_the_right_key_nothing_is_answered(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_writes_and_the_dashboard_are_refused(client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {KEY}"}
    body = {"crew": "W1", "mood": 3, "sleep": 3}
    assert client.post("/api/checkin", json=body, headers=auth).status_code == 405
    assert client.put("/api/settings", json={}, headers=auth).status_code == 405
    assert client.get("/", headers=auth).status_code == 405


def test_a_key_is_required(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        create_public_app(conn, load_settings(), "")
