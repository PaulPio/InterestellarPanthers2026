---
name: ccm-gateway
description: Use when working on the Crew Context Monitor gateway that runs on the Raspberry Pi (gateway/, ccm_gateway) - the SQLite tables, MQTT ingest, the per-minute analytics that set the cardio/air/thermal/radiation card levels and attribution, the /api service, the cloud uplink, or when a card turns the wrong color or a scenario evaluation fails.
---

# CCM gateway (`gateway/`, package `ccm_gateway`)

Every service is a separate process sharing one SQLite file (WAL mode). Settings come from
`CCM_*` environment variables (`config.py`: `CCM_DB`, `CCM_MQTT_HOST/PORT`, `CCM_API_HOST/PORT`,
`CCM_DASHBOARD_DIST`, `CCM_RAW_RETENTION_DAYS`, `CCM_UPLINK_URL/KEY/PERIOD_S`).

| Module | Job | Status |
|---|---|---|
| `db.py` | Schema, `connect()`, `add_sample()` | Done |
| `adapter.py` + `ingest.py` | MQTT → normalize (incl. firmware quirks) → raw rows + rollups | In progress (StormyRaven) |
| `analytics.py` | One pass per complete minute → card levels, events, `kv['status']` | v1 done |
| `api.py` + `cli.py` | FastAPI over SQLite with the contract's `/api` shapes | In progress (TealSpring) |
| `uplink.py` | Push snapshots to the cloud | Planned |
| `evalsim.py` | Score analytics against the simulator's scenario expectations | Done |

## Tables (`db.py`)

| Table | Holds |
|---|---|
| `raw_wearable`, `raw_cabin` | Every message; primary key `(id, seq, t_meas)`, so resends are ignored |
| `rollup_1m` | `(t_min, subject, metric)` → `total, min, max, n`; **mean = total / n**. Subject is a crew id or a cabin node id |
| `node_status` | Online/offline, `src`, `last_seen_t` per node |
| `crew` | Enrolled crew; analytics enrolls any wearable it sees |
| `baseline` | `(subject, metric)` → EWM mean/variance, `n` |
| `events` | `t, kind, domain, subject, metric, score, reason (the sentence), simulated` |
| `risk` | Card levels per minute |
| `earth_queue` | Summaries waiting for crew approval |
| `kv` | `status` (the full `/api/status` body), `analytics_state`, `settings` |

`add_sample(conn, t_meas, subject, metric, value)` folds one value into its **measured** minute,
so late or buffered data lands where it belongs. Analytics also writes derived per-minute rows
into `rollup_1m`: `prv`, `hr_resid`, and `ibi_ssd` (sum of squared successive beat differences in
`total`, pair count in `n`, so RMSSD over any window is `sqrt(sum(total) / sum(n))`).

## Analytics (`analytics.py`)

`process_pending(conn)` analyses every complete minute after the last one it did (the newest
minute may still be filling), in measured-time order, then writes `kv['status']`. Live data,
replays and full-speed simulator runs all take this same path. `run(settings)` loops it every 5 s.
State that must survive restarts (card "since" times, debounce counters, the radiation streak) is
in `kv['analytics_state']`.

Per minute, `analyse_minute`:

1. **Crew** (`_analyse_crew`): 5-minute means for display; 15-minute means (`judge_min`) for the
   judgement, which averages out beat noise. Baselines are **time-of-day aware**: each metric
   keeps an overall EWM (half-life ~2 days) plus one per hour of day, interpolated between
   neighbouring hours (`_expected`), because heart rate follows a daily rhythm. A change counts
   when it is both > 3 σ from baseline **and** past the detectability line (10 bpm / 8 ms), held
   3 minutes (`_debounce`; it clears after 10). One sign → yellow; both signs at 2× the line, or
   SpO2 < 92 → red. No judgement while moving (activity > 0.3 g) or during the first 2 h after
   enrollment. Baselines learn **only from quiet, resting minutes**, so an episode never becomes
   the new normal.
2. **Cabin** (`_analyse_cabin`, `_radiation`): CO2 converted to mmHg. Radiation uses fixed
   10-minute windows and a Poisson test, exceed when `N > λ + 4√λ`; a partial window is scaled
   if at least half of it arrived.
3. **Levels** (`_levels`), all limits from `contracts/thresholds.yaml`:
   - air: CO2 ≥ 2.5 mmHg yellow, ≥ 3.0 red; VOC index ≥ 250 yellow
   - thermal: cabin outside 18–27 °C red; cabin swung 2 °C from its baseline, or ≥ 2 crew with
     skin ≥ 0.5 °C from baseline, yellow; ≥ 2 crew at ≥ 1.0 °C for 30 min red. One person's skin
     change alone is individual and stays off this card.
   - radiation: one exceeding window yellow, two in a row red
4. **Attribution** (`attribute`), cardio only: ≥ 2 crew changed **and** the cabin changed →
   `cabin`; exactly one changed, cabin steady, ≥ 2 crew ready → `individual`; anything else →
   `unclear`. It picks the sentence, never the color.
5. **Status body** (`build_status`): contract-shaped, sentences from `ccm_sim.mockapi.templates`
   so the copy rules hold; worst card wins the status bar (ties: radiation, air, thermal, cardio).
6. **Side effects**: an event on every level change; an Earth-queue item when a card turns red
   (tier by domain) and an hourly summary; approved items marked `sent` at each 30-minute window.

Tunables are the `Params` dataclass at the top of the file. Change a limit in
`thresholds.yaml`, not in code.

## Evaluating changes

```bash
cd gateway
../.venv/Scripts/python -m pytest -q                         # unit tests (tests/test_analytics.py)
../.venv/Scripts/python -m ccm_gateway.evalsim individual_illness scrubber_failure
../.venv/Scripts/python -m ccm_gateway.evalsim --seeds 5     # every scenario, five seeds (slow)
```

`evalsim` seeds 24 h of `quiet_day` first (so baselines exist), plays the scenario, and at each
oracle checkpoint compares card levels and cardio attribution. Any alert during the seed period
is reported as a false alarm. About 50 s per scenario.

## Common mistakes

- Analysing by arrival time instead of `t_meas`.
- Letting a baseline learn during an episode, so the alert clears itself.
- Using a single minute of heart rate to judge: the simulated (and real) beat noise is ±3 bpm.
- Raising a color from attribution, correlation or a check-in. Only measured values set levels.
- Treating a missing hardware field as zero. Missing is NULL; the card says there's no data.
