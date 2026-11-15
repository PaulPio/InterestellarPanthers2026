"""Analytics, version 1: baselines, the four risk cards, attribution and the status snapshot.

``process_pending`` walks every complete minute of rollups that has not been analysed yet, in
order of measured time, so live data, a replay and a fast simulator run all take the same path.
Each minute it updates the per-person baselines, decides the level of each card from measured
values, and writes the contract-shaped ``/api/status`` body to ``kv['status']``.

Severity comes from measured values only. Attribution changes the wording, never a level.
A decision-support prototype, not a medical device.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ccm_sim.mockapi import templates as tpl
from ccm_sim.thresholds import Thresholds, load_thresholds
from ccm_sim.units import ppm_to_mmhg

from ccm_gateway import sensors
from ccm_gateway.config import Settings
from ccm_gateway.db import connect

log = logging.getLogger("ccm.analytics")

MINUTE = 60
DOMAINS = ("cardio", "air", "thermal", "radiation")
RANK = {"green": 0, "yellow": 1, "red": 2}
WORST_ORDER = ("radiation", "air", "thermal", "cardio")  # tie-break for the status bar
RED_TIER = {
    "cardio": "crew_only",
    "air": "flight_surgeon",
    "thermal": "flight_surgeon",
    "radiation": "urgent",
}
COMMS_WINDOW_S = 1800
STATE_KEY = "analytics_state"
STATUS_KEY = "status"


@dataclass(frozen=True)
class Params:
    """Tunable detector settings. The limits themselves live in ``contracts/thresholds.yaml``."""

    learning_s: float = 7200.0  # no cardio alerts for this long after a crew member is first seen
    half_life_min: float = 2880.0  # baseline half-life, about 2 days
    window_min: int = 5  # averaging window for the values the cards show
    judge_min: int = 15  # longer window for the cardio judgement, to average out beat noise
    bucket_half_life: float = 120.0  # samples; the time-of-day baseline, about 2 days per hour
    bucket_min_n: int = 20  # samples an hour-of-day bucket needs before it is used
    z_event: float = 3.0
    hr_min_delta: float = 10.0  # bpm; provisional detectability line (D24)
    prv_min_delta: float = 8.0  # ms; provisional detectability line (D24)
    hr_sd_floor: float = 1.0
    prv_sd_floor: float = 2.0
    confirm_min: int = 3  # a change must hold this many minutes before it counts
    clear_min: int = 10  # and be absent this many minutes before it clears
    spo2_red: float = 92.0
    act_rest_max: float = 0.3  # above this the crew member is moving; cardio is not judged
    min_prv_pairs: int = 30
    red_factor: float = 2.0  # both signs at this multiple of the line make cardio red
    cabin_co2_shift_mmhg: float = 0.5  # a cabin reading "changed too" for attribution
    skin_cabin_gain: float = 0.25  # C of skin change per C of cabin change, at steady state
    cabin_wide_crew: int = 2  # this many crew moving together counts as cabin-wide
    stale_s: float = 300.0  # a value older than this is shown as missing
    max_catchup_min: int = 8 * 24 * 60


@dataclass
class CrewView:
    """What one tick knows about one crew member."""

    id: str
    name: str
    ready: bool
    learning_ends_t: float | None
    hr: float | None = None
    hr0: float | None = None
    prv: float | None = None
    prv0: float | None = None
    spo2: float | None = None
    skin_resid: float | None = None
    resting: bool = True
    level: str = "green"
    hr_changed: bool = False
    prv_changed: bool = False
    low_spo2: bool = False
    since_t: float | None = None


@dataclass
class CabinView:
    id: str | None = None
    co2: float | None = None  # mmHg
    co2_0: float | None = None
    voc: float | None = None
    voc0: float | None = None
    temp: float | None = None
    temp0: float | None = None
    cpm: float | None = None
    cpm0: float | None = None
    radiation_streak: int = 0
    simulated: set[str] = field(default_factory=set)  # metrics shown from the simulator
    offline: dict[str, float] = field(default_factory=dict)  # sensor -> offline since


@dataclass
class Tick:
    t: float  # end of the minute being analysed
    crew: list[CrewView] = field(default_factory=list)
    cabin: CabinView = field(default_factory=CabinView)
    levels: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- small helpers
def _kv_get(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _kv_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, separators=(",", ":"))),
    )


def _mean(
    conn: sqlite3.Connection, subject: str, metric: str, t_from: float, t_to: float
) -> float | None:
    """Mean of a metric over minutes ``t_from <= t_min < t_to``, or None with no samples."""
    row = conn.execute(
        "SELECT SUM(total) AS s, SUM(n) AS n FROM rollup_1m "
        "WHERE subject = ? AND metric = ? AND t_min >= ? AND t_min < ?",
        (subject, metric, int(t_from), int(t_to)),
    ).fetchone()
    if row is None or not row["n"]:
        return None
    return float(row["s"]) / float(row["n"])


def _put_derived(
    conn: sqlite3.Connection, t_min: int, subject: str, metric: str, value: float
) -> None:
    """Store a value analytics computed (one per minute) so History can plot it."""
    conn.execute(
        "INSERT OR REPLACE INTO rollup_1m (t_min, subject, metric, total, min, max, n) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (t_min, subject, metric, value, value, value),
    )


def rmssd(intervals: list[list[float]]) -> tuple[float, int] | None:
    """RMSSD in ms over runs of inter-beat intervals, and the number of pairs used."""
    total, pairs = 0.0, 0
    for run in intervals:
        for a, b in zip(run, run[1:], strict=False):
            total += (b - a) ** 2
            pairs += 1
    if pairs == 0:
        return None
    return math.sqrt(total / pairs), pairs


def _store_beat_pairs(conn: sqlite3.Connection, crew: str, t_min: int) -> None:
    """Sum the squared successive beat differences of one still minute into the rollups.

    Stored as metric ``ibi_ssd`` with ``total`` the sum and ``n`` the number of pairs, so the
    pulse-rate variability (RMSSD) over any window is ``sqrt(sum(total) / sum(n))``.
    """
    rows = conn.execute(
        "SELECT ibi FROM raw_wearable WHERE crew = ? AND t_meas >= ? AND t_meas < ? "
        "AND motion = 0 AND ibi IS NOT NULL ORDER BY t_meas",
        (crew, t_min, t_min + MINUTE),
    ).fetchall()
    beats: list[float] = []
    for row in rows:
        try:
            beats.extend(float(x) for x in json.loads(row["ibi"]))
        except (ValueError, TypeError):
            continue
    pairs = len(beats) - 1
    if pairs < 1:
        return
    ssd = sum((y - x) ** 2 for x, y in zip(beats, beats[1:], strict=False))
    conn.execute(
        "INSERT OR REPLACE INTO rollup_1m (t_min, subject, metric, total, min, max, n) "
        "VALUES (?, ?, 'ibi_ssd', ?, 0, 0, ?)",
        (t_min, crew, ssd, pairs),
    )


def _prv(
    conn: sqlite3.Connection, crew: str, t_from: float, t_to: float, min_pairs: int
) -> float | None:
    """Pulse-rate variability (RMSSD, ms) from still, finger-on beats in the window."""
    row = conn.execute(
        "SELECT SUM(total) AS s, SUM(n) AS n FROM rollup_1m "
        "WHERE subject = ? AND metric = 'ibi_ssd' AND t_min >= ? AND t_min < ?",
        (crew, int(t_from), int(t_to)),
    ).fetchone()
    if row is None or not row["n"] or row["n"] < min_pairs:
        return None
    return math.sqrt(float(row["s"]) / float(row["n"]))


# --------------------------------------------------------------------------- baselines
def _baseline(
    conn: sqlite3.Connection, subject: str, metric: str
) -> tuple[float, float, int] | None:
    row = conn.execute(
        "SELECT ewm_mean, ewm_var, n FROM baseline WHERE subject = ? AND metric = ?",
        (subject, metric),
    ).fetchone()
    if row is None:
        return None
    return float(row["ewm_mean"]), float(row["ewm_var"]), int(row["n"])


def _learn(
    conn: sqlite3.Connection, subject: str, metric: str, x: float, t: float, half_life_min: float
) -> None:
    """Fold one per-minute value into the exponentially weighted mean and variance.

    Early on the weight is ``1/n``, a plain running mean, so a fresh baseline settles quickly;
    after that it decays with the configured half-life.
    """
    current = _baseline(conn, subject, metric)
    if current is None:
        mean, var, n = x, 0.0, 1
    else:
        mean0, var0, n0 = current
        n = n0 + 1
        alpha = max(1.0 - 0.5 ** (1.0 / half_life_min), 1.0 / n)
        delta = x - mean0
        mean = mean0 + alpha * delta
        var = (1.0 - alpha) * (var0 + alpha * delta * delta)
    conn.execute(
        "INSERT INTO baseline (subject, metric, ewm_mean, ewm_var, n, updated) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (subject, metric) DO UPDATE SET "
        "ewm_mean = excluded.ewm_mean, ewm_var = excluded.ewm_var, n = excluded.n, "
        "updated = excluded.updated",
        (subject, metric, mean, var, n, t),
    )


def _bucket(metric: str, hour: int) -> str:
    return f"{metric}:h{hour % 24:02d}"


def _expected(
    conn: sqlite3.Connection, subject: str, metric: str, t: float, p: Params
) -> tuple[float, float, int] | None:
    """The baseline for this time of day.

    People run on a daily rhythm, so each hour of the day keeps its own baseline. The value is
    interpolated between the two nearest hour buckets to avoid a step on the hour. On the first
    day the next hour has not been seen yet, so whichever neighbour is ready is used alone; the
    overall baseline is the last resort, because it hides the daily swing.
    """
    pos = (t % 86400) / 3600.0 - 0.5  # bucket centres sit on the half hour
    lo_h = math.floor(pos)
    frac = pos - lo_h
    lo = _baseline(conn, subject, _bucket(metric, lo_h))
    hi = _baseline(conn, subject, _bucket(metric, lo_h + 1))
    lo_ok = lo is not None and lo[2] >= p.bucket_min_n
    hi_ok = hi is not None and hi[2] >= p.bucket_min_n
    if not (lo_ok and hi_ok):
        if lo_ok:
            return lo
        if hi_ok:
            return hi
        return _baseline(conn, subject, metric)
    assert lo is not None and hi is not None
    mean = lo[0] * (1 - frac) + hi[0] * frac
    var = lo[1] * (1 - frac) + hi[1] * frac
    return mean, var, min(lo[2], hi[2])


def _learn_both(
    conn: sqlite3.Connection, subject: str, metric: str, x: float, t: float, p: Params
) -> None:
    _learn(conn, subject, metric, x, t, p.half_life_min)
    hour = int((t % 86400) // 3600)
    _learn(conn, subject, _bucket(metric, hour), x, t, p.bucket_half_life)


def _changed(
    x: float | None,
    base: tuple[float, float, int] | None,
    sd_floor: float,
    min_delta: float,
    z_event: float,
) -> bool:
    """True when a value sits far from its baseline, in both relative and absolute terms."""
    if x is None or base is None:
        return False
    mean, var, _ = base
    sd = max(math.sqrt(max(var, 0.0)), sd_floor)
    return abs(x - mean) / sd > z_event and abs(x - mean) >= min_delta


# --------------------------------------------------------------------------- state
def _load_state(conn: sqlite3.Connection) -> dict[str, Any]:
    state = _kv_get(conn, STATE_KEY) or {}
    state.setdefault("last_t_min", None)
    state.setdefault("cards", {})
    state.setdefault("crew", {})
    state.setdefault("radiation", {"last_window": None, "streak": 0})
    state.setdefault("skin_since", {})
    state.setdefault("last_summary_hour", None)
    return state


def _since(state: dict[str, Any], domain: str, level: str, t: float) -> float:
    """When the card entered its current level; resets when the level changes."""
    card = state["cards"].get(domain)
    if card is None or card["level"] != level:
        state["cards"][domain] = {"level": level, "since": t}
        return t
    return float(card["since"])


def _debounce(crew_state: dict[str, Any], key: str, flagged: bool, p: Params) -> bool:
    """Turn a noisy per-minute flag into a steady one: on after confirm_min, off after clear_min."""
    on = bool(crew_state.get(f"{key}_on", False))
    hits = int(crew_state.get(f"{key}_hits", 0))
    clear = int(crew_state.get(f"{key}_clear", 0))
    if flagged:
        hits, clear = hits + 1, 0
        if hits >= p.confirm_min:
            on = True
    else:
        hits, clear = 0, clear + 1
        if clear >= p.clear_min:
            on = False
    crew_state[f"{key}_on"], crew_state[f"{key}_hits"], crew_state[f"{key}_clear"] = on, hits, clear
    return on


# --------------------------------------------------------------------------- one minute
def _crew_rows(conn: sqlite3.Connection, t: float) -> list[sqlite3.Row]:
    """Enrolled crew, after enrolling any wearable that has sent data."""
    seen = conn.execute(
        "SELECT crew, MIN(t_meas) AS first_t FROM raw_wearable GROUP BY crew"
    ).fetchall()
    for row in seen:
        conn.execute(
            "INSERT OR IGNORE INTO crew (id, name, enrolled_t) VALUES (?, ?, ?)",
            (row["crew"], row["crew"], float(row["first_t"])),
        )
    return conn.execute("SELECT id, name, enrolled_t FROM crew ORDER BY id").fetchall()


def _cabin_id(conn: sqlite3.Connection) -> str:
    """The cabin node to analyse; ``C1`` before any has reported (the fallback may fill it)."""
    return sensors.cabin_node(conn)


def _analyse_crew(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    t_min: int,
    state: dict[str, Any],
    p: Params,
    th: Thresholds,
) -> CrewView:
    t = float(t_min + MINUTE)
    cid = str(row["id"])
    t_from = t - p.window_min * MINUTE
    learning_ends = float(row["enrolled_t"]) + p.learning_s
    view = CrewView(
        id=cid,
        name=str(row["name"]),
        ready=t >= learning_ends,
        learning_ends_t=None if t >= learning_ends else learning_ends,
    )
    crew_state = state["crew"].setdefault(cid, {})

    _store_beat_pairs(conn, cid, t_min)
    judge_from = t - p.judge_min * MINUTE
    view.hr = _mean(conn, cid, "hr", t_from, t)
    view.spo2 = _mean(conn, cid, "spo2", t_from, t)
    act = _mean(conn, cid, "act", judge_from, t)
    skin = _mean(conn, cid, "skin_c", t_from, t)
    view.prv = _prv(conn, cid, t_from, t, p.min_prv_pairs)
    hr_judge = _mean(conn, cid, "hr", judge_from, t)
    prv_judge = _prv(conn, cid, judge_from, t, p.min_prv_pairs * 3)
    view.resting = act is None or act <= p.act_rest_max

    hr_base = _expected(conn, cid, "hr", t, p)
    prv_base = _expected(conn, cid, "prv", t, p)
    skin_base = _expected(conn, cid, "skin_c", t, p)
    view.hr0 = hr_base[0] if hr_base else None
    view.prv0 = prv_base[0] if prv_base else None
    if skin is not None and skin_base is not None:
        view.skin_resid = skin - skin_base[0]

    if view.prv is not None:
        _put_derived(conn, t_min, cid, "prv", view.prv)
    if view.hr is not None and view.hr0 is not None:
        _put_derived(conn, t_min, cid, "hr_resid", view.hr - view.hr0)

    hr_flag = prv_flag = False
    if view.ready and view.resting:
        hr_flag = _changed(hr_judge, hr_base, p.hr_sd_floor, p.hr_min_delta, p.z_event)
        prv_flag = _changed(prv_judge, prv_base, p.prv_sd_floor, p.prv_min_delta, p.z_event)
        view.hr_changed = _debounce(crew_state, "hr", hr_flag, p)
        view.prv_changed = _debounce(crew_state, "prv", prv_flag, p)
        low = view.spo2 is not None and view.spo2 < p.spo2_red
        view.low_spo2 = _debounce(crew_state, "spo2", low, p)
    else:  # moving or still learning: hold the last judgement, do not make a new one
        view.hr_changed = bool(crew_state.get("hr_on", False)) and view.ready
        view.prv_changed = bool(crew_state.get("prv_on", False)) and view.ready
        view.low_spo2 = bool(crew_state.get("spo2_on", False)) and view.ready

    big = (
        view.hr_changed
        and view.prv_changed
        and hr_base is not None
        and prv_base is not None
        and hr_judge is not None
        and prv_judge is not None
        and abs(hr_judge - hr_base[0]) >= p.red_factor * p.hr_min_delta
        and abs(prv_judge - prv_base[0]) >= p.red_factor * p.prv_min_delta
    )
    if view.low_spo2 or big:
        view.level = "red"
    elif view.hr_changed or view.prv_changed:
        view.level = "yellow"
    if view.level == "green":
        crew_state.pop("since", None)
    else:
        view.since_t = float(crew_state.setdefault("since", t))

    # Learn only from quiet, resting minutes, so an episode never becomes the new normal.
    quiet = view.level == "green" and not hr_flag and not prv_flag and view.resting
    if quiet:
        if hr_judge is not None:
            _learn_both(conn, cid, "hr", hr_judge, t, p)
        if prv_judge is not None:
            _learn_both(conn, cid, "prv", prv_judge, t, p)
    skin_yellow = th.value("thermal.skin_residual_c.yellow")
    if skin is not None and (view.skin_resid is None or abs(view.skin_resid) < skin_yellow):
        _learn_both(conn, cid, "skin_c", skin, t, p)
    return view


def _analyse_cabin(
    conn: sqlite3.Connection, t_min: int, state: dict[str, Any], p: Params, th: Thresholds
) -> CabinView:
    t = float(t_min + MINUTE)
    t_from = t - p.window_min * MINUTE
    cid = _cabin_id(conn)
    cabin = CabinView(id=cid)
    cabin.radiation_streak = int(state["radiation"]["streak"])
    cabin.offline = sensors.offline(conn, cid)

    def read(metric: str) -> float | None:
        # Real readings, with the simulator's filling in for a failed sensor.
        value, simulated = sensors.window_mean(conn, cid, metric, t_from, t)
        if simulated:
            cabin.simulated.add(metric)
        return value

    ppm = read("co2")
    if ppm is not None:
        cabin.co2 = ppm_to_mmhg(ppm, th.cabin_pressure_mmhg)
    cabin.voc = read("voc")
    cabin.temp = read("temp_c")
    cabin.cpm = read("cpm")

    for name, metric in (("co2_0", "co2_mmhg"), ("voc0", "voc"), ("temp0", "temp_c")):
        base = _baseline(conn, cid, metric)
        setattr(cabin, name, base[0] if base else None)

    _radiation(conn, cid, t_min, state, th, cabin)
    return cabin


def _radiation(
    conn: sqlite3.Connection,
    cid: str,
    t_min: int,
    state: dict[str, Any],
    th: Thresholds,
    cabin: CabinView,
) -> None:
    """Poisson test on fixed windows: exceed when N > lambda + k * sqrt(lambda)."""
    window_s = int(th.value("radiation.poisson.window_s"))
    k = th.value("radiation.poisson.k_sigma")
    rad = state["radiation"]
    base = _baseline(conn, cid, "rad_window")
    if base is not None:
        cabin.cpm0 = base[0] * 60.0 / window_s

    window = (t_min + MINUTE) // window_s - 1  # the last complete window
    if rad["last_window"] is not None and window <= rad["last_window"]:
        return
    start, end = window * window_s, (window + 1) * window_s
    # Real counts, or the simulator's while the Geiger counter is offline.
    total, n, sim_counts = sensors.window_sum(conn, cid, "c10s", start, end)
    cpm_total, n_cpm, sim_cpm = sensors.window_sum(conn, cid, "cpm", start, end)
    expected = window_s / 10.0  # one message every 10 s
    if n and n >= expected / 2:
        counts = total * expected / n  # scale for gaps
        simulated = sim_counts
    elif n_cpm and n_cpm >= expected / 2:
        counts = cpm_total / n_cpm * window_s / 60.0
        simulated = sim_cpm
    else:
        return  # not enough of this window arrived to judge it
    rad["last_window"] = window

    if base is None:  # the first full window sets the baseline, simulated or not
        _learn(conn, cid, "rad_window", counts, float(end), 10.0)
        cabin.cpm0 = counts * 60.0 / window_s
        return
    lam = base[0]
    if counts > lam + k * math.sqrt(max(lam, 1.0)):
        rad["streak"] = int(rad["streak"]) + 1
    else:
        rad["streak"] = 0
        if not simulated:  # simulated counts never retrain a real baseline
            _learn(conn, cid, "rad_window", counts, float(end), 10.0)
    cabin.radiation_streak = int(rad["streak"])


def _levels(tick: Tick, state: dict[str, Any], p: Params, th: Thresholds) -> dict[str, str]:
    cabin, t = tick.cabin, tick.t
    levels = {d: "green" for d in DOMAINS}

    levels["cardio"] = max((c.level for c in tick.crew), key=lambda lv: RANK[lv], default="green")

    if cabin.co2 is not None:
        if cabin.co2 >= th.co2_mmhg("red"):
            levels["air"] = "red"
        elif cabin.co2 >= th.co2_mmhg("yellow"):
            levels["air"] = "yellow"
    if levels["air"] == "green" and cabin.voc is not None:
        if cabin.voc >= th.value("air.voc_index.yellow"):
            levels["air"] = "yellow"

    # Thermal is a cabin card. Red: the cabin is outside its band, or crew skin temperature has
    # sat a red-size step from baseline for the hold time. Yellow: the cabin has swung far enough
    # from its own baseline to move skin temperature by the yellow step, or it already has.
    # One crew member's skin change alone is individual and stays off this card.
    lo, hi = th.value("thermal.cabin_temp_c.lo"), th.value("thermal.cabin_temp_c.hi")
    skin_y = th.value("thermal.skin_residual_c.yellow")
    skin_r = th.value("thermal.skin_residual_c.red")
    hold_s = th.value("thermal.skin_residual_c.duration_min") * MINUTE
    resids = [abs(c.skin_resid) for c in tick.crew if c.skin_resid is not None]
    wide = min(p.cabin_wide_crew, max(len(tick.crew), 1))
    if cabin.temp is not None and cabin.temp0 is not None:
        if abs(cabin.temp - cabin.temp0) >= skin_y / p.skin_cabin_gain:
            levels["thermal"] = "yellow"
    if sum(r >= skin_y for r in resids) >= wide:
        levels["thermal"] = "yellow"
    if sum(r >= skin_r for r in resids) >= wide:
        began = float(state["skin_since"].setdefault("crew", t))
        if t - began >= hold_s:
            levels["thermal"] = "red"
    else:
        state["skin_since"].pop("crew", None)
    if cabin.temp is not None and not lo <= cabin.temp <= hi:
        levels["thermal"] = "red"

    red_n = int(th.value("radiation.poisson.red_windows"))
    yellow_n = int(th.value("radiation.poisson.yellow_windows"))
    if cabin.radiation_streak >= red_n:
        levels["radiation"] = "red"
    elif cabin.radiation_streak >= yellow_n:
        levels["radiation"] = "yellow"
    return levels


def _learn_cabin(conn: sqlite3.Connection, tick: Tick, p: Params) -> None:
    """Cabin baselines learn only while their card is green, and only from real readings.

    Simulated values may start a baseline that does not exist yet, so a cabin that has never
    reported still gets numbers to compare against, but they never retrain a real one.
    """
    cabin = tick.cabin
    if cabin.id is None:
        return
    cid = cabin.id

    def learn(metric: str, source: str, value: float) -> None:
        if source in cabin.simulated and _baseline(conn, cid, metric) is not None:
            return
        _learn(conn, cid, metric, value, tick.t, p.half_life_min)

    if tick.levels["air"] == "green":
        # A rise that has not reached the yellow line yet must not become the new normal.
        rising = (
            cabin.co2 is not None
            and cabin.co2_0 is not None
            and cabin.co2 - cabin.co2_0 >= p.cabin_co2_shift_mmhg
        )
        if cabin.co2 is not None and not rising:
            learn("co2_mmhg", "co2", cabin.co2)
        if cabin.voc is not None:
            learn("voc", "voc", cabin.voc)
    if tick.levels["thermal"] == "green" and cabin.temp is not None:
        learn("temp_c", "temp_c", cabin.temp)


# --------------------------------------------------------------------------- attribution
def attribute(tick: Tick, p: Params) -> tuple[str, list[str]]:
    """Cabin, individual or unclear, for a cardio change, and the crew it names."""
    affected = [c.id for c in tick.crew if c.level != "green"]
    ready = [c for c in tick.crew if c.ready]
    cabin = tick.cabin
    cabin_changed = tick.levels["air"] != "green" or tick.levels["thermal"] != "green"
    if cabin.co2 is not None and cabin.co2_0 is not None:
        cabin_changed |= cabin.co2 - cabin.co2_0 >= p.cabin_co2_shift_mmhg
    if cabin.temp is not None and cabin.temp0 is not None:
        cabin_changed |= abs(cabin.temp - cabin.temp0) >= 2.0
    if len(affected) >= 2 and cabin_changed:
        return "cabin", affected
    if len(affected) == 1 and not cabin_changed and len(ready) >= 2:
        return "individual", affected
    return "unclear", affected


# --------------------------------------------------------------------------- status body
def _num(
    label: str, value: float | None, unit: str, baseline: float | None, digits: int = 1
) -> dict[str, Any]:
    return {
        "label": label,
        "value": None if value is None else round(value, digits),
        "unit": unit,
        "baseline": None if baseline is None else round(baseline, digits),
    }


def _facts(
    tick: Tick,
    domain: str,
    th: Thresholds,
    attribution: str | None,
    crew: list[str],
    since_t: float | None,
) -> tpl.Facts:
    cabin = tick.cabin
    shown = next((c for c in tick.crew if c.id in crew), tick.crew[0] if tick.crew else None)
    all_crew = tuple(c.id for c in tick.crew)

    def val(x: float | None, default: float) -> float:
        return default if x is None else x

    hr = val(shown.hr if shown else None, 0.0)
    prv = val(shown.prv if shown else None, 0.0)
    worst_skin = max(
        (abs(c.skin_resid) for c in tick.crew if c.skin_resid is not None), default=0.0
    )
    signed = next(
        (
            c.skin_resid
            for c in tick.crew
            if c.skin_resid is not None and abs(c.skin_resid) == worst_skin
        ),
        0.0,
    )
    return tpl.Facts(
        domain=domain,
        level=tick.levels[domain],
        attribution=attribution,
        crew=tuple(crew),
        all_crew=all_crew or ("W1",),
        since_t=since_t,
        hr=hr,
        hr0=val(shown.hr0 if shown else None, hr),
        prv=prv,
        prv0=val(shown.prv0 if shown else None, prv),
        co2=val(cabin.co2, 0.0),
        co2_0=val(cabin.co2_0, val(cabin.co2, 0.0)),
        temp=val(cabin.temp, 0.0),
        skin_resid=signed,
        cpm=val(cabin.cpm, 0.0),
        cpm0=val(cabin.cpm0, val(cabin.cpm, 0.0)),
        co2_yellow=th.co2_mmhg("yellow"),
        co2_red=th.co2_mmhg("red"),
        temp_lo=th.value("thermal.cabin_temp_c.lo"),
        temp_hi=th.value("thermal.cabin_temp_c.hi"),
        radiation_windows=cabin.radiation_streak,
    )


def _card(
    tick: Tick, domain: str, state: dict[str, Any], p: Params, th: Thresholds
) -> dict[str, Any]:
    level = tick.levels[domain]
    cabin = tick.cabin
    all_crew = [c.id for c in tick.crew]
    attribution: str | None = None
    crew: list[str] = []
    if level != "green":
        if domain == "cardio":
            attribution, crew = attribute(tick, p)
        else:
            attribution, crew = "cabin", all_crew
    since = _since(state, domain, level, tick.t)
    minutes = (tick.t - since) / MINUTE
    facts = _facts(tick, domain, th, attribution, crew, since if level != "green" else None)
    reason = tpl.card_reason(facts)
    name, det_state, detail = tpl.detector(facts, minutes)
    numbers: list[dict[str, Any]]

    if domain == "cardio":
        shown = next((c for c in tick.crew if c.id in crew), tick.crew[0] if tick.crew else None)
        numbers = [
            _num(
                "Pulse-rate variability",
                shown.prv if shown else None,
                "ms",
                shown.prv0 if shown else None,
                0,
            ),
            _num(
                "Resting HR 5-min",
                shown.hr if shown else None,
                "bpm",
                shown.hr0 if shown else None,
                0,
            ),
        ]
        name = "baseline comparison"
        detail = "no change" if level == "green" else f"change began {int(minutes)} min ago"
        if shown is None or shown.hr is None:
            reason = "No wearable data in the last 5 min."
        elif not any(c.ready for c in tick.crew):
            reason = f"Learning the baseline; resting HR {shown.hr:.0f} so far."
    elif domain == "air":
        numbers = [
            _num("CO2", cabin.co2, "mmHg", cabin.co2_0),
            _num("VOC index", cabin.voc, "index", cabin.voc0, 0),
        ]
        voc_limit = th.value("air.voc_index.yellow")
        if cabin.co2 is None and cabin.voc is None:
            reason = "No cabin air data in the last 5 min."
        elif cabin.co2 is None and level == "green":
            voc_base = "" if cabin.voc0 is None else f", baseline {cabin.voc0:.0f}"
            reason = f"No CO2 reading in the last 5 min; VOC index {cabin.voc:.0f}{voc_base}."
        elif level == "yellow" and (cabin.co2 is None or cabin.co2 < th.co2_mmhg("yellow")):
            co2_part = "" if cabin.co2 is None else f"; cabin CO2 {cabin.co2:.1f} mmHg"
            reason = f"VOC index {cabin.voc:.0f}, above the {voc_limit:.0f} yellow line{co2_part}."
            detail = f"VOC index above {voc_limit:.0f} for {int(minutes)} min"
    elif domain == "thermal":
        numbers = [
            _num("Cabin temperature", cabin.temp, "C", cabin.temp0),
            _num("Skin residual", facts.skin_resid, "C", 0.0),
        ]
        if cabin.temp is None:
            reason = (
                "No cabin temperature in the last 5 min; "
                f"skin {facts.skin_resid:+.1f} C from baseline."
            )
    else:
        numbers = [_num("Counts per minute", cabin.cpm, "cpm", cabin.cpm0, 0)]
        if cabin.cpm is None:
            reason = "No radiation proxy counts in the last 5 min."

    return {
        "domain": domain,
        "level": level,
        "reason": reason,
        "action": tpl.card_action(facts),
        "attribution": attribution,
        "crew": crew,
        "numbers": numbers,
        "detector": {"name": name, "state": det_state, "detail": detail},
        "model": None,
        "fallback": _fallback(cabin, domain),
        "_facts": facts,
    }


# Sensors behind each card's numbers (particulates are informational, not shown on a card).
CARD_SENSORS = {"air": ("scd41", "sgp40"), "thermal": ("scd41",), "radiation": ("geiger",)}


def _fallback(cabin: CabinView, domain: str) -> dict[str, Any] | None:
    """The warning for a card fed by an offline sensor, or None when its sensors are fine."""
    down = [s for s in CARD_SENSORS.get(domain, ()) if s in cabin.offline]
    if not down:
        return None
    since = min(cabin.offline[s] for s in down)
    names = " and ".join(sensors.SENSORS[s].name for s in down)
    readings = " and ".join(sensors.SENSORS[s].readings for s in down)
    simulated = any(m in cabin.simulated for s in down for m in sensors.SENSORS[s].metrics)
    when = tpl.clock(since)
    if simulated:
        sentence = f"{names} offline since {when}; {readings} shown are simulated."
    else:
        sentence = f"{names} offline since {when}; no {readings} are arriving."
    return {
        "sensors": [sensors.SENSORS[s].name for s in down],
        "since_t": since,
        "sentence": sentence,
    }


def _nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT node, id, state, last_seen_t FROM node_status ORDER BY node DESC, id"
    )
    return [
        {"node": r["node"], "id": r["id"], "state": r["state"], "last_seen_t": r["last_seen_t"]}
        for r in rows
        if r["node"] in ("wearable", "cabin")
    ]


def build_status(
    conn: sqlite3.Connection, tick: Tick, state: dict[str, Any], p: Params, th: Thresholds
) -> dict[str, Any]:
    cards = [_card(tick, d, state, p, th) for d in DOMAINS]
    by_domain = {c["domain"]: c for c in cards}
    worst = max(WORST_ORDER, key=lambda d: (RANK[tick.levels[d]], -WORST_ORDER.index(d)))
    worst_card = by_domain[worst]
    cardio_facts: tpl.Facts = by_domain["cardio"]["_facts"]

    if worst_card["level"] == "green":
        learning = [c for c in tick.crew if not c.ready and c.learning_ends_t is not None]
        if learning:
            left = max(float(c.learning_ends_t or 0.0) for c in learning) - tick.t
            sentence = tpl.learning_sentence(left)
        elif tick.crew and tick.crew[0].hr is not None:
            sentence = tpl.crew_status_sentence(worst_card["_facts"], cardio_facts, cardio_facts)
        else:
            sentence = "All four domains are normal; 0 alerts."
        crew_status = {
            "level": "green",
            "domain": None,
            "sentence": sentence,
            "attribution": None,
            "action": None,
        }
    else:
        sentence = tpl.crew_status_sentence(worst_card["_facts"], cardio_facts, cardio_facts)
        if worst_card["reason"] != tpl.card_reason(worst_card["_facts"]):
            sentence = worst_card["reason"]  # the card used a no-data or VOC sentence
        crew_status = {
            "level": worst_card["level"],
            "domain": worst,
            "sentence": sentence,
            "attribution": worst_card["attribution"],
            "action": worst_card["action"],
        }

    seen = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, state, src, last_seen_t FROM node_status WHERE node = 'wearable'"
        )
    }
    crew_out = []
    for c in tick.crew:
        node = seen.get(c.id)
        crew_out.append(
            {
                "id": c.id,
                "name": c.name,
                "src": node["src"] if node and node["src"] in ("hw", "sim") else "sim",
                "online": bool(node and node["state"] == "online"),
                "level": c.level,
                "baseline": "ready" if c.ready else "learning",
                "learning_ends_t": c.learning_ends_t,
                "last_seen_t": node["last_seen_t"] if node else None,
            }
        )

    day_ago = tick.t - 86400
    checked = conn.execute(
        "SELECT 1 FROM events WHERE kind = 'checkin' AND t >= ? LIMIT 1", (day_ago,)
    ).fetchone()
    for card in cards:
        del card["_facts"]
    return {
        "t": tick.t,
        "source_mode": "live",
        "thresholds_status": th.status if th.status in ("provisional", "final") else "provisional",
        "crew_status": crew_status,
        "crew": crew_out,
        "cards": cards,
        "nodes": _nodes(conn),
        "correlation": None,
        "checkin_due": checked is None and bool(tick.crew),
    }


# --------------------------------------------------------------------------- side effects
def _record_changes(
    conn: sqlite3.Connection, status: dict[str, Any], previous: dict[str, str], tick: Tick
) -> None:
    """Log an event when a card changes level, and queue a summary for Earth when it turns red."""
    settings = _kv_get(conn, "settings") or {}
    auto = settings.get("auto_approve", {})
    for card in status["cards"]:
        domain, level = card["domain"], card["level"]
        before = previous.get(domain, "green")
        if level == before:
            continue
        if level == "green":
            sentence = (
                f"{tpl.DOMAIN_NAMES[domain]} back to normal after {before}; 0 alerts on this card."
            )
        else:
            sentence = card["reason"]
        crew = card["crew"][0] if domain == "cardio" and len(card["crew"]) == 1 else None
        conn.execute(
            "INSERT INTO events (t, kind, domain, subject, metric, score, reason, simulated) "
            "VALUES (?, 'spike', ?, ?, ?, NULL, ?, 0)",
            (tick.t, domain, crew, None, sentence),
        )
        if level == "red":
            payload = {
                "domain": domain,
                "level": "red",
                "t": int(tick.t),
                "summary": card["reason"],
            }
            body = json.dumps(payload, separators=(",", ":"))
            tier = RED_TIER[domain]
            conn.execute(
                "INSERT OR IGNORE INTO earth_queue "
                "(id, t, tier, summary, payload, bytes, approved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"q-{int(tick.t)}-{domain}",
                    tick.t,
                    tier,
                    card["reason"],
                    body,
                    len(body.encode("utf-8")),
                    1 if auto.get(tier) else 0,
                ),
            )


def _hourly_summary(
    conn: sqlite3.Connection, status: dict[str, Any], state: dict[str, Any], t: float
) -> None:
    hour = int(t // 3600)
    if state["last_summary_hour"] == hour:
        return
    first = state["last_summary_hour"] is None
    state["last_summary_hour"] = hour
    if first:
        return  # wait for a full hour of data before the first summary
    levels = {c["domain"]: c["level"] for c in status["cards"]}
    summary = (
        "Hourly crew summary: "
        + ", ".join(f"{tpl.DOMAIN_NAMES[d]} {levels[d]}" for d in DOMAINS)
        + f"; {len(status['crew'])} crew."
    )
    payload = {
        "type": "hourly_summary",
        "t": int(t),
        "levels": levels,
        "numbers": {c["domain"]: c["numbers"] for c in status["cards"]},
    }
    body = json.dumps(payload, separators=(",", ":"))
    auto = (_kv_get(conn, "settings") or {}).get("auto_approve", {})
    conn.execute(
        "INSERT OR IGNORE INTO earth_queue (id, t, tier, summary, payload, bytes, approved) "
        "VALUES (?, ?, 'crew_only', ?, ?, ?, ?)",
        (
            f"q-{int(t)}-hourly",
            t,
            summary,
            body,
            len(body.encode("utf-8")),
            1 if auto.get("crew_only") else 0,
        ),
    )


def _send_approved(conn: sqlite3.Connection, t: float) -> None:
    """An approved item leaves at the first comms window after its approval."""
    if int(t) % COMMS_WINDOW_S < MINUTE:
        conn.execute("UPDATE earth_queue SET sent = 1 WHERE approved = 1 AND sent = 0")


# --------------------------------------------------------------------------- entry points
def analyse_minute(
    conn: sqlite3.Connection, t_min: int, state: dict[str, Any], p: Params, th: Thresholds
) -> dict[str, Any]:
    """Analyse the minute starting at ``t_min`` and return the status body. The caller commits."""
    tick = Tick(t=float(t_min + MINUTE))
    previous = {d: c["level"] for d, c in state["cards"].items()}
    tick.crew = [_analyse_crew(conn, row, t_min, state, p, th) for row in _crew_rows(conn, tick.t)]
    tick.cabin = _analyse_cabin(conn, t_min, state, p, th)
    tick.levels = _levels(tick, state, p, th)
    _learn_cabin(conn, tick, p)
    status = build_status(conn, tick, state, p, th)
    for card in status["cards"]:
        conn.execute(
            "INSERT OR REPLACE INTO risk "
            "(t_min, subject, domain, level, reason, action, attribution) "
            "VALUES (?, 'cabin', ?, ?, ?, ?, ?)",
            (
                t_min,
                card["domain"],
                card["level"],
                card["reason"],
                card["action"],
                card["attribution"],
            ),
        )
    _record_changes(conn, status, previous, tick)
    _hourly_summary(conn, status, state, tick.t)
    _send_approved(conn, tick.t)
    state["last_t_min"] = t_min
    return status


def process_pending(
    conn: sqlite3.Connection, p: Params | None = None, th: Thresholds | None = None
) -> int:
    """Analyse every complete minute not yet analysed. Returns how many minutes were processed."""
    p = p or Params()
    th = th or load_thresholds()
    state = _load_state(conn)
    row = conn.execute("SELECT MIN(t_min) AS lo, MAX(t_min) AS hi FROM rollup_1m").fetchone()
    if row is None or row["hi"] is None:
        return 0
    last_complete = int(row["hi"]) - MINUTE  # the newest minute may still be filling
    start = int(row["lo"]) if state["last_t_min"] is None else int(state["last_t_min"]) + MINUTE
    start = max(start, last_complete - p.max_catchup_min * MINUTE)
    done = 0
    status: dict[str, Any] | None = None
    for t_min in range(start, last_complete + 1, MINUTE):
        status = analyse_minute(conn, t_min, state, p, th)
        done += 1
        if done % 240 == 0:
            _kv_set(conn, STATE_KEY, state)
            conn.commit()
    if status is not None:
        _kv_set(conn, STATUS_KEY, status)
        _kv_set(conn, STATE_KEY, state)
        conn.commit()
    return done


def run(settings: Settings, period_s: float = 5.0) -> None:
    """Analyse new minutes forever."""
    conn = connect(settings.db_path)
    th = load_thresholds()
    p = Params()
    log.info("analytics started on %s", settings.db_path)
    while True:
        started = time.monotonic()
        try:
            done = process_pending(conn, p, th)
            if done:
                log.info("analysed %d minute(s) in %.2f s", done, time.monotonic() - started)
        except sqlite3.OperationalError as exc:  # a writer held the lock; try again next round
            log.warning("database busy: %s", exc)
            conn.rollback()
        time.sleep(period_s)
