from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ccm_gateway.db import connect

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def firmware_cabin() -> list[dict[str, Any]]:
    lines = (FIXTURES / "firmware_cabin.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
