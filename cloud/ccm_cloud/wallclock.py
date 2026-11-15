"""A simulator demo that follows the wall clock, for serverless hosts with no background threads.

The mock API normally advances a scenario in a background thread. A serverless function has no
thread that outlives a request, so here every request works out where the demo should be from the
wall clock and runs the scenario up to that point with ``MockState.advance_to``. The rotation of
scenarios loops forever. Every instance computes the same position from the same clock and seed,
so all visitors see the same picture, and a cold instance catches up in about a second.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ccm_sim.mockapi.state import MockState
from ccm_sim.scenario import load_scenario

# Fixed origin for the loop, so every instance agrees on the position (2026-11-14T00:00:00Z).
ANCHOR = 1_794_614_400.0
DEFAULT_ROTATION = "scrubber_failure:60,individual_illness:120,solar_particle_event:60"


@dataclass(frozen=True)
class Step:
    scenario: str
    speed: float  # simulated seconds per real second
    duration: float  # simulated seconds

    @property
    def real_s(self) -> float:
        return self.duration / self.speed


def parse_rotation(text: str) -> list[Step]:
    """``"scrubber_failure:60,individual_illness:120"`` -> steps. Speed defaults to 60."""
    steps = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        name, _, speed = part.partition(":")
        rate = float(speed) if speed else 60.0
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"bad speed in rotation entry {part!r}")
        steps.append(Step(name, rate, float(load_scenario(name).duration)))
    if not steps:
        raise ValueError("the rotation needs at least one scenario")
    return steps


class WallClockMock:
    """Holds the ``MockState`` for the current rotation step, advanced to the current time."""

    def __init__(
        self,
        rotation: str = DEFAULT_ROTATION,
        seed: int = 1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.steps = parse_rotation(rotation)
        self.seed = seed
        self.clock = clock
        self.period = sum(s.real_s for s in self.steps)
        self._lock = threading.Lock()
        self._key: tuple[int, int] | None = None  # (loop number, step index)
        self._state: MockState | None = None

    def position(self, now: float) -> tuple[int, int, float, float]:
        """``(loop, step, step_start_wall, simulated_elapsed)`` for wall time ``now``."""
        since = max(now - ANCHOR, 0.0)
        loop, within = divmod(since, self.period)
        start = ANCHOR + loop * self.period
        for index, step in enumerate(self.steps):
            if within < step.real_s:
                return int(loop), index, start, within * step.speed
            within -= step.real_s
            start += step.real_s
        last = len(self.steps) - 1  # float rounding at the very end of a loop
        return (
            int(loop),
            last,
            start - self.steps[last].real_s,
            self.steps[last].duration,
        )

    def state(self) -> MockState:
        """The mock state for now, created or advanced as needed."""
        with self._lock:
            loop, index, start, elapsed = self.position(self.clock())
            step = self.steps[index]
            if self._state is None or self._key != (loop, index):
                # Simulated time starts at the step's wall-clock start, as serve-mock does.
                self._state = MockState(
                    step.scenario,
                    step.speed,
                    seed=self.seed,
                    t0=start,
                    start_thread=False,
                )
                self._key = (loop, index)
            self._state.advance_to(self._state.t0 + elapsed)
            return self._state
