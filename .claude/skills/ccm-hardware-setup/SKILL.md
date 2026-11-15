---
name: ccm-hardware-setup
description: Use when wiring, flashing, connecting or debugging the Crew Context Monitor hardware - the Raspberry Pi 4 gateway and its two ESP32 nodes (the cabin node and the wearable) - including WiFi/MQTT/NTP setup, pin maps, Arduino settings, secrets.h, sensor checks, or when a node's data does not reach the Pi or the dashboard.
---

# CCM hardware: the Raspberry Pi and its two ESP32 nodes

```
Cabin node  (ESP32 DevKitC)  ──WiFi, MQTT every 10 s──┐
                                                       ├──▶ Raspberry Pi 4: mosquitto :1883 ─▶ ccm-gateway
Wearable W1 (ESP32-S3)       ──WiFi, MQTT every 5 s ──┘     chrony :123 (clock for both nodes)
```

Both nodes publish JSON to the Pi's MQTT broker and take their clock from the Pi's time server,
both at the **same address** (`MQTT_HOST`). The Pi stores, analyses and serves the dashboard on
port 8000; the public website reads it through Tailscale Funnel (`deploy/cloud/README.md`). It is a
decision-support prototype, not a medical device.

## Network: choose one setup

| Setup | WiFi | Pi address | What the nodes need |
|---|---|---|---|
| **Pi as the hotspot** (`deploy/pi/hotspot.sh`) | the SSID in `/etc/ccm/hotspot.env` | `192.168.4.1` | WiFi details only: both default to the hotspot address |
| **Shared WiFi** (e.g. a phone hotspot) | that WiFi | `hostname -I` on the Pi | WiFi details **and** `MQTT_HOST` = the Pi's address |

- On a shared WiFi the **gateway is the router or phone, not the Pi**. The wearable's default
  (the gateway address) then points at the wrong device: always set `MQTT_HOST`.
- The Pi's address on a shared WiFi can change after a reconnect. If nodes stop arriving, check
  `hostname -I`, update `MQTT_HOST` and reflash.
- The Pi's own hotspot takes over `wlan0`, so the Pi then needs Ethernet or a USB WiFi adapter for
  internet. Running the hotspot over the only connection cuts SSH.

**Secrets**: WiFi names, passwords and the Pi's address go only in each node's `secrets.h`, which
git ignores (`firmware/**/secrets.h`). Commit only `secrets.example.h` with placeholders. Before
committing, check that `git diff --cached` contains neither.

## Ports on the Pi

| Port | Service | Reachable from |
|---|---|---|
| 1883 | mosquitto (MQTT, anonymous: demo LAN only) | the nodes' WiFi |
| 123/udp | chrony (time for the nodes; allows private address ranges) | the nodes' WiFi |
| 8000 | `ccm-api`: dashboard and `/api` | the WiFi (`http://<pi>:8000`) |
| 8001 | `ccm-public`: read-only API with a key | loopback only; Tailscale Funnel publishes it |
| 22 | SSH | the WiFi |

Never expose 1883 or 8000 to the internet.

## Cabin node: `firmware/ccm_cabin_node/` (ESP32 DevKitC)

- **Arduino**: board *ESP32 Dev Module*. Libraries: Sensirion I2C SCD4x, Adafruit SGP40,
  PubSubClient, Adafruit SSD1306 (+ GFX, BusIO, Sensirion Core).
- **`secrets.h`**: `WIFI_SSID`, `WIFI_PASS`, and on a shared WiFi `MQTT_HOST` and `MQTT_ENABLED 1`
  (the default is 0: it never contacts the broker).

| Signal | GPIO | Notes |
|---|---|---|
| I2C SDA / SCL | 18 / 19 | SCD41 `0x62`, SGP40 `0x59`, optional OLED `0x3C`; 100 kHz |
| PMS5003 TX → UART2 RX | 15 | 9600 baud, 3.3 V logic; GPIO15 is a boot strapping pin |
| UART2 TX | 17 | leave unconnected |
| Geiger pulse out | 27 | falling edge; **measure the pulse voltage first**, divide it if it swings to 5 V |
| 5 V (VIN) | | PMS5003 and Geiger module |
| 3V3 | | SCD41, SGP40, OLED |

- **Power** from a 5 V 2 A USB supply, not a laptop port.
- **Geiger safety**: the module makes about 400 V; keep it covered.
- **SCD41 placement**: put it by a vent, away from the ESP32, which runs warm (readings near 28 °C
  usually mean it sits too close). The SCD41 settles in about a minute and the SGP40 in about an
  hour.
- **Messages**: `ccm/cabin/telemetry` every 10 s, without `src` or `t_meas`. Absent or stale
  sensors are left out; `c10s` is always sent. Status is plain text on `ccm/cabin/status`, and
  debug JSON goes to `ccm/cabin/info`.
- **Remote serial monitor**: `http://<node ip>/` or `http://ccm-cabin.local/`.

## Wearable: `firmware/ccm_wearable/` (ESP32-S3 SuperMini or XIAO ESP32-S3)

- **Arduino**: board *ESP32S3 Dev Module*, **USB CDC On Boot: Enabled**, Flash Size 4MB.
  Libraries: SparkFun MAX3010x, PubSubClient.
- **`secrets.h`**: `WIFI_SSID`, `WIFI_PASS`, and on a shared WiFi `MQTT_HOST` (the same address
  is used for the clock). Without it, it uses the WiFi gateway (right only for the Pi hotspot).

| Signal | GPIO | Notes |
|---|---|---|
| I2C SDA / SCL | 3 / 4 | MAX30102 `0x57`, TMP117 `0x48`-`0x4B`, MPU6050 `0x68`/`0x69` |
| MAX30102 INT | 1 | wired, not used |
| 3V3, GND | | all three sensors |

- If nothing answers on GPIO 3/4, it searches pads 1 to 13 and prints the pins that work. Nothing
  answering anywhere means power or ground.
- **Messages**: `ccm/wearable/telemetry` every 5 s: `hr`, `spo2`, `skin_c`, `act` (m/s²), `motion`,
  `ibi`. `hr`/`spo2` are `null` and `"finger":false` is added with no finger. Status is plain text
  on `ccm/wearable/status`.
- **Tuning**: `FINGER_IR_MIN` (finger detection) and `LED_BRIGHTNESS` (lower it if the infrared
  reading saturates near 262143). The MAX30102 needs firm, still contact: a finger clip works best.

## How the Pi adapts each node (`gateway/src/ccm_gateway/adapter.py`)

| Quirk | Handling |
|---|---|
| No `src`, `crew`, `t_meas` | `src = "hw"`, `crew = id`; `t_meas = t_node` once it is clock time (> 1.7e9), else the Pi's receive time |
| Cabin sends `co2_mmhg`, omits stale sensors | `co2_mmhg` is dropped; missing values are stored as null |
| Wearable `act` in m/s² | divided by 9.80665 (the contract and analytics use g) |
| Wearable `skin_c`, `act`, `batt` may be null | accepted as null |
| Wearable `finger:false` (no finger on the pulse sensor) | `skin_c` is the board's own heat (3–4 °C above the room): stored raw, not used as skin temperature |
| Plain-text status with no id | `ccm/cabin/status` → C1, `ccm/wearable/status` → W1 |
| Resends after a reconnect | dropped as duplicates on `(id, seq, t_meas)` |

## Checking it works

```bash
mosquitto_sub -h 127.0.0.1 -t 'ccm/#' -v      # on the Pi: both nodes' messages
chronyc clients                               # both nodes should appear (they ask for the time)
curl -s http://127.0.0.1:8000/api/status      # nodes C1 and W1 online; W1 src "hw"
systemctl status ccm-ingest ccm-analytics ccm-api ccm-public
journalctl -u ccm-ingest -n 50                # "Rejected MQTT message ..." lines name the reason
```

The node's Serial Monitor (115200 baud) shows WiFi and MQTT state every second. A new crew
member's baseline learns for `CCM_LEARNING_S` (900 s) before cardiovascular alerts.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Node says `MQTT: broker not reachable` or `connect failed` | `MQTT_HOST` wrong or missing on a shared WiFi; Pi address changed; cabin `MQTT_ENABLED 0` |
| Messages arrive but nothing on the dashboard | `journalctl -u ccm-ingest`: a rejected message says why |
| `t_node` stays 0 (cabin) or small (wearable) | the Pi's time server is not reachable at `MQTT_HOST`; check `chronyc clients` |
| Cardio card: "no finger on the pulse sensor" | infrared below `FINGER_IR_MIN`: press the finger on firmly, or tune the threshold |
| A sensor missing in the node name or the Serial line | wiring or power to that sensor; I2C address list above |
| Readings stop after a while on a phone hotspot | the phone dropped an idle device, or the Pi's address changed |
