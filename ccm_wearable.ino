/*
  Crew Context Monitor - wearable node firmware
  Board: ESP32-S3 SuperMini (HW-747). Also runs on a XIAO ESP32-S3: same GPIO numbers.
  Prototype for a hackathon demo. NOT a medical device.

  Arduino IDE settings
    Board:            ESP32S3 Dev Module
    USB CDC On Boot:  Enabled      (needed for Serial over the USB-C port)
    Flash Size:       4MB
  Libraries to install (Library Manager):
    "SparkFun MAX3010x Pulse and Proximity Sensor Library"
    "PubSubClient" by Nick O'Leary
  Everything else (Wire, WiFi, BLE) ships with the ESP32 board package.
  The TMP117 and MPU6050 are read with plain I2C register access, so they need no library.

  Wiring (same as the assembly guide)
    SDA = GPIO3, SCL = GPIO4, 3V3 and GND to all three sensors.
    MAX30102 INT -> GPIO1 is wired but not used; this firmware polls the sensor.

  What it does
    - probes 0x57 (MAX30102), 0x48..0x4B (TMP117), 0x68/0x69 (MPU6050) at boot and puts any
      missing sensor in the BLE name, e.g. CCM-W1-noTMP
    - MAX30102 at 100 samples/s: beat detection (IBI in ms) and SpO2 over a 4 s buffer
    - MPU6050 at 50 Hz: act = standard deviation of |acceleration| (m/s^2) per 5 s window
    - TMP117 once per second
    - joins the Pi's hotspot and publishes one JSON message every 5 s to MQTT topic
      ccm/wearable/telemetry on the Pi (the hotspot's gateway address, port 1883)
    - ccm/wearable/status is "online" (retained), and "offline" by last will
    - the last 60 s of messages are kept and resent after a reconnect
    - t_node is clock time once the Pi's NTP answers, otherwise seconds since power-on
    - prints every sensor reading to the Serial Monitor once per second (set PRINT_EVERY_SECOND to 0 to stop)
    - BLE notifications are still in the code but off by default: set USE_BLE to 1 to turn them on
*/

#include <Wire.h>
#include <math.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <time.h>

#define USE_BLE 0                    // 1 = also send each message as a BLE notification
#if USE_BLE
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#endif
#include "MAX30105.h"
#include "heartRate.h"
#include "spo2_algorithm.h"

// ----------------------------------------------------------------- settings
#define NODE_ID            "W1"
#define PRINT_EVERY_SECOND 1         // 1 = print all sensor readings on Serial once per second
#define WIFI_SSID          "Paul"
#define WIFI_PASS          "cabimas22"
#define MQTT_PORT          1883
#define TOPIC_TELEMETRY    "ccm/wearable/telemetry"
#define TOPIC_STATUS       "ccm/wearable/status"
#define PIN_SDA            3
#define PIN_SCL            4
#define BATT_ADC_PIN       -1        // set to an ADC GPIO if you add a 2:1 divider from the cell; -1 sends "batt":null
#define BATT_DIVIDER       2.0f

#define FINGER_IR_MIN      50000UL   // below this IR level there is no finger on the sensor. Tune on the bench.
#define ACT_MOTION_THRESH  0.50f     // m/s^2. act above this sets "motion":true. Tune on the bench.
#define LED_BRIGHTNESS     0x3F      // MAX30102 LED current, 0x00..0xFF. Lower it if IR saturates near 262143.
#define WINDOW_MS          5000UL
#define REPLAY_SLOTS       12        // 12 x 5 s = 60 s
#define MAX_IBI_PER_MSG    8

// Nordic UART Service UUIDs. The Pi's ble-bridge subscribes to the TX (notify) characteristic.
#define SERVICE_UUID       "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NOTIFY_CHAR_UUID   "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// ----------------------------------------------------------------- state
MAX30105 pulse;
bool haveMax = false, haveTmp = false, haveMpu = false;
uint8_t tmpAddr = 0x48, mpuAddr = 0x68;

WiFiClient net;
PubSubClient mqtt(net);
char nodeName[32] = "CCM-" NODE_ID;
uint32_t tMqttTry = 0, tPulsePoll = 0;
bool ntpStarted = false, wifiWasUp = false;

#if USE_BLE
BLEServer *bleServer = nullptr;
BLECharacteristic *bleChar = nullptr;
volatile bool bleConnected = false;
#endif

// pulse / SpO2
#define SPO2_N 100                       // 100 samples at 25 samples/s = 4 s (what the SparkFun algorithm expects)
uint32_t irBuf[SPO2_N], redBuf[SPO2_N];  // ring buffers, decimated 4:1 from the 100 samples/s stream
uint32_t irLin[SPO2_N], redLin[SPO2_N];
int spo2Head = 0, spo2Count = 0;
uint32_t decIr = 0, decRed = 0; uint8_t decN = 0;
uint32_t sampleIdx = 0, lastBeatIdx = 0; bool haveLastBeat = false;
uint16_t winIbi[MAX_IBI_PER_MSG]; uint8_t winIbiN = 0;
uint16_t recentIbi[8]; uint8_t recentN = 0, recentHead = 0;
uint64_t irSum = 0; uint32_t irN = 0;

// motion
double accSum = 0, accSumSq = 0; uint32_t accN = 0;
// temperature
float skinC = NAN;
// latest raw readings, for the once-per-second display
uint32_t lastIr = 0, lastRed = 0; uint16_t lastIbi = 0;
float lastAx = NAN, lastAy = NAN, lastAz = NAN;
// timing
uint32_t tMpu = 0, tTmp = 0, tWin = 0, tPrint = 0, seqNo = 0;

// replay buffer
char replay[REPLAY_SLOTS][256];
bool replaySent[REPLAY_SLOTS];
uint32_t replaySeq[REPLAY_SLOTS];
uint8_t replayHead = 0, replayCount = 0;

// ----------------------------------------------------------------- I2C helpers
bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}
bool i2cRead(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, (int)n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}
bool i2cWrite8(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

// ----------------------------------------------------------------- BLE (optional)
#if USE_BLE
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *s) override { bleConnected = true; }
  void onDisconnect(BLEServer *s) override { bleConnected = false; BLEDevice::startAdvertising(); }
};
void bleStart(const char *name) {
  BLEDevice::init(name);
  BLEDevice::setMTU(247);                 // the JSON is about 200 bytes; the default 23-byte MTU would cut it
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallbacks());
  BLEService *svc = bleServer->createService(SERVICE_UUID);
  bleChar = svc->createCharacteristic(NOTIFY_CHAR_UUID, BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_READ);
  bleChar->addDescriptor(new BLE2902());
  svc->start();
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}
#endif

// ----------------------------------------------------------------- WiFi + MQTT
// Never blocks for long: the pulse sensor's FIFO only holds 320 ms of samples.
void networkService() {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiWasUp) {
    Serial.printf("WiFi up: %s, gateway %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(), WiFi.RSSI());
    if (!ntpStarted) { static char ntpHost[20]; strncpy(ntpHost, WiFi.gatewayIP().toString().c_str(), sizeof(ntpHost) - 1); configTime(0, 0, ntpHost); ntpStarted = true; }
    tMqttTry = 0;
  }
  if (!up && wifiWasUp) Serial.println("WiFi lost, retrying in the background");
  wifiWasUp = up;
  if (!up) return;
  if (mqtt.connected()) { mqtt.loop(); return; }
  if (tMqttTry && millis() - tMqttTry < 3000) return;          // one attempt every 3 s
  tMqttTry = millis() | 1;
  IPAddress broker = WiFi.gatewayIP();                          // the Pi is the hotspot, so it is the gateway
  net.stop();
  if (!net.connect(broker, MQTT_PORT, 300)) { Serial.println("MQTT: broker not reachable"); return; }
  mqtt.setServer(broker, MQTT_PORT);
  if (mqtt.connect(nodeName, TOPIC_STATUS, 1, true, "offline")) {
    mqtt.publish(TOPIC_STATUS, "online", true);
    Serial.println("MQTT connected");
  } else {
    Serial.printf("MQTT connect failed, state %d\n", mqtt.state());
  }
}

// store a message for sending; it stays in the 60 s buffer until MQTT has taken it
void queueMessage(const char *msg, uint32_t seq) {
  uint8_t slot = replayHead;
  strncpy(replay[slot], msg, sizeof(replay[slot]) - 1);
  replay[slot][sizeof(replay[slot]) - 1] = 0;
  replaySeq[slot] = seq;
  replaySent[slot] = false;
  replayHead = (replayHead + 1) % REPLAY_SLOTS;
  if (replayCount < REPLAY_SLOTS) replayCount++;
#if USE_BLE
  if (bleConnected) { bleChar->setValue((uint8_t *)msg, strlen(msg)); bleChar->notify(); }
#endif
}

// publish everything not yet sent, oldest first
void flushMessages() {
  if (!mqtt.connected()) return;
  for (uint8_t i = 0; i < replayCount; i++) {
    uint8_t slot = (replayHead + REPLAY_SLOTS - replayCount + i) % REPLAY_SLOTS;
    if (replaySent[slot]) continue;
    if (!mqtt.publish(TOPIC_TELEMETRY, replay[slot])) return;
    replaySent[slot] = true;
  }
}

// ----------------------------------------------------------------- sensors
void resetPulseState() {
  haveLastBeat = false; recentN = 0; recentHead = 0; winIbiN = 0;
  spo2Count = 0; spo2Head = 0; decN = 0; decIr = 0; decRed = 0;
}

void handlePulseSample(uint32_t red, uint32_t ir) {
  sampleIdx++;                                        // one sample = 10 ms
  lastIr = ir; lastRed = red;
  irSum += ir; irN++;
  if (ir < FINGER_IR_MIN) { if (haveLastBeat || spo2Count) resetPulseState(); return; }

  if (checkForBeat((long)ir)) {
    if (haveLastBeat) {
      uint32_t ibi = (sampleIdx - lastBeatIdx) * 10;  // ms
      if (ibi >= 300 && ibi <= 2000) {                // 30 to 200 bpm
        lastIbi = (uint16_t)ibi;
        if (winIbiN < MAX_IBI_PER_MSG) winIbi[winIbiN++] = (uint16_t)ibi;
        recentIbi[recentHead] = (uint16_t)ibi; recentHead = (recentHead + 1) % 8; if (recentN < 8) recentN++;
      }
    }
    lastBeatIdx = sampleIdx; haveLastBeat = true;
  }
  if (haveLastBeat && sampleIdx - lastBeatIdx > 300) haveLastBeat = false;   // 3 s without a beat: start over

  decIr += ir; decRed += red; decN++;                 // 4:1 decimation to 25 samples/s for SpO2
  if (decN == 4) {
    irBuf[spo2Head] = decIr / 4; redBuf[spo2Head] = decRed / 4;
    spo2Head = (spo2Head + 1) % SPO2_N; if (spo2Count < SPO2_N) spo2Count++;
    decIr = 0; decRed = 0; decN = 0;
  }
}

void pollPulse() {
  if (!haveMax) return;
  uint32_t now = millis();
  if (now - tPulsePoll > 300) haveLastBeat = false;   // the FIFO overflowed during a stall: do not trust the next interval
  tPulsePoll = now;
  pulse.check();
  while (pulse.available()) {
    handlePulseSample(pulse.getFIFORed(), pulse.getFIFOIR());
    pulse.nextSample();
  }
}

void pollMpu() {
  if (!haveMpu) return;
  uint8_t b[6];
  if (!i2cRead(mpuAddr, 0x3B, b, 6)) return;
  const float k = 9.80665f / 16384.0f;                // +-2 g range
  float ax = (int16_t)((b[0] << 8) | b[1]) * k, ay = (int16_t)((b[2] << 8) | b[3]) * k, az = (int16_t)((b[4] << 8) | b[5]) * k;
  lastAx = ax; lastAy = ay; lastAz = az;
  double mag = sqrt((double)ax * ax + (double)ay * ay + (double)az * az);
  accSum += mag; accSumSq += mag * mag; accN++;
}

void pollTmp() {
  if (!haveTmp) return;
  uint8_t b[2];
  if (!i2cRead(tmpAddr, 0x00, b, 2)) return;
  skinC = (int16_t)((b[0] << 8) | b[1]) * 0.0078125f;
}

float readBatt() {
#if BATT_ADC_PIN >= 0
  return analogReadMilliVolts(BATT_ADC_PIN) / 1000.0f * BATT_DIVIDER;
#else
  return NAN;
#endif
}

// ----------------------------------------------------------------- derived values
float currentHr() {                                    // from the most recent beats (up to 8)
  if (recentN < 2) return NAN;
  uint32_t s = 0; for (uint8_t i = 0; i < recentN; i++) s += recentIbi[i];
  return 60000.0f / ((float)s / recentN);
}
float currentSpo2() {                                  // from the 4 s buffer
  if (spo2Count < SPO2_N) return NAN;
  for (int i = 0; i < SPO2_N; i++) { int j = (spo2Head + i) % SPO2_N; irLin[i] = irBuf[j]; redLin[i] = redBuf[j]; }
  int32_t s = 0, h = 0; int8_t sOk = 0, hOk = 0;
  maxim_heart_rate_and_oxygen_saturation(irLin, SPO2_N, redLin, &s, &sOk, &h, &hOk);
  return (sOk && s >= 70 && s <= 100) ? (float)s : NAN;
}
float currentAct() {                                   // std of |a| over the window so far
  if (accN < 2) return NAN;
  double mean = accSum / accN, var = accSumSq / accN - mean * mean;
  return (float)sqrt(var > 0 ? var : 0);
}

// one line per second with everything the sensors are reading
void printSecond() {
  bool finger = haveMax && lastIr >= FINGER_IR_MIN;
  Serial.printf("[%lus] ", (unsigned long)(millis() / 1000));
  if (!haveMax) Serial.print("PULSE: sensor missing");
  else if (!finger) Serial.printf("PULSE: no finger (IR=%lu RED=%lu)", (unsigned long)lastIr, (unsigned long)lastRed);
  else {
    float hr = currentHr(), sp = currentSpo2();
    Serial.printf("PULSE: IR=%lu RED=%lu ", (unsigned long)lastIr, (unsigned long)lastRed);
    if (isnan(hr)) Serial.print("HR=-- "); else Serial.printf("HR=%.0f bpm (last beat gap %u ms) ", hr, lastIbi);
    if (isnan(sp)) Serial.print("SpO2=--"); else Serial.printf("SpO2=%.0f%%", sp);
  }
  if (!haveTmp) Serial.print(" | TEMP: sensor missing");
  else if (isnan(skinC)) Serial.print(" | TEMP: --");
  else Serial.printf(" | TEMP: %.2f C", skinC);
  if (!haveMpu) Serial.print(" | MOTION: sensor missing");
  else if (isnan(lastAx)) Serial.print(" | MOTION: --");
  else {
    float act = currentAct();
    Serial.printf(" | MOTION: x=%.2f y=%.2f z=%.2f m/s2, act=%.3f%s", lastAx, lastAy, lastAz, isnan(act) ? 0.0f : act,
                  (!isnan(act) && act > ACT_MOTION_THRESH) ? " MOVING" : "");
  }
  if (WiFi.status() == WL_CONNECTED) Serial.printf(" | WiFi up %d dBm, MQTT %s\n", WiFi.RSSI(), mqtt.connected() ? "up" : "down");
  else Serial.println(" | WiFi down");
}

// ----------------------------------------------------------------- 5 s window -> JSON
void appendNum(char *out, size_t cap, const char *key, float v, int decimals) {
  size_t n = strlen(out);
  if (isnan(v)) snprintf(out + n, cap - n, ",\"%s\":null", key);
  else snprintf(out + n, cap - n, ",\"%s\":%.*f", key, decimals, v);
}

void closeWindow() {
  // finger present?
  bool finger = haveMax && irN > 0 && (irSum / irN) >= FINGER_IR_MIN;
  float hr = finger ? currentHr() : NAN;
  float spo2 = finger ? currentSpo2() : NAN;
  float act = currentAct();
  bool motion = !isnan(act) && act > ACT_MOTION_THRESH;

  char msg[256];
  time_t t = time(nullptr);
  unsigned long tNode = t > 1700000000 ? (unsigned long)t : millis() / 1000;   // clock time once NTP has answered
  snprintf(msg, sizeof(msg), "{\"node\":\"wearable\",\"id\":\"%s\",\"seq\":%lu,\"t_node\":%lu",
           NODE_ID, (unsigned long)seqNo, tNode);
  appendNum(msg, sizeof(msg), "hr", isnan(hr) ? NAN : roundf(hr), 0);
  appendNum(msg, sizeof(msg), "spo2", spo2, 0);
  appendNum(msg, sizeof(msg), "skin_c", skinC, 2);
  appendNum(msg, sizeof(msg), "act", act, 3);
  size_t n = strlen(msg);
  n += snprintf(msg + n, sizeof(msg) - n, ",\"motion\":%s,\"ibi\":[", motion ? "true" : "false");
  for (uint8_t i = 0; finger && i < winIbiN; i++) n += snprintf(msg + n, sizeof(msg) - n, "%s%u", i ? "," : "", winIbi[i]);
  snprintf(msg + n, sizeof(msg) - n, "]");
  appendNum(msg, sizeof(msg), "batt", readBatt(), 2);
  n = strlen(msg);
  if (haveMax && !finger) n += snprintf(msg + n, sizeof(msg) - n, ",\"finger\":false");
  snprintf(msg + n, sizeof(msg) - n, "}");

  Serial.print("SENT> "); Serial.println(msg);
  queueMessage(msg, seqNo);
  seqNo++;

  winIbiN = 0; irSum = 0; irN = 0; accSum = 0; accSumSq = 0; accN = 0;
}

// ----------------------------------------------------------------- sensor discovery
void probeSensors() {
  haveMax = haveTmp = haveMpu = false;
  if (i2cPresent(0x57)) {                                             // MAX30102
    haveMax = pulse.begin(Wire, 400000);
    if (!haveMax) { Wire.setClock(100000); haveMax = pulse.begin(Wire, 100000); if (haveMax) Serial.println("i2c:100k"); }
    if (haveMax) pulse.setup(LED_BRIGHTNESS, 4, 2, 400, 411, 4096);   // red+IR, 400 Hz averaged by 4 = 100 samples/s
  }
  for (uint8_t a = 0x48; a <= 0x4B && !haveTmp; a++) if (i2cPresent(a)) { tmpAddr = a; haveTmp = true; }   // TMP117
  for (uint8_t a = 0x68; a <= 0x69 && !haveMpu; a++) if (i2cPresent(a)) { mpuAddr = a; haveMpu = true; }   // MPU6050
  if (haveMpu) i2cWrite8(mpuAddr, 0x6B, 0x00);                        // wake from sleep
}

// try every ordered pair of pads 1..13 as (SDA, SCL); true and the pins filled in if any sensor answers
bool findBus(int &sdaOut, int &sclOut) {
  const uint8_t addrs[] = {0x68, 0x57, 0x48, 0x69, 0x49};
  for (int sda = 1; sda <= 13; sda++) {
    for (int scl = 1; scl <= 13; scl++) {
      if (sda == scl) continue;
      Wire.end();
      Wire.begin(sda, scl);
      Wire.setClock(100000);
      Wire.setTimeOut(10);
      for (uint8_t a : addrs) {
        if (i2cPresent(a)) {
          Serial.printf("    0x%02X answers with SDA=GPIO%d SCL=GPIO%d\n", a, sda, scl);
          sdaOut = sda; sclOut = scl; Wire.setTimeOut(50);
          return true;
        }
      }
    }
  }
  Wire.setTimeOut(50);
  return false;
}

// ----------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nCCM wearable node - prototype, not a medical device");

  int sda = PIN_SDA, scl = PIN_SCL;
  Wire.begin(sda, scl);
  Wire.setClock(400000);
  probeSensors();
  if (!haveMax && !haveTmp && !haveMpu) {
    // nothing answered on the configured pins: look for the sensors on every pad pair 1..13
    Serial.printf("No sensor answered on SDA=GPIO%d SCL=GPIO%d. Searching pads 1 to 13...\n", sda, scl);
    if (findBus(sda, scl)) {
      Serial.printf(">>> Sensors found on SDA=GPIO%d SCL=GPIO%d. Using those pins. Set PIN_SDA/PIN_SCL to match.\n", sda, scl);
      Wire.end(); Wire.begin(sda, scl); Wire.setClock(400000);
      probeSensors();
    } else {
      Serial.println(">>> No I2C device answered on ANY pair of pads 1 to 13. This is power or ground, not SDA/SCL:");
      Serial.println(">>> check 3.3 V between 3V3 and GND at a sensor, and that GND is shared.");
      Wire.end(); Wire.begin(PIN_SDA, PIN_SCL); Wire.setClock(400000);
    }
  }

  Serial.printf("MAX30102 %s | TMP117 %s (0x%02X) | MPU6050 %s (0x%02X)\n",
                haveMax ? "ok" : "MISSING", haveTmp ? "ok" : "MISSING", tmpAddr, haveMpu ? "ok" : "MISSING", mpuAddr);

  if (!haveMax) strcat(nodeName, "-noMAX");
  if (!haveTmp) strcat(nodeName, "-noTMP");
  if (!haveMpu) strcat(nodeName, "-noMPU");

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(nodeName);              // shows up in the Pi's DHCP leases, missing sensors included
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  mqtt.setBufferSize(512);
  mqtt.setSocketTimeout(2);
  mqtt.setKeepAlive(15);
  Serial.printf("Node %s joining WiFi \"%s\"\n", nodeName, WIFI_SSID);
#if USE_BLE
  bleStart(nodeName);
  Serial.printf("BLE advertising as %s\n", nodeName);
#endif

#if BATT_ADC_PIN >= 0
  analogSetPinAttenuation(BATT_ADC_PIN, ADC_11db);
#endif
  tWin = tMpu = tTmp = tPrint = millis();
}

void loop() {
  uint32_t now = millis();
  pollPulse();                                           // must run often: the sensor FIFO holds 320 ms
  if (now - tMpu >= 20) { tMpu += 20; if (now - tMpu > 200) tMpu = now; pollMpu(); }
  if (now - tTmp >= 1000) { tTmp = now; pollTmp(); }
#if PRINT_EVERY_SECOND
  if (now - tPrint >= 1000) { tPrint += 1000; printSecond(); }
#endif
  if (now - tWin >= WINDOW_MS) { tWin += WINDOW_MS; closeWindow(); }
  networkService();
  flushMessages();
  delay(2);
}
