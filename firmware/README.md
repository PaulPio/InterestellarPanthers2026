# Firmware

Two ESP32 nodes talk to the Raspberry Pi over WiFi and MQTT: the cabin node (`ccm_cabin_node/`, this first part) and the wearable (`ccm_wearable/`, [below](#wearable-node-firmware)). How they connect to the Pi is in the `ccm-hardware-setup` skill and `deploy/pi/README.md`.

# Cabin node firmware

Firmware for the cabin (environmental) node: an ESP32 DevKitC with an SCD41 (CO2, temperature,
relative humidity), an SGP40 (VOC index), a PMS5003 (particulates), a Geiger pulse counter and an
optional SSD1306 OLED. It publishes one JSON telemetry message every 10 s to the Pi's MQTT broker.

**Decision-support prototype, not a safety or medical device.**

Firmware by `defranco-sys`, uploaded on the `dev-code` branch as `ccm_cabin_node.zip`.

## Build and flash (Arduino IDE)

1. Install the Arduino IDE and its ESP32 board support (`arduino-esp32` core); the sketch targets
   the **ESP32 Dev Module** board.
2. Install these libraries from the Library Manager:
   - Sensirion I2C SCD4x (>= 1.0)
   - Adafruit SGP40
   - PubSubClient
   - Adafruit SSD1306
   - their dependencies: Adafruit GFX, Adafruit BusIO and Sensirion Core
3. Copy `secrets.example.h` to `secrets.h` in the same folder and fill in the Pi hotspot details.
   `secrets.h` is git-ignored:

   ```c
   #define WIFI_SSID "ccm-pi"
   #define WIFI_PASS "change-me"
   ```

4. Open `firmware/ccm_cabin_node/ccm_cabin_node.ino`, select **Tools > Board > ESP32 Dev Module**
   and the board's serial port, then upload.
5. Open **Tools > Serial Monitor** at 115200 baud. The sketch prints a live sensor line once a
   second and, with `SERIAL_JSON_LOG`, each telemetry message as it is sent.

## Pin map (`config.h`)

| Signal | GPIO | Notes |
|---|---|---|
| I2C SDA | 18 | SCD41, SGP40 and the optional OLED share this bus |
| I2C SCL | 19 | I2C runs at 100 kHz |
| PMS5003 TX -> UART2 RX | 15 | UART2 at 9600 baud, 3.3 V logic; GPIO15 (D15) is a boot strapping pin |
| UART2 TX | 17 | Leave unconnected |
| Geiger pulse out | 27 | Active-low falling edge by default |

I2C addresses: `0x62` SCD41 (CO2), `0x59` SGP40 (VOC), `0x3C` OLED (optional). The sketch scans the
bus at boot and prints every address that answers; a sensor plugged in late is picked up again
without a reset.

## Settings a teammate changes (`config.h`)

| Setting | Default | What it does |
|---|---|---|
| `MQTT_ENABLED` | `0` | `0` never contacts the broker. Leave it at 0 until the Pi is set up: each failed attempt blocks the node (and the remote monitor) for about 3 s. Set to 1 for the real system. |
| `MQTT_HOST` | `"192.168.4.1"` | The Pi's address on its hotspot; `MQTT_PORT` is 1883. The Pi is also the NTP server (`NTP_SERVER`), so the node's clock needs it. |
| `GEIGER_ENABLED` | `1` | Set to 0 if the Geiger module has not arrived; `cpm` and `c10s` are then omitted. |

Other values in the same file: `NODE_ID` (`"C1"`), `FW_VERSION` (`"0.3.0"`), `REPORT_MS` (10000),
`REMOTE_MONITOR` (1), `REMOTE_HOSTNAME` (`"ccm-cabin"`), `PUBLISH_CO2_MMHG` (1) and
`CABIN_PRESSURE_MMHG` (760.0).

## Remote serial monitor

Every line the sketch prints goes to USB serial **and** into an 8 KB ring buffer (about the last 70
sensor lines). With `REMOTE_MONITOR` on, the node serves that buffer as a read-only page:

- `http://<node IP>/` (the IP is printed on USB serial as `[web] ...` and on the OLED)
- `http://ccm-cabin.local/` via mDNS

The page polls `/log?from=<position>` once a second and offers autoscroll and clear. There is no
password: anyone on the same WiFi can read the log.

## What it publishes

The node publishes to three topics:

| Topic | Retained | Contents |
|---|---|---|
| `ccm/cabin/telemetry` | no | One JSON sample every 10 s (`REPORT_MS`) |
| `ccm/cabin/status` | yes | `online` on connect, `offline` as the MQTT last will |
| `ccm/cabin/info` | yes | Debug JSON on connect: firmware, IP, which sensors answered |

With `MQTT_ENABLED` at 0, none of these are published.

A telemetry message looks like this (from the sketch's header comment):

```json
{"node":"cabin","id":"C1","seq":902,"t_node":1760000000,
 "co2":812,"co2_mmhg":0.62,"temp_c":22.1,"rh":41,"voc":105,
 "pm1":2,"pm25":4,"pm10":6,"cpm":18,"c10s":3}
```

- `node` is always `"cabin"`, `id` is `NODE_ID`, and `seq` increments by one per message.
- `t_node` is the epoch second at which the sample was measured. It is `0` when the node's clock was
  never set; ingest should then fall back to its own receive time.
- `co2` is the SCD41's native ppm. `co2_mmhg` is included only with `PUBLISH_CO2_MMHG`: ppm x 1e-6
  x `CABIN_PRESSURE_MMHG`.
- `temp_c` and `rh` come from the SCD41 and are omitted once its sample is older than 30 s.
- `voc` appears only when the SGP40 is present and its index is above 0 (the index is 0 during the
  sensor's roughly one-minute start-up).
- `pm1`, `pm25` and `pm10` appear only while a checksum-valid PMS5003 frame is less than 10 s old.
- `c10s` is the pulse count in the current 10 s window; `cpm` is the rolling 60 s sum and appears
  only after six full windows.

A sensor that is absent or stale has its fields left out, never faked.

The retained info message has the same shape each time:

```json
{"id":"C1","fw":"0.3.0","ip":"192.168.4.15","scd41":true,"sgp40":true,"oled":false,"geiger":true}
```

The node queues up to 64 readings (about 10 minutes offline). After a reconnect it resends anything
published in the previous 30 s, so ingest must tolerate a repeated `(id, seq, t_node)`.

## Safety notes from the source

- **Measure the Geiger pulse voltage before wiring it.** GPIO27 expects 3.3 V logic; use a divider if
  the pulse swings to 5 V (`config.h`).
- The remote serial monitor is read-only but has no password, so anyone on the network can read it.
- This is a decision-support prototype, not a safety or medical device.

# Wearable node firmware

Firmware for the wearable (`ccm_wearable/`): an ESP32-S3 (SuperMini HW-747, or a XIAO ESP32-S3
with the same GPIO numbers) with a MAX30102 (pulse, SpO2 and beat-to-beat intervals), a TMP117
(skin temperature) and an MPU6050 (motion). It publishes one JSON message every 5 s to the Pi's
MQTT broker over WiFi. BLE is in the code but off (`USE_BLE 0`).

**Prototype for a hackathon demo, not a medical device.**

Firmware by Adrian Morton, uploaded to `master` as `ccm_wearable.ino`. The copy here differs only
in moving the WiFi details to `secrets.h` and adding the optional `MQTT_HOST`.

## Build and flash (Arduino IDE)

1. Board **ESP32S3 Dev Module**, **USB CDC On Boot: Enabled** (Serial over the USB-C port),
   **Flash Size: 4MB**.
2. Libraries: **SparkFun MAX3010x Pulse and Proximity Sensor Library** and **PubSubClient**. The
   TMP117 and MPU6050 are read with plain I2C, so they need none.
3. Copy `secrets.example.h` to `secrets.h` in `firmware/ccm_wearable/` and fill it in. `secrets.h`
   is git-ignored: never commit it.

   ```c
   #define WIFI_SSID "your-wifi"
   #define WIFI_PASS "your-password"
   #define MQTT_HOST "192.168.1.50"   // the Pi's address; leave out when the Pi is the hotspot
   ```

4. Open `firmware/ccm_wearable/ccm_wearable.ino`, pick the board's port and upload. The Serial
   Monitor (115200 baud) prints every sensor once a second and each message as `SENT> {...}`.

## Wiring

| Signal | GPIO | Goes to |
|---|---|---|
| I2C SDA | 3 | MAX30102, TMP117, MPU6050 |
| I2C SCL | 4 | MAX30102, TMP117, MPU6050 |
| MAX30102 INT | 1 | wired, not used (the firmware polls) |
| 3V3, GND | | all three sensors |

I2C addresses: `0x57` MAX30102, `0x48`-`0x4B` TMP117, `0x68`/`0x69` MPU6050. If nothing answers on
GPIO 3/4, the firmware tries every pair of pads 1 to 13 and prints the pins that work. A missing
sensor is added to the node name (`CCM-W1-noTMP`) and its fields are sent as `null`.

## Settings at the top of the sketch

| Setting | Default | What it does |
|---|---|---|
| `NODE_ID` | `"W1"` | Crew id; the Pi shows it on the crew chip |
| `FINGER_IR_MIN` | `50000` | Below this infrared level there is no finger. Tune on the bench |
| `ACT_MOTION_THRESH` | `0.50` | m/s²; activity above it sets `motion: true` |
| `LED_BRIGHTNESS` | `0x3F` | Lower it if the infrared reading saturates near 262143 |
| `PRINT_EVERY_SECOND` | `1` | One Serial line per second with all readings |

## What it publishes

| Topic | Retained | Contents |
|---|---|---|
| `ccm/wearable/telemetry` | no | One JSON message every 5 s |
| `ccm/wearable/status` | yes | `online` on connect, `offline` as the MQTT last will |

```json
{"node":"wearable","id":"W1","seq":12,"t_node":1794715000,"hr":72,"spo2":97,"skin_c":33.41,
 "act":0.214,"motion":false,"ibi":[830,845,812,861,840],"batt":null}
```

- `hr` and `spo2` are `null` with no finger, and `"finger":false` is added; `ibi` is then empty.
- `act` is the standard deviation of acceleration over the 5 s window, in **m/s²**. The Pi
  converts it to g.
- `t_node` is clock time once the Pi's time server answers, otherwise seconds since power-on (the
  Pi then uses its own receive time).
- The last 60 s of messages are kept and resent after a reconnect; the Pi drops duplicates.
