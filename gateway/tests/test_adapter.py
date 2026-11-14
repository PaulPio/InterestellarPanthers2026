from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from ccm_gateway.adapter import (
    CABIN_METRICS,
    CabinTelemetry,
    NodeStatus,
    SimEvent,
    WearableTelemetry,
    normalize,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts/examples/mqtt"
RECEIVED = 1_794_690_060.0


def topic_for(path: Path, data: dict[str, Any]) -> str:
    kind = path.name.split(".", 1)[0]
    if kind == "node_status":
        return f"ccm/{data.get('node', 'cabin')}/status/{data.get('id', 'C1')}"
    return {
        "cabin_telemetry": "ccm/cabin/telemetry",
        "wearable_telemetry": "ccm/wearable/telemetry",
        "sim_event": "ccm/sim/event",
    }[kind]


@pytest.mark.parametrize("path", sorted((EXAMPLES / "valid").glob("*.json")), ids=lambda p: p.name)
def test_valid_contract_examples(path: Path) -> None:
    payload = path.read_bytes()
    data = json.loads(payload)
    assert normalize(topic_for(path, data), payload, RECEIVED) is not None


@pytest.mark.parametrize(
    "path", sorted((EXAMPLES / "invalid").glob("*.json")), ids=lambda p: p.name
)
def test_invalid_contract_examples(path: Path) -> None:
    payload = path.read_bytes()
    assert normalize(topic_for(path, json.loads(payload)), payload, RECEIVED) is None


def test_real_firmware_shapes(firmware_cabin: list[dict[str, Any]]) -> None:
    for data in firmware_cabin:
        original = dict(data)
        record = normalize("ccm/cabin/telemetry", json.dumps(data).encode(), RECEIVED)
        assert isinstance(record, CabinTelemetry)
        assert record.src == "hw" and record.id == "C1"
        assert record.t_meas == (data["t_node"] or RECEIVED)
        assert record.t_node == data["t_node"] and record.t_recv == RECEIVED
        assert "co2_mmhg" not in asdict(record)
        for metric in CABIN_METRICS:
            assert getattr(record, metric) == data.get(metric)
        assert data == original


def test_absent_sensors_and_clock_remain_unknown() -> None:
    record = normalize("ccm/cabin/telemetry", b'{"node":"cabin","id":"C1","seq":0}', RECEIVED)
    assert isinstance(record, CabinTelemetry)
    assert record.t_meas == RECEIVED and record.t_node == 0
    assert all(getattr(record, field) is None for field in CABIN_METRICS)


@pytest.mark.parametrize("state", ["online", "offline"])
def test_firmware_status(state: str) -> None:
    record = normalize("ccm/cabin/status", state.encode(), RECEIVED)
    assert record == NodeStatus("cabin", "C1", state, RECEIVED, RECEIVED, "hw")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node", "wearable"),
        ("id", 1),
        ("id", "W1"),
        ("seq", True),
        ("seq", -1),
        ("seq", 2**32),
        ("seq", 1.5),
        ("t_node", "0"),
        ("t_node", -1),
        ("co2", "800"),
        ("co2", 40001),
        ("co2", True),
        ("rh", 101),
        ("temp_c", -21),
        ("voc", 501),
        ("pm25", -1),
        ("cpm", 1.5),
        ("c10s", -1),
    ],
)
def test_bad_firmware_fields(field: str, value: Any, caplog: pytest.LogCaptureFixture) -> None:
    data = {"node": "cabin", "id": "C1", "seq": 1, "t_node": 0, field: value}
    with caplog.at_level(logging.WARNING):
        assert normalize("ccm/cabin/telemetry", json.dumps(data).encode(), RECEIVED) is None
    assert "Rejected MQTT message" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"garbage",
        b"\xff\x00",
        b"[]",
        b"null",
        b"false",
        b"12",
        b'{"node":"cabin","id":"C1","seq":1,"t_node":NaN}',
        b'{"node":"cabin","id":"C1","seq":1,"t_node":1e999}',
        b'{"node":"cabin","id":"C1","seq":1,"t_node":Infinity}',
        b"[" * 2000,
    ],
)
def test_malformed_input_does_not_raise(payload: bytes) -> None:
    assert normalize("ccm/cabin/telemetry", payload, RECEIVED) is None


def test_info_and_other_topics_are_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert normalize("ccm/cabin/info", b"not even JSON", RECEIVED) is None
        assert normalize("other/topic", b"anything", RECEIVED) is None
    assert "Ignoring" in caplog.text


def test_bad_status_and_topic_identity_are_rejected() -> None:
    assert normalize("ccm/cabin/status", b"broken", RECEIVED) is None
    payload = b'{"node":"cabin","id":"C2","state":"online","t":1}'
    assert normalize("ccm/cabin/status/C1", payload, RECEIVED) is None
    assert normalize("ccm/wearable/telemetry", payload, RECEIVED) is None


def test_contract_measurement_time_and_no_finger_are_preserved() -> None:
    payload = (EXAMPLES / "valid/wearable_telemetry.no_finger.json").read_bytes()
    record = normalize("ccm/wearable/telemetry", payload, RECEIVED)
    assert isinstance(record, WearableTelemetry)
    assert record.t_meas == json.loads(payload)["t_meas"]
    assert record.hr is None and record.spo2 is None
    assert record.ibi == ()
    event = (EXAMPLES / "valid/sim_event.mark.json").read_bytes()
    assert isinstance(normalize("ccm/sim/event", event, RECEIVED), SimEvent)


def test_src_messages_do_not_fall_back_to_permissive_hardware_path() -> None:
    payload = b'{"node":"cabin","id":"C1","seq":1,"src":"sim","t_node":0}'
    assert normalize("ccm/cabin/telemetry", payload, RECEIVED) is None


@pytest.mark.parametrize("received", [float("nan"), float("inf"), -1, True])
def test_bad_receipt_time_is_rejected(received: float) -> None:
    assert normalize("ccm/cabin/status", b"online", received) is None
