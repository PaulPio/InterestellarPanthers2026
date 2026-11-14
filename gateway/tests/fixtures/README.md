# Fixtures

`firmware_cabin.jsonl` holds messages in the exact shape the cabin node firmware
(`firmware/ccm_cabin_node`, fw 0.3.0) builds in `buildTelemetry`: no `src`, no `t_meas`, an extra
`co2_mmhg`, `t_node` of 0 when the clock is not set, and a sensor's fields left out when it is
absent or stale. The firmware also publishes plain-text `online` / `offline` to `ccm/cabin/status`
and a JSON blob to `ccm/cabin/info`.
