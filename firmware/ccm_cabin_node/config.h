// Crew Context Monitor - cabin (environmental) node - build configuration
// Everything a teammate should need to touch lives in this file.
#pragma once

// ---------- Identity ----------
#define NODE_ID            "C1"
#define FW_VERSION         "0.3.0"

// ---------- Network (the Pi's hostapd hotspot) ----------
// WIFI_SSID and WIFI_PASS live in secrets.h, which git ignores.
// Copy secrets.example.h to secrets.h and fill it in.
#include "secrets.h"
#define MQTT_HOST          "192.168.4.1"     // TODO: the Pi's address on its own hotspot
#define MQTT_PORT          1883
// The node keeps measured time, so it needs a clock. The Pi is offline, so the
// Pi itself must serve NTP on the hotspot (chrony with an "allow" line).
// Until the clock is set, t_node is sent as 0 and ingest should use t_recv.
#define NTP_SERVER         MQTT_HOST

// ---------- Remote serial monitor over WiFi ----------
// With WIFI_SSID / WIFI_PASS set to any WiFi your laptop or phone is also on, open
// http://<node IP>/ or http://ccm-cabin.local/ in a browser to see the same lines the
// USB serial monitor shows. The IP is printed on USB serial ("[web] ...") and on the OLED.
#define REMOTE_MONITOR     1
#define REMOTE_HOSTNAME    "ccm-cabin"
#define LOG_BUFFER_BYTES   8192       // about the last 70 sensor lines are kept for late joiners

// 0 = do not try to reach the MQTT broker. Leave at 0 until the Pi is set up: every failed
// attempt freezes the node (and the remote monitor) for about 3 s. Set to 1 for the real system.
#define MQTT_ENABLED       0

// ---------- MQTT topics (data contract) ----------
#define TOPIC_TELEMETRY    "ccm/cabin/telemetry"
#define TOPIC_STATUS       "ccm/cabin/status"   // retained "online" / "offline" (last will)
#define TOPIC_INFO         "ccm/cabin/info"     // retained, debug only: fw, ip, sensors found

// ---------- Pins (ESP32 DevKitC, from the onboarding doc pin map) ----------
#define PIN_I2C_SDA        18
#define PIN_I2C_SCL        19
#define PIN_PMS_RX         15    // UART2 RX  <- PMS5003 TX (3.3 V logic). GPIO15 = D15, a boot strapping pin
#define PIN_PMS_TX         17    // UART2 TX, leave unconnected
#define PIN_GEIGER         27    // Geiger pulse out. MEASURE IT FIRST: divider if it swings to 5 V

// ---------- I2C ----------
#define I2C_HZ             100000
#define ADDR_SCD41         0x62
#define ADDR_SGP40         0x59
#define ADDR_OLED          0x3C

// ---------- Geiger ----------
#define GEIGER_ENABLED     1          // 0 if the module has not arrived: cpm and c10s are omitted
#define GEIGER_EDGE        FALLING    // active-low pulse on CAJOE-style boards; RISING if yours idles low
#define GEIGER_PIN_MODE    INPUT      // INPUT_PULLUP if the output is open-collector
#define GEIGER_DEBOUNCE_US 300        // ignore edges closer together than this (ringing)

// ---------- Timing ----------
#define REPORT_MS          10000      // one telemetry message every 10 s
#define CPM_WINDOWS        6          // 6 x 10 s = 60 s rolling sum
#define SCD_STALE_MS       30000      // omit co2/temp_c/rh if no fresh SCD41 sample for this long
#define PMS_STALE_MS       10000      // omit pm fields if no checksum-valid frame for this long

// ---------- Serial monitor (115200 baud) ----------
#define SERIAL_SENSOR_LOG  1          // 1 = print a line of live sensor values
#define SERIAL_SENSOR_MS   1000       // how often; use a multiple of 1000
#define SERIAL_JSON_LOG    1          // 1 = also print each 10 s telemetry message as sent

// ---------- Store and forward ----------
#define QUEUE_LEN          64         // 60 messages = 10 min offline, plus the unconfirmed tail
#define MQTT_KEEPALIVE_S   10
#define CONFIRM_MS         30000      // a sent message is held this long; a drop inside it resends

// ---------- CO2 units ----------
// "co2" is the SCD41's native ppm, as in the draft schema. The v2 slides want
// mmHg, and the SCD41 has no pressure sensor, so co2_mmhg assumes a fixed
// cabin pressure: mmHg = ppm * 1e-6 * CABIN_PRESSURE_MMHG.
// Set PUBLISH_CO2_MMHG to 0 if the team decides the gateway converts instead.
#define PUBLISH_CO2_MMHG   1
#define CABIN_PRESSURE_MMHG 760.0f
