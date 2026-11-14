---
name: ccm-system-overview
description: Use when starting work on the Crew Context Monitor (CCM) repo, when asked how the system fits together, where a piece of code lives, what runs on the ESP32 vs the Raspberry Pi vs the cloud, or which parts exist yet and which are still planned.
---

# Crew Context Monitor: system overview

CCM tells a crew member whether a change in their vitals comes from **them** or from the
**cabin**. It pairs crew wearables with a cabin sensor node and compares each person to their
own baseline and to the rest of the crew. It is a decision-support prototype, **not a medical
device**, and every screen says so.

## Data flow

```
ESP32 cabin node ──MQTT──▶ mosquitto ─▶ ccm-gateway ingest ─▶ SQLite ◀─ ccm-gateway analytics
ccm-sim (crew W1–W3) ─MQTT─▶  (Pi)                               │        (one pass per minute)
                                                                 ▼
                          dashboard (React) ◀── ccm-gateway api (FastAPI :8000, /api/*)
                                                                 │ uplink (planned)
                                                                 ▼
                          public site on Vercel (planned; falls back to the simulator)
```

Everything downstream of MQTT cannot tell hardware from simulator, except the `src` field
(`hw` / `sim`), which only the crew chips' badge uses.

## Repository map

| Path | What it is | Status |
|---|---|---|
| `contracts/` | MQTT topics and schemas, `/api` schemas, `thresholds.yaml`, replay CSV format | Frozen at v1.1.0 |
| `firmware/ccm_cabin_node/` | ESP32 cabin node (Arduino): SCD41, SGP40, PMS5003, Geiger | Working; see `firmware/README.md` |
| `sim/` (`ccm_sim`) | Simulator, 11 scenarios, expectation oracle, **mock API** | Done |
| `gateway/` (`ccm_gateway`) | Pi services: SQLite store, ingest, analytics, API, uplink | Store + analytics v1 done; ingest, API, uplink in progress |
| `dashboard/` | React + Vite + Recharts app, built once and served as static files | Done |
| `desktop/` | Electron wrapper around the dashboard | Done |
| `deploy/pi/` | Raspberry Pi 4 install script, systemd units, hotspot, NTP | In progress |
| `docs/` | Demo runbook, backend handoff, plan, decisions (`DECISIONS.md` D1–D24) | Reference |

No wearable firmware exists yet; the simulator plays crew W1, W2 and W3.

## Which skill next

| Task | Skill |
|---|---|
| Message fields, topics, units, firmware quirks, changing the contract | `ccm-data-contract` |
| Generating data, scenarios, the mock API, the expectation oracle | `ccm-simulator` |
| SQLite tables, ingest, analytics, card rules, evaluation | `ccm-gateway` |
| Screens, routes, polling, copy rules, generated types | `ccm-dashboard` |

## Running things

There is a root `.venv` (Python 3.12, created with uv) with `sim` and `gateway` installed in
editable mode. GNU Make may not be on PATH on Windows; each Make target is a short command you
can run by hand (read the `Makefile`).

| Goal | Command |
|---|---|
| Dashboard on the mock API at http://127.0.0.1:8000 | `cd dashboard && npm run build`, then `.venv/Scripts/ccm-sim serve-mock --scenario scrubber_failure --speed 60` |
| Fast checks | `make check` (contracts, sim lint/test, `gateway-check`, dashboard, desktop) |
| Gateway checks only | in `gateway/`: `../.venv/Scripts/python -m ruff check src tests`, `-m mypy`, `-m pytest -q` |
| Analytics vs scenarios | in `gateway/`: `../.venv/Scripts/python -m ccm_gateway.evalsim [scenario ...]` |

## Ground rules that span every part

- **Severity comes from measured values only.** Attribution (cabin / individual / unclear)
  changes the sentence, never a level.
- **Copy rules** for every string a person reads: one plain sentence with a number and a
  baseline; no z-scores; "pulse-rate variability", never "HRV"; radiation is always a "proxy";
  "consistent with", never "caused by". Tests enforce them (`ccm_sim.mockapi.copyrules`).
- **Thresholds live only in `contracts/thresholds.yaml`**, all marked `provisional`.
- **CO2 is ppm on MQTT and mmHg in the API.** The conversion happens once, in the API/analytics
  layer: `ccm_sim.units.ppm_to_mmhg` with the cabin pressure from the thresholds file.
- Work happens on `feat/pi-gateway`; agents use worktrees under `.worktrees/` and coordinate
  through MCP Agent Mail.
