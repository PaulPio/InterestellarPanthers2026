"""Normalize the simulator contract and the cabin firmware's wire format."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

from ccm_sim.contracts import ContractError, validate

LOG = logging.getLogger(__name__)
Source: TypeAlias = Literal["hw", "sim"]
Node: TypeAlias = Literal["cabin", "wearable"]
State: TypeAlias = Literal["online", "offline"]
CABIN_METRICS = ("co2", "temp_c", "rh", "voc", "pm1", "pm25", "pm10", "cpm", "c10s")


@dataclass(frozen=True)
class CabinTelemetry:
    id: str
    seq: int
    src: Source
    t_node: float
    t_meas: float
    t_recv: float
    co2: float | None
    temp_c: float | None
    rh: float | None
    voc: float | None
    pm1: float | None
    pm25: float | None
    pm10: float | None
    cpm: float | None
    c10s: float | None


@dataclass(frozen=True)
class WearableTelemetry:
    id: str
    crew: str
    seq: int
    src: Source
    t_node: float
    t_meas: float
    t_recv: float
    hr: int | None
    spo2: int | None
    skin_c: float | None  # None: the wearable's TMP117 is missing
    act: float | None  # g; None: the wearable's MPU6050 is missing
    motion: bool
    ibi: tuple[int, ...]
    batt: float | None  # None: no battery sense wired


@dataclass(frozen=True)
class NodeStatus:
    node: Node
    id: str
    state: State
    t: float
    t_recv: float
    src: Source | None = None


@dataclass(frozen=True)
class SimEvent:
    scenario: str
    name: str
    label: str
    phase: Literal["start", "end", "mark"]
    t_meas: float
    crew: str | None
    t_recv: float


Normalized: TypeAlias = CabinTelemetry | WearableTelemetry | NodeStatus | SimEvent


def _finite(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)
    elif isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError("non-finite number")


def _number(value: Any, low: float, high: float = math.inf, *, integer: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
        or (integer and not isinstance(value, int))
    ):
        raise ValueError("invalid numeric field")
    return float(value)


def _metric(data: dict[str, Any], name: str) -> float | None:
    value = data.get(name)
    return None if value is None else float(value)


def _cabin(data: dict[str, Any], t_recv: float) -> CabinTelemetry:
    if "src" in data:
        validate("cabin_telemetry", data)
        t_node, t_meas = float(data["t_node"]), float(data["t_meas"])
        src = cast(Source, data["src"])
    else:
        if data.get("node") != "cabin" or not isinstance(data.get("id"), str):
            raise ValueError("hardware cabin requires node=cabin and an id")
        if re.fullmatch(r"C[0-9]+", data["id"]) is None:
            raise ValueError("invalid cabin id")
        _number(data.get("seq"), 0, 2**32 - 1, integer=True)
        t_node = _number(data.get("t_node", 0), 0)
        t_meas = t_node if t_node > 0 else t_recv
        src = "hw"
        for name in CABIN_METRICS:
            if data.get(name) is None:
                continue
            low, high = {
                "co2": (0, 40000),
                "temp_c": (-20, 80),
                "rh": (0, 100),
                "voc": (0, 500),
            }.get(name, (0, math.inf))
            _number(data[name], low, high, integer=name not in ("temp_c", "rh"))
    return CabinTelemetry(
        id=data["id"],
        seq=int(data["seq"]),
        src=src,
        t_node=t_node,
        t_meas=t_meas,
        t_recv=t_recv,
        co2=_metric(data, "co2"),
        temp_c=_metric(data, "temp_c"),
        rh=_metric(data, "rh"),
        voc=_metric(data, "voc"),
        pm1=_metric(data, "pm1"),
        pm25=_metric(data, "pm25"),
        pm10=_metric(data, "pm10"),
        cpm=_metric(data, "cpm"),
        c10s=_metric(data, "c10s"),
    )


PLAIN_STATUS: dict[str, tuple[Node, str]] = {
    "ccm/cabin/status": ("cabin", "C1"),
    "ccm/wearable/status": ("wearable", "W1"),
}
G = 9.80665  # m/s^2 per g
EPOCH_MIN = 1_700_000_000  # a t_node below this is seconds since power-on, not clock time


def _optional(data: dict[str, Any], name: str, low: float, high: float) -> float | None:
    value = data.get(name)
    return None if value is None else _number(value, low, high)


def _wearable_hw(data: dict[str, Any], t_recv: float) -> WearableTelemetry:
    """The wearable firmware's message, as it sends it (``firmware/ccm_wearable``).

    It has no ``src``, ``crew`` or ``t_meas``; ``t_node`` is seconds since power-on until the
    Pi's time server answers; ``skin_c``, ``act`` and ``batt`` are null when that part is
    missing; ``act`` is in m/s^2 (the contract uses g); ``finger:false`` marks no finger.
    """
    if data.get("node") != "wearable" or not isinstance(data.get("id"), str):
        raise ValueError("hardware wearable requires node=wearable and an id")
    if re.fullmatch(r"W[0-9]+", data["id"]) is None:
        raise ValueError("invalid wearable id")
    seq = int(_number(data.get("seq"), 0, 2**32 - 1, integer=True))
    t_node = _number(data.get("t_node", 0), 0)
    hr = _optional(data, "hr", 25, 250)
    spo2 = _optional(data, "spo2", 50, 100)
    act_ms2 = _optional(data, "act", 0, 1000)
    motion = data.get("motion", False)
    if not isinstance(motion, bool):
        raise ValueError("motion must be true or false")
    raw_ibi = data.get("ibi", [])
    if not isinstance(raw_ibi, list):
        raise ValueError("ibi must be a list")
    ibi = tuple(
        int(v)
        for v in raw_ibi
        if isinstance(v, int) and not isinstance(v, bool) and 300 <= v <= 2000
    )[:20]
    return WearableTelemetry(
        id=data["id"],
        crew=data["id"],
        seq=seq,
        src="hw",
        t_node=t_node,
        t_meas=t_node if t_node >= EPOCH_MIN else t_recv,
        t_recv=t_recv,
        hr=None if hr is None else round(hr),
        spo2=None if spo2 is None else round(spo2),
        skin_c=_optional(data, "skin_c", 10, 45),
        act=None if act_ms2 is None else act_ms2 / G,
        motion=motion,
        ibi=ibi,
        batt=_optional(data, "batt", 2.5, 4.5),
    )


def normalize(topic: str, payload: bytes, t_recv: float) -> Normalized | None:
    """Reject malformed input with a reason; never let a bad packet stop ingest."""
    if topic == "ccm/cabin/info":
        LOG.debug("Ignoring cabin info")
        return None
    try:
        _number(t_recv, 0)
        if topic in PLAIN_STATUS:
            # The firmware publishes plain text with no id: one node of each kind exists.
            state = payload.decode("utf-8").strip()
            if state not in ("online", "offline"):
                raise ValueError("expected plain-text online or offline")
            plain_node, plain_id = PLAIN_STATUS[topic]
            return NodeStatus(plain_node, plain_id, cast(State, state), t_recv, t_recv, "hw")
        status = re.fullmatch(r"ccm/(cabin|wearable)/status/([WC][0-9]+)", topic)
        kinds = {
            "ccm/cabin/telemetry": "cabin_telemetry",
            "ccm/wearable/telemetry": "wearable_telemetry",
            "ccm/sim/event": "sim_event",
        }
        if topic not in kinds and status is None:
            LOG.debug("Ignoring topic %s", topic)
            return None
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        _finite(data)
        if topic == "ccm/cabin/telemetry":
            return _cabin(data, t_recv)
        if topic == "ccm/wearable/telemetry" and "src" not in data:
            return _wearable_hw(data, t_recv)
        kind = "node_status" if status is not None else kinds[topic]
        validate(kind, data)
        if status is not None:
            node, device_id = status.groups()
            if data["node"] != node or data["id"] != device_id:
                raise ValueError("status identity does not match its topic")
            if not device_id.startswith("C" if node == "cabin" else "W"):
                raise ValueError("status id does not match its node type")
            return NodeStatus(cast(Node, node), device_id, data["state"], float(data["t"]), t_recv)
        if kind == "sim_event":
            return SimEvent(
                data["scenario"],
                data["name"],
                data["label"],
                data["phase"],
                float(data["t_meas"]),
                data["crew"],
                t_recv,
            )
        return WearableTelemetry(
            id=data["id"],
            crew=data["crew"],
            seq=int(data["seq"]),
            src=data["src"],
            t_node=float(data["t_node"]),
            t_meas=float(data["t_meas"]),
            t_recv=t_recv,
            hr=None if data["hr"] is None else int(data["hr"]),
            spo2=None if data["spo2"] is None else int(data["spo2"]),
            skin_c=float(data["skin_c"]),
            act=float(data["act"]),
            motion=data["motion"],
            ibi=tuple(int(value) for value in data["ibi"]),
            batt=float(data["batt"]),
        )
    except (ValueError, TypeError, OverflowError, RecursionError, ContractError) as exc:
        LOG.warning("Rejected MQTT message on %s: %s", topic, exc)
        return None
