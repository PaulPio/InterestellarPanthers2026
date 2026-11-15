"""Fallback service: the simulator stands in for any cabin sensor that has failed.

It runs a simulator scenario in real time (``CCM_FALLBACK_SCENARIO``, default ``quiet_day``) and,
for every simulated cabin message (one every 10 s), checks the real sensors with
:func:`ccm_gateway.sensors.assess`. The values of the sensors that are offline are stored under the
rollup subject ``"<node>:sim"``; values for working sensors are thrown away. Real data always wins
when it returns (see :mod:`ccm_gateway.sensors`). If the whole cabin node goes quiet, every sensor
is offline and the whole cabin is simulated.

The simulator's crew messages are ignored: crew come from ``ccm-sim-crew`` or real wearables.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from ccm_sim.engine import build_world, run_world
from ccm_sim.scenario import load_scenario
from ccm_sim.sinks import Record

from ccm_gateway import sensors
from ccm_gateway.config import Settings
from ccm_gateway.db import add_sample, connect

log = logging.getLogger("ccm.fallback")
CABIN_TOPIC = "ccm/cabin/telemetry"


class FallbackSink:
    """A simulator sink that keeps only the readings of offline cabin sensors."""

    def __init__(self, conn: sqlite3.Connection, stale_s: float = 60.0) -> None:
        self.conn = conn
        self.stale_s = stale_s
        self.last_offline: frozenset[str] = frozenset()

    def open_node(
        self, key: str, will_topic: str | None, will_payload: dict[str, Any] | None
    ) -> None:
        return None

    def publish(self, record: Record) -> None:
        if record.topic != CABIN_TOPIC:
            return
        self.fill(record.payload, float(record.payload["t_meas"]))

    def fill(self, payload: dict[str, Any], t: float) -> list[str]:
        """Store the offline sensors' values from one simulated message; return those sensors."""
        node = sensors.cabin_node(self.conn)
        states = sensors.assess(self.conn, node, t, self.stale_s)
        down = [key for key, state in states.items() if state == "offline"]
        subject = sensors.sim_subject(node)
        for key in down:
            for metric in sensors.SENSORS[key].metrics:
                value = payload.get(metric)
                if value is not None:
                    add_sample(self.conn, t, subject, metric, float(value))
        self.conn.commit()
        if frozenset(down) != self.last_offline:
            names = ", ".join(sensors.SENSORS[k].name for k in down) or "none"
            log.info("simulating offline sensors on %s: %s", node, names)
            self.last_offline = frozenset(down)
        return down

    def drop_node(self, key: str, offline: Record) -> None:
        return None

    def restore_node(self, key: str) -> None:
        return None

    def close(self) -> None:
        return None


def run(settings: Settings) -> None:
    """Play the fallback scenario in real time, forever."""
    conn = connect(settings.db_path)
    sink = FallbackSink(conn, settings.fallback_stale_s)
    scenario = load_scenario(settings.fallback_scenario)
    seed = 1
    log.info(
        "fallback started: scenario %s, stale after %.0f s",
        settings.fallback_scenario,
        settings.fallback_stale_s,
    )
    while True:
        # The crew is required by the simulator but its messages are ignored by the sink.
        world = build_world(scenario, seed, float(int(time.time())), ["W1"])
        try:
            run_world(world, sink, 1.0)
        except sqlite3.OperationalError as exc:  # a writer held the lock; start the loop again
            log.warning("database busy: %s", exc)
            conn.rollback()
            time.sleep(5)
        seed += 1
