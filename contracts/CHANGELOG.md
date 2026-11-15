# Contract changelog

Versioning: minor bump for additive changes, major bump for breaking ones.

## 1.2.0 (2026-11-14)

- Additive: optional `fallback` on each `/api/status` card (`{sensors, since_t, sentence}` or null), set while a sensor behind the card is offline and its readings come from the simulator (D29). No new level; existing producers that omit it stay valid.

## 1.1.0 (2026-11-14)

- Additive: `replay_csv.md` defines the record and replay file format (`topic,t_meas,retain,payload_json`). No schema changes.

## 1.0.0 (2026-11-14)

- Initial frozen contract: MQTT topics and four message schemas (wearable telemetry, cabin telemetry, node status, simulator event), eleven dashboard API schemas, provisional thresholds, CO2 ppm to mmHg conversion.
