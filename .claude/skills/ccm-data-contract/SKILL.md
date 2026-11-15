---
name: ccm-data-contract
description: Use when reading, producing or validating CCM MQTT messages or /api responses, when a field, topic, unit or timestamp in the Crew Context Monitor is unclear, when real cabin-node firmware messages fail schema validation, or before changing anything under contracts/.
---

# CCM data contract

`contracts/` is the agreement between firmware, simulator, gateway and dashboard. Version is in
`contracts/VERSION` (1.2.0), history in `contracts/CHANGELOG.md`, full field docs in
`contracts/README.md`.

## MQTT topics (`contracts/mqtt/topics.yaml`)

| Topic | Schema | Rate | Retained |
|---|---|---|---|
| `ccm/wearable/telemetry` | `wearable_telemetry` | every 5 s per crew member | no |
| `ccm/cabin/telemetry` | `cabin_telemetry` | every 10 s | no |
| `ccm/wearable/status/{id}`, `ccm/cabin/status/{id}` | `node_status` (`node`, `id`, `state`, `t`) | on connect; last will sets `offline` | yes |
| `ccm/sim/event` | `sim_event` | at each scenario event | no |

All payloads are JSON (D3). Schemas use `additionalProperties: false`.

## Fields that trip people up

| Field | Meaning |
|---|---|
| `t_meas` | When the sample was **measured**. Analysis uses this, so buffered data lands in its true minute (D2). |
| `t_node` | The node's clock at publish time. Debug only. |
| `t_recv` | Set by the gateway on arrival; not in the contract. |
| `src` | `hw` or `sim`. Analytics must ignore it (D9); only the dashboard badge reads it. |
| `crew` | Crew id (`W1`…) on wearable messages; separate from the device `id`. |
| `ibi` | Inter-beat intervals in ms for the window; feeds pulse-rate variability (RMSSD). |
| `motion` | `true` when activity is high; those beats are left out of pulse-rate variability. |
| `co2` | **ppm** on MQTT. The API returns `co2_mmhg` = ppm × 1e-6 × cabin pressure (760 mmHg default). |
| `c10s`, `cpm` | Geiger counts in the last 10 s window, and the rolling 60 s sum. |

## The real cabin firmware does NOT match the contract

`firmware/ccm_cabin_node` (fw 0.3.0) was written against the older draft schema. Gateway ingest
adapts it; **do not "fix" the contract or the firmware to match each other** without a decision.

| Firmware sends | Contract expects | Gateway handling |
|---|---|---|
| No `src`, no `t_meas` | Both required | `src = "hw"`; `t_meas = t_node` if `t_node > 0`, else `t_recv` |
| `t_node = 0` until NTP sets the clock | Unix seconds | Fall back to `t_recv` |
| A sensor's fields **left out** when absent or stale | `co2`, `temp_c`, `rh`, `voc`, `pm1`, `pm25`, `pm10` required | Accept; store NULL |
| Extra `co2_mmhg` | Not allowed | Drop it |
| Status on `ccm/cabin/status` as plain text `online` / `offline` | `ccm/cabin/status/C1`, JSON | Accept both |
| Retained `ccm/cabin/info` debug JSON | Not in contract | Ignore |
| Resends the last 30 s after a reconnect | — | Dedupe on `(id, seq, t_meas)` |

Real examples live in `gateway/tests/fixtures/firmware_cabin.jsonl`. So: validate **simulator**
messages strictly with the schema, and pass **hardware** messages through the adapter.

## API (`contracts/api/*.schema.json`)

All routes are under `/api` (D7) so the dashboard owns every other path. `GET /api/status` is
the big one: `crew_status`, `crew[]`, exactly four `cards` in the fixed order **cardio, air,
thermal, radiation**, `nodes[]`, `correlation`, `checkin_due`, and `source_mode` (`mock` or
`live`, the only field that tells the mock and the real API apart). Since 1.2.0 each card may carry `fallback`: `null` or `{sensors, since_t, sentence}`, set while a sensor behind the card is offline and its readings are simulated (D29). There is no "no data" level. Route behaviors the dashboard
relies on are listed in `docs/handoff-backend.md` section 2.

## Validating

```python
from ccm_sim import contracts
contracts.errors("cabin_telemetry", msg)   # [] when valid; readable messages otherwise
contracts.validate("status", body)         # raises ContractError
```

`python -m ccm_sim.contracts` (or `make contracts`) checks every example in
`contracts/examples/{mqtt,api}/{valid,invalid}`.

## Changing the contract

Follow `contracts/README.md`: a numbered entry in `docs/plan/DECISIONS.md`, a version bump
(minor if additive, major if breaking), a `CHANGELOG.md` line, and in the **same commit** the
schemas, examples, simulator, mock API and regenerated dashboard types (`make types`). Any
change to `contracts/thresholds.yaml` also moves the scrubber scenario, whose CO2 ramp is
defined relative to the red line (D6).
