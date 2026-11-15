"""Evaluate the analytics against the simulator's scenario expectations.

Each scenario is generated at full speed, preceded by a quiet period that seeds the baselines,
written into a fresh in-memory store and analysed minute by minute. At every checkpoint the
simulator's oracle lists (``ccm-sim scenarios expected``), the card levels and the cardio
attribution are compared with what the analytics produced.

Run ``python -m ccm_gateway.evalsim`` for the table, or pass scenario names.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass

from ccm_sim.engine import build_world, run_world
from ccm_sim.expectations import checkpoints
from ccm_sim.scenario import list_scenarios, load_scenario
from ccm_sim.sinks import MemorySink, Record
from ccm_sim.thresholds import load_thresholds
from ccm_sim.timeutil import DEFAULT_START

from ccm_gateway.analytics import MINUTE, Params, _load_state, analyse_minute
from ccm_gateway.db import add_sample, connect

WEARABLE_METRICS = ("hr", "spo2", "skin_c", "act")
CABIN_METRICS = ("co2", "temp_c", "rh", "voc", "pm25", "cpm")
SEED_HOURS = 24.0
SKIP = {"quiet_week"}  # seven days of data; covered by quiet_day for the false-alarm count


def store_record(conn: sqlite3.Connection, record: Record) -> None:
    """Write one simulator record the way ingest does (contract-valid input only)."""
    topic, p = record.topic, record.payload
    if topic.endswith("wearable/telemetry"):
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_wearable (id, seq, t_meas, crew, src, t_node, t_recv, hr, "
            "spo2, skin_c, act, motion, ibi, batt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p["id"],
                p["seq"],
                p["t_meas"],
                p["crew"],
                p["src"],
                p["t_node"],
                p["t_meas"],
                p["hr"],
                p["spo2"],
                p["skin_c"],
                p["act"],
                int(p["motion"]),
                json.dumps(p["ibi"]),
                p["batt"],
            ),
        )
        if cur.rowcount:
            for metric in WEARABLE_METRICS:
                if p.get(metric) is not None:
                    add_sample(conn, p["t_meas"], p["crew"], metric, float(p[metric]))
    elif topic.endswith("cabin/telemetry"):
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_cabin (id, seq, t_meas, src, t_node, t_recv, co2, temp_c, "
            "rh, voc, pm1, pm25, pm10, cpm, c10s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p["id"],
                p["seq"],
                p["t_meas"],
                p["src"],
                p["t_node"],
                p["t_meas"],
                p.get("co2"),
                p.get("temp_c"),
                p.get("rh"),
                p.get("voc"),
                p.get("pm1"),
                p.get("pm25"),
                p.get("pm10"),
                p.get("cpm"),
                p.get("c10s"),
            ),
        )
        if cur.rowcount:
            for metric in CABIN_METRICS:
                if p.get(metric) is not None:
                    add_sample(conn, p["t_meas"], p["id"], metric, float(p[metric]))
    elif "/status/" in topic:
        conn.execute(
            "INSERT INTO node_status (node, id, state, src, t, last_seen_t) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (node, id) DO UPDATE SET state = excluded.state, t = excluded.t",
            (p["node"], p["id"], p["state"], "sim", p["t"], p["t"]),
        )


def generate(scenario: str, seed: int, t0: float, duration: float | None = None) -> list[Record]:
    world = build_world(load_scenario(scenario), seed, t0, None, duration)
    sink = MemorySink()
    run_world(world, sink, "max")
    return list(sink.records)


@dataclass
class Result:
    scenario: str
    seed: int
    checks: int
    problems: list[str]
    yellow_minutes: int
    red_minutes: int

    @property
    def ok(self) -> bool:
        return not self.problems


def run_scenario(
    scenario: str, seed: int = 1, params: Params | None = None, seed_hours: float = SEED_HOURS
) -> Result:
    """Seed baselines with a quiet period, play the scenario, compare with the oracle."""
    p = params or Params()
    th = load_thresholds()
    conn = connect(":memory:")
    t0 = DEFAULT_START
    seed_start = t0 - seed_hours * 3600.0
    for record in generate("quiet_day", seed + 1000, seed_start, seed_hours * 3600.0):
        if "/status/" not in record.topic:
            store_record(conn, record)
    world = build_world(load_scenario(scenario), seed, t0)
    sink = MemorySink()
    run_world(world, sink, "max")
    for record in sink.records:
        store_record(conn, record)
    conn.commit()

    wanted = {int(t0 + c["offset"]) // MINUTE * MINUTE: c for c in checkpoints(world)}
    state = _load_state(conn)
    row = conn.execute("SELECT MIN(t_min) AS lo, MAX(t_min) AS hi FROM rollup_1m").fetchone()
    problems: list[str] = []
    checks = yellow = red = 0
    for t_min in range(int(row["lo"]), int(row["hi"]), MINUTE):
        status = analyse_minute(conn, t_min, state, p, th)
        if t_min < t0:
            if status["crew_status"]["level"] != "green":
                problems.append(f"seed period not quiet at {(t_min - t0) / 60:.0f} min")
            continue
        level = status["crew_status"]["level"]
        yellow += level == "yellow"
        red += level == "red"
        check = wanted.get(t_min + MINUTE)  # status time is the end of the minute
        if check is None:
            continue
        got = {c["domain"]: c for c in status["cards"]}
        at = f"+{(t_min + MINUTE - t0) / 60:.0f} min"
        for domain, want in check["cards"].items():
            checks += 1
            card = got[domain]
            if card["level"] != want["level"]:
                problems.append(f"{at} {domain}: {card['level']}, expected {want['level']}")
            elif domain == "cardio" and want["level"] != "green":
                if card["attribution"] != want["attribution"]:
                    problems.append(
                        f"{at} cardio attribution: {card['attribution']}, "
                        f"expected {want['attribution']}"
                    )
                elif sorted(card["crew"]) != sorted(want["crew"]):
                    problems.append(f"{at} cardio crew: {card['crew']}, expected {want['crew']}")
    conn.close()
    return Result(scenario, seed, checks, problems, yellow, red)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    seeds = [1]
    if "--seeds" in args:
        i = args.index("--seeds")
        seeds = list(range(1, int(args[i + 1]) + 1))
        del args[i : i + 2]
    names = args or [n for n in list_scenarios() if n not in SKIP]
    failed = 0
    print(f"{'scenario':<24}{'seed':>5}{'checks':>8}{'yellow min':>12}{'red min':>9}  result")
    for name in names:
        for seed in seeds:
            r = run_scenario(name, seed)
            print(
                f"{r.scenario:<24}{r.seed:>5}{r.checks:>8}{r.yellow_minutes:>12}{r.red_minutes:>9}"
                f"  {'pass' if r.ok else 'FAIL'}"
            )
            for problem in r.problems[:8]:
                print(f"    {problem}")
            failed += not r.ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
