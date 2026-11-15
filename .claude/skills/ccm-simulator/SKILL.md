---
name: ccm-simulator
description: Use when generating fake crew or cabin data for the Crew Context Monitor, running or editing a scenario (scrubber_failure, individual_illness, solar_particle_event...), serving the mock API, replaying recorded data, publishing simulated crew next to real hardware, or checking analytics against scenario expectations.
---

# CCM simulator (`sim/`, package `ccm_sim`, command `ccm-sim`)

The simulator publishes exactly what the hardware publishes, onto the same topics, so the
pipeline can be built and rehearsed without sensors. It has three jobs:

1. **Data source**: three crew (W1–W3) and a cabin node, down to individual heartbeats.
2. **Oracle**: every scenario states what a correct pipeline should show (`expect:`).
3. **Mock API**: serves the dashboard's `/api` routes from a running scenario.

## Commands

| Goal | Command |
|---|---|
| List scenarios | `ccm-sim scenarios list` |
| Write a scenario to a file at full speed | `ccm-sim generate --scenario scrubber_failure --seed 1 --out data/s.jsonl` |
| Publish live to a broker, 60× real time | `ccm-sim run --scenario scrubber_failure --speed 60 --broker localhost:1883` |
| Simulated crew beside the **real** cabin node | `ccm-sim run --scenario quiet_day --crew W1,W2,W3 --no-cabin --broker <pi>:1883` |
| Mock API + built dashboard on :8000 | `ccm-sim serve-mock --scenario individual_illness --speed 120` |
| Record a broker, replay it later | `ccm-sim record --broker ... --out f.csv` / `ccm-sim replay --file f.csv --speed max --broker ...` |
| Check every scenario's expectations are self-consistent | `ccm-sim scenarios check` |
| Print expected card states at checkpoints, as JSON | `ccm-sim scenarios expected [names]` |

Without `--broker`, `run` prints JSON lines. `--speed` is simulated seconds per real second, or
`max`. The same `--seed` gives byte-identical output; each crew member and the cabin have their
own named random stream, so adding a crew member changes nobody else's numbers.

`--no-cabin` drops only the cabin's **messages**: the cabin model still runs, because it drives
the crew's physiology. Without it, simulated cabin readings would collide with the real node.

## How a run is built (`engine.py`)

`build_world(scenario, seed, t0, crew, duration)` returns a `World`: the crew physiology models
(`physiology.py`, parameters in `sim/params/physiology.yaml`), the cabin model (`cabin.py`,
`sim/params/cabin.yaml`), node wrappers that turn them into messages (`messages.py`), the
scenario's effects resolved into a `Truth` timeline (`truth.py`, `effects.py`), and the event
list. `run_world(world, sink, speed)` publishes in measured-time order through a `Publisher`
into a sink: `MemorySink`, `JsonlSink`, `MqttSink` (one MQTT client per node, each with its own
last will), or `CsvSink`.

```python
from ccm_sim.engine import build_world, run_world
from ccm_sim.scenario import load_scenario
from ccm_sim.sinks import MemorySink
from ccm_sim.timeutil import DEFAULT_START

world = build_world(load_scenario("scrubber_failure"), seed=1, t0=DEFAULT_START)
sink = MemorySink()
run_world(world, sink, "max")
for record in sink.records:        # Record(topic, payload, retain, ...)
    ...
```

## Scenarios (`sim/scenarios/*.yaml`)

`quiet_day`, `quiet_week` (7-day baseline pre-seed), `scrubber_failure`,
`individual_illness`, `coincident_trends`, `solar_particle_event`, `sleep_debt_drift`,
`exercise_block`, `thermal_fault`, `wifi_dropout`, `ble_dropout`, plus the `demo` playlist.

A scenario has `events` (each with `effects` on targets such as `cabin.co2`, `crew.*.rmssd`,
`crew.W2.hr`, using ops `set`, `step`, `ramp`, `drift_per_day`, `coupled`, `exercise`, `rate`
and `silence`), optional `marks` (named moments
such as "CO2 passes yellow"), and an `expect:` block. Effects can reference thresholds
(`{threshold: air.co2.red, factor: 1.3}`), so editing `contracts/thresholds.yaml` moves the
scenario with it. Schema: `sim/scenarios/scenario.schema.json`.

## The expectation oracle (`expectations.py`)

`expect:` entries say what a correct pipeline shows: `{card: air, level: red, from: ..., until:
...}`, `{card: cardio, level: yellow, attribution: individual, crew: [W2]}`, `{quiet: [cardio]}`,
`{correlation: ...}`, `{node: C1, state: offline}`. Times are durations (`3h`), mark references
(`mark:co2_passes_yellow + 75m`) or truth conditions (`cabin.co2 >= air.co2.yellow`).
`checkpoints(world)` turns them into concrete times with the expected level, attribution and crew
of every card. The gateway's `ccm_gateway.evalsim` scores analytics against exactly this.

The cardio detectability line is provisional (D24): 8 ms of pulse-rate variability or 10 bpm of
resting heart rate. `coincident_trends` is deliberately below it and expects nothing.

## The mock API (`mockapi/`)

`MockState` runs a scenario in a background thread and rolls it up per minute; `create_app`
serves every contract route plus `/api/_mock/*` (mock only). **It detects nothing**: card levels
and attribution come straight from the scenario's `expect:` block; numbers come from the
generated data; sentences come from `templates.py`. `MockState.advance_to(t)` runs the scenario
synchronously to any simulated time, which is what a serverless deployment would use.
`mockapi/static.py::mount_dashboard` serves `dashboard/dist` with SPA fallback; the real gateway
API reuses it unchanged. `templates.py` and `copyrules.py` are also reused by the gateway.
