// Crew Context Monitor - cabin (environmental) node
// ESP32 DevKitC: SCD41 + SGP40 + PMS5003 + Geiger pulse counter (+ optional SSD1306)
// Publishes one JSON message every 10 s to ccm/cabin/telemetry on the Pi's broker.
//
// Decision-support prototype, not a safety or medical device.
//
// Board:     "ESP32 Dev Module" (arduino-esp32 core)
// Libraries: Sensirion I2C SCD4x (>= 1.0), Adafruit SGP40, PubSubClient,
//            Adafruit SSD1306 (+ Adafruit GFX, Adafruit BusIO, Sensirion Core)
// Settings:  config.h
//
// Message:
//   {"node":"cabin","id":"C1","seq":902,"t_node":1760000000,
//    "co2":812,"co2_mmhg":0.62,"temp_c":22.1,"rh":41,"voc":105,
//    "pm1":2,"pm25":4,"pm10":6,"cpm":18,"c10s":3}
// A sensor that is absent or stale has its fields left out, never faked.
// t_node is the time the sample was measured (epoch s); 0 means the clock was
// never set, so ingest should fall back to t_recv.

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <time.h>
#include <PubSubClient.h>
#include <SensirionI2cScd4x.h>
#include <Adafruit_SGP40.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WebServer.h>
#include <ESPmDNS.h>

#include "config.h"
#include "reading.h"

#define I2C_STR2(x) #x
#define I2C_STR(x) I2C_STR2(x)   // pin number as text, for the wiring hints

// ---------------------------------------------------------------- log (USB serial + WiFi)
// Everything the sketch prints goes to the USB serial port AND into a ring buffer that
// the built-in web page reads, so the "serial monitor" also works with no USB cable.
class TeeLog : public Print {
public:
  size_t write(uint8_t c) override {
    Serial.write(c);
    buf[total % sizeof(buf)] = (char)c;
    total++;
    return 1;
  }
  size_t write(const uint8_t *data, size_t n) override {
    for (size_t i = 0; i < n; i++) write(data[i]);
    return n;
  }
  // Text from absolute position `from` to the end (clamped to what is still held).
  String since(uint32_t from) const {
    uint32_t oldest = total > sizeof(buf) ? total - sizeof(buf) : 0;
    if (from < oldest || from > total) from = oldest;
    String out;
    out.reserve(total - from);
    for (uint32_t i = from; i < total; i++) out += buf[i % sizeof(buf)];
    return out;
  }
  uint32_t total = 0;
private:
  char buf[LOG_BUFFER_BYTES];
};
TeeLog Tee;

// ---------------------------------------------------------------- devices
WiFiClient        net;
PubSubClient      mqtt(net);
SensirionI2cScd4x scd;
Adafruit_SGP40    sgp;
Adafruit_SSD1306  oled(128, 64, &Wire, -1);
WebServer         web(80);

bool haveScd = false, haveSgp = false, haveOled = false;

// ---------------------------------------------------------------- latest values
uint16_t co2Ppm = 0;
float    tempC = 25.0f, rhPct = 50.0f;   // SGP40 compensation defaults until the SCD41 reports
uint32_t scdAtMs = 0;
bool     scdValid = false;

int32_t  vocIndex = 0;

uint16_t pm1 = 0, pm25 = 0, pm10 = 0;
uint32_t pmsAtMs = 0;
bool     pmsValid = false;

// ---------------------------------------------------------------- geiger
portMUX_TYPE      geigerMux = portMUX_INITIALIZER_UNLOCKED;
volatile uint32_t geigerCount = 0;
volatile uint32_t geigerLastUs = 0;
uint16_t cpmRing[CPM_WINDOWS] = {0};
uint8_t  cpmIdx = 0, cpmFilled = 0;
uint16_t lastCpm = 0;
bool     lastCpmValid = false;

void IRAM_ATTR onGeigerPulse() {
  uint32_t now = micros();
  portENTER_CRITICAL_ISR(&geigerMux);
  if (now - geigerLastUs >= GEIGER_DEBOUNCE_US) {
    geigerCount++;
    geigerLastUs = now;
  }
  portEXIT_CRITICAL_ISR(&geigerMux);
}

// ---------------------------------------------------------------- queue
Reading  queueBuf[QUEUE_LEN];
uint16_t qHead = 0, qCount = 0;
uint32_t seqNo = 0;
uint32_t dropped = 0;

Reading &qAt(uint16_t i) { return queueBuf[(qHead + i) % QUEUE_LEN]; }

void qPush(const Reading &r) {
  if (qCount == QUEUE_LEN) {            // full: lose the oldest, keep the newest
    qHead = (qHead + 1) % QUEUE_LEN;
    qCount--;
    dropped++;
  }
  qAt(qCount) = r;
  qCount++;
}

void qPop() {
  qHead = (qHead + 1) % QUEUE_LEN;
  qCount--;
}

uint16_t qUnsent() {
  uint16_t n = 0;
  for (uint16_t i = 0; i < qCount; i++) if (!(qAt(i).flags & F_SENT)) n++;
  return n;
}

// ---------------------------------------------------------------- time
bool clockSet() { return time(nullptr) > 1700000000; }

// Epoch seconds at which a queued reading was measured, or 0 if the clock was never set.
uint32_t measuredEpoch(const Reading &r) {
  if (!clockSet()) return 0;
  uint32_t ageS = (millis() - r.takenMs) / 1000;
  return (uint32_t)time(nullptr) - ageS;
}

// ---------------------------------------------------------------- JSON
// Appends printf-style text; returns false once the buffer is exhausted.
bool jsonAppend(char *out, size_t cap, size_t &len, const char *fmt, ...) {
  if (len >= cap) return false;
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(out + len, cap - len, fmt, ap);
  va_end(ap);
  if (n < 0 || (size_t)n >= cap - len) { len = cap; return false; }
  len += (size_t)n;
  return true;
}

size_t buildTelemetry(const Reading &r, char *out, size_t cap) {
  size_t len = 0;
  jsonAppend(out, cap, len, "{\"node\":\"cabin\",\"id\":\"%s\",\"seq\":%lu,\"t_node\":%lu",
             NODE_ID, (unsigned long)r.seq, (unsigned long)measuredEpoch(r));
  if (r.flags & F_SCD) {
    jsonAppend(out, cap, len, ",\"co2\":%u", (unsigned)r.co2);
#if PUBLISH_CO2_MMHG
    jsonAppend(out, cap, len, ",\"co2_mmhg\":%.2f", (double)(r.co2 * 1e-6f * CABIN_PRESSURE_MMHG));
#endif
    jsonAppend(out, cap, len, ",\"temp_c\":%.1f,\"rh\":%d", (double)r.tempC, (int)lroundf(r.rh));
  }
  if (r.flags & F_VOC)  jsonAppend(out, cap, len, ",\"voc\":%u", (unsigned)r.voc);
  if (r.flags & F_PM)   jsonAppend(out, cap, len, ",\"pm1\":%u,\"pm25\":%u,\"pm10\":%u",
                                   (unsigned)r.pm1, (unsigned)r.pm25, (unsigned)r.pm10);
  if (r.flags & F_CPM)  jsonAppend(out, cap, len, ",\"cpm\":%u", (unsigned)r.cpm);
  if (r.flags & F_C10S) jsonAppend(out, cap, len, ",\"c10s\":%u", (unsigned)r.c10s);
  if (!jsonAppend(out, cap, len, "}")) return 0;
  return len;
}

// ---------------------------------------------------------------- sensors
bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

uint32_t scdStartMs = 0;

void startScd() {
  scdStartMs = millis();
  scd.begin(Wire, ADDR_SCD41);
  scd.stopPeriodicMeasurement();        // harmless if idle; required after a warm reset (blocks 500 ms)
  int16_t err = scd.startPeriodicMeasurement();   // one sample every 5 s
  if (err != 0) Tee.printf("[scd41] start failed, err=%d\n", err);
}

void pollScd() {
  bool ready = false;
  if (scd.getDataReadyStatus(ready) != 0 || !ready) return;
  uint16_t c = 0; float t = 0, h = 0;
  if (scd.readMeasurement(c, t, h) != 0 || c == 0) return;   // 0 ppm = invalid sample
  co2Ppm = c; tempC = t; rhPct = h;
  scdAtMs = millis();
  scdValid = true;
}

// The Sensirion VOC algorithm expects one sample per second, compensated with
// the SCD41's temperature and humidity. Index is 0 during its start-up blackout.
void pollSgp() {
  vocIndex = sgp.measureVocIndex(tempC, rhPct);   // blocks about 250 ms
}

// Prints every I2C address that answers. Stops early if the bus itself is stuck.
void i2cScan() {
  char line[160];
  size_t len = 0;
  uint8_t found = 0;
  jsonAppend(line, sizeof(line), len, "[i2c] scan:");
  for (uint8_t a = 8; a < 120; a++) {
    uint32_t t0 = millis();
    bool ok = i2cPresent(a);
    if (millis() - t0 > 20) {
      Tee.println("[i2c] bus not responding: SDA or SCL is held low or miswired "
                     "(SDA=GPIO" I2C_STR(PIN_I2C_SDA) ", SCL=GPIO" I2C_STR(PIN_I2C_SCL) "), or a sensor has no power");
      return;
    }
    if (ok) { jsonAppend(line, sizeof(line), len, " 0x%02X", a); found++; }
  }
  if (!found) jsonAppend(line, sizeof(line), len, " nothing answered. Check each sensor: VIN to 3V3, "
                         "GND to GND, SDA to D" I2C_STR(PIN_I2C_SDA) ", SCL to D" I2C_STR(PIN_I2C_SCL));
  Tee.println(line);
  Tee.println("[i2c] expected: 0x62 = SCD41 (CO2), 0x59 = SGP40 (VOC), 0x3C = OLED (optional)");
}

// Called every 5 s. Sensors are no longer detected only at boot: one that is plugged in
// late, or that lost power for a moment, is picked up again here without a reset.
void serviceI2c() {
  static uint8_t tick = 0;
  uint32_t now = millis();

  bool scdHere = i2cPresent(ADDR_SCD41);
  if (scdHere && !haveScd) {
    Tee.println("[scd41] found at 0x62, starting (first reading in about 5 s)");
    startScd();
    haveScd = true;
  } else if (!scdHere && haveScd) {
    Tee.println("[scd41] stopped answering at 0x62: check its power and SDA/SCL wires");
    haveScd = false;
    scdValid = false;
  } else if (haveScd && now - scdStartMs > SCD_STALE_MS && (!scdValid || now - scdAtMs > SCD_STALE_MS)) {
    Tee.println("[scd41] answering but no readings for 30 s (it probably lost power briefly): restarting it");
    startScd();
  }

  bool sgpHere = i2cPresent(ADDR_SGP40);
  if (sgpHere && !haveSgp) {
    haveSgp = sgp.begin(&Wire);
    Tee.println(haveSgp ? "[sgp40] found at 0x59, started (VOC index appears after about 1 min)"
                           : "[sgp40] answers at 0x59 but failed its self-test");
  } else if (!sgpHere && haveSgp) {
    Tee.println("[sgp40] stopped answering at 0x59: check its power and SDA/SCL wires");
    haveSgp = false;
    vocIndex = 0;
  }

  if ((!haveScd || !haveSgp) && (tick++ % 3 == 0)) i2cScan();   // every 15 s while one is missing
}

// PMS5003 active-mode frame: 0x42 0x4D, length 28, 13 words, 16-bit sum of the first 30 bytes.
void pollPms() {
  static uint8_t f[32];
  static uint8_t n = 0;
  while (Serial2.available()) {
    uint8_t b = (uint8_t)Serial2.read();
    if (n == 0) { if (b == 0x42) f[n++] = b; continue; }
    if (n == 1) {
      if (b == 0x4D) f[n++] = b;
      else n = (b == 0x42) ? 1 : 0;
      continue;
    }
    f[n++] = b;
    if (n == 4 && (((uint16_t)f[2] << 8) | f[3]) != 28) { n = 0; continue; }
    if (n < 32) continue;
    n = 0;
    uint16_t sum = 0;
    for (uint8_t i = 0; i < 30; i++) sum += f[i];
    if (sum != ((((uint16_t)f[30]) << 8) | f[31])) continue;   // bad checksum: keep the previous frame
    pm1  = ((uint16_t)f[10] << 8) | f[11];    // atmospheric-environment values
    pm25 = ((uint16_t)f[12] << 8) | f[13];
    pm10 = ((uint16_t)f[14] << 8) | f[15];
    pmsAtMs = millis();
    pmsValid = true;
  }
}

// ---------------------------------------------------------------- report
void takeReading() {
  uint32_t now = millis();
  Reading r = {};
  r.seq = ++seqNo;
  r.takenMs = now;

  if (haveScd && scdValid && now - scdAtMs < SCD_STALE_MS) {
    r.co2 = co2Ppm; r.tempC = tempC; r.rh = rhPct;
    r.flags |= F_SCD;
  }
  if (haveSgp && vocIndex > 0) {
    r.voc = (uint16_t)vocIndex;
    r.flags |= F_VOC;
  }
  if (pmsValid && now - pmsAtMs < PMS_STALE_MS) {
    r.pm1 = pm1; r.pm25 = pm25; r.pm10 = pm10;
    r.flags |= F_PM;
  }
#if GEIGER_ENABLED
  portENTER_CRITICAL(&geigerMux);
  uint32_t c = geigerCount;
  geigerCount = 0;
  portEXIT_CRITICAL(&geigerMux);
  r.c10s = (uint16_t)min(c, (uint32_t)65535);
  r.flags |= F_C10S;
  cpmRing[cpmIdx] = r.c10s;
  cpmIdx = (cpmIdx + 1) % CPM_WINDOWS;
  if (cpmFilled < CPM_WINDOWS) cpmFilled++;
  if (cpmFilled == CPM_WINDOWS) {       // no cpm until a real 60 s exists
    uint32_t s = 0;
    for (uint8_t i = 0; i < CPM_WINDOWS; i++) s += cpmRing[i];
    r.cpm = (uint16_t)min(s, (uint32_t)65535);
    r.flags |= F_CPM;
    lastCpm = r.cpm;
    lastCpmValid = true;
  }
#endif
  qPush(r);

#if SERIAL_JSON_LOG
  char msg[320];
  if (buildTelemetry(r, msg, sizeof(msg))) Tee.println(msg);
#endif
}

// ---------------------------------------------------------------- serial monitor
// One line of live values. "--" means the sensor is missing, still warming up, or stale.
//   [  42s] CO2 812 ppm | T 22.1 C | RH 41.3 % | VOC 105 | PM1 2 PM2.5 4 PM10 6 ug/m3 | geiger 3 in window, 18 cpm
void printSensors() {
  uint32_t now = millis();
  char line[200];
  size_t len = 0;
  jsonAppend(line, sizeof(line), len, "[%4lus] ", (unsigned long)(now / 1000));

  if (haveScd && scdValid && now - scdAtMs < SCD_STALE_MS)
    jsonAppend(line, sizeof(line), len, "CO2 %u ppm | T %.1f C | RH %.1f %% | ",
               (unsigned)co2Ppm, (double)tempC, (double)rhPct);
  else
    jsonAppend(line, sizeof(line), len, "CO2 -- | T -- | RH -- | ");

  if (haveSgp && vocIndex > 0) jsonAppend(line, sizeof(line), len, "VOC %ld | ", (long)vocIndex);
  else                         jsonAppend(line, sizeof(line), len, "VOC -- | ");

  if (pmsValid && now - pmsAtMs < PMS_STALE_MS)
    jsonAppend(line, sizeof(line), len, "PM1 %u PM2.5 %u PM10 %u ug/m3 | ",
               (unsigned)pm1, (unsigned)pm25, (unsigned)pm10);
  else
    jsonAppend(line, sizeof(line), len, "PM -- | ");

#if GEIGER_ENABLED
  portENTER_CRITICAL(&geigerMux);
  uint32_t c = geigerCount;             // pulses so far in the current 10 s window
  portEXIT_CRITICAL(&geigerMux);
  if (lastCpmValid) jsonAppend(line, sizeof(line), len, "geiger %lu in window, %u cpm",
                               (unsigned long)c, (unsigned)lastCpm);
  else              jsonAppend(line, sizeof(line), len, "geiger %lu in window, -- cpm", (unsigned long)c);
#else
  jsonAppend(line, sizeof(line), len, "geiger off");
#endif
  Tee.println(line);
}

// ---------------------------------------------------------------- remote serial monitor
// Open http://<the node's IP>/ (or http://REMOTE_HOSTNAME.local/) from any device on the
// same WiFi. Read-only, no password: anyone on that network can see the log.
const char MONITOR_PAGE[] PROGMEM = R"HTML(<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CCM cabin node</title>
<style>body{margin:0;background:#111;color:#ddd;font:13px/1.4 Consolas,Menlo,monospace}
#bar{position:sticky;top:0;background:#222;padding:6px 10px;display:flex;gap:14px;align-items:center}
#log{margin:0;padding:10px;white-space:pre-wrap;word-break:break-all}
.ok{color:#6c6}.bad{color:#e66}label{user-select:none}</style></head><body>
<div id="bar"><b>CCM cabin node - remote serial monitor</b><span id="st" class="bad">connecting</span>
<label><input type="checkbox" id="as" checked> autoscroll</label>
<button onclick="document.getElementById('log').textContent=''">clear</button></div>
<pre id="log"></pre><script>
let next=0,busy=false;const log=document.getElementById('log'),st=document.getElementById('st');
async function tick(){if(busy)return;busy=true;try{
const r=await fetch('/log?from='+next,{cache:'no-store'});const t=await r.text();
const n=parseInt(r.headers.get('X-Next'));if(n<next)log.textContent+='\n--- node restarted ---\n';
next=n;if(t){log.textContent+=t;if(log.textContent.length>400000)log.textContent=log.textContent.slice(-300000);
if(document.getElementById('as').checked)window.scrollTo(0,document.body.scrollHeight);}
st.textContent='live';st.className='ok';}catch(e){st.textContent='no connection';st.className='bad';}busy=false;}
setInterval(tick,1000);tick();</script></body></html>)HTML";

bool webStarted = false;

void handleMonitorPage() { web.send(200, "text/html", MONITOR_PAGE); }

void handleMonitorLog() {
  uint32_t from = web.hasArg("from") ? (uint32_t)strtoul(web.arg("from").c_str(), nullptr, 10) : 0;
  String body = Tee.since(from);
  web.sendHeader("X-Next", String(Tee.total));
  web.sendHeader("Cache-Control", "no-store");
  web.send(200, "text/plain", body);
}

void startWeb() {
  if (!webStarted) {
    web.on("/", handleMonitorPage);
    web.on("/log", handleMonitorLog);
    web.begin();
    webStarted = true;
  }
  if (MDNS.begin(REMOTE_HOSTNAME)) MDNS.addService("http", "tcp", 80);
  Tee.printf("[web] remote serial monitor: http://%s/  or  http://%s.local/\n",
             WiFi.localIP().toString().c_str(), REMOTE_HOSTNAME);
}

// ---------------------------------------------------------------- network
bool     wifiWasUp = false, mqttWasUp = false;
uint32_t wifiDownSinceMs = 0, mqttNextTryMs = 0;

void serviceWifi() {
  bool up = WiFi.status() == WL_CONNECTED;
  uint32_t now = millis();
  if (up && !wifiWasUp) {
    Tee.printf("[wifi] up, ip=%s\n", WiFi.localIP().toString().c_str());
    configTime(0, 0, NTP_SERVER);        // UTC; SNTP keeps retrying in the background
#if REMOTE_MONITOR
    startWeb();
#endif
  }
  if (!up && wifiWasUp) {
    Tee.println("[wifi] down");
    wifiDownSinceMs = now;
  }
  if (!up && now - wifiDownSinceMs > 30000) {   // auto-reconnect stalled: start over
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    wifiDownSinceMs = now;
  }
  wifiWasUp = up;
}

void publishInfo() {
  char info[200];
  snprintf(info, sizeof(info),
           "{\"id\":\"%s\",\"fw\":\"%s\",\"ip\":\"%s\",\"scd41\":%s,\"sgp40\":%s,\"oled\":%s,\"geiger\":%s}",
           NODE_ID, FW_VERSION, WiFi.localIP().toString().c_str(),
           haveScd ? "true" : "false", haveSgp ? "true" : "false",
           haveOled ? "true" : "false", GEIGER_ENABLED ? "true" : "false");
  mqtt.publish(TOPIC_INFO, info, true);
}

void serviceMqtt() {
#if !MQTT_ENABLED
  return;
#endif
  uint32_t now = millis();
  bool up = mqtt.connected();

  if (!up && mqttWasUp) {
    // Anything published inside CONFIRM_MS may have gone into a dead socket: send it again.
    // Ingest must tolerate a repeated (id, seq, t_node).
    for (uint16_t i = 0; i < qCount; i++) qAt(i).flags &= ~F_SENT;
    Tee.printf("[mqtt] lost, state=%d, %u queued\n", mqtt.state(), (unsigned)qCount);
    mqttNextTryMs = now + 2000;
  }
  mqttWasUp = up;

  if (up) { mqtt.loop(); return; }
  if (WiFi.status() != WL_CONNECTED || (int32_t)(now - mqttNextTryMs) < 0) return;

  // Blocks for up to the socket timeout if the Pi is unreachable; pulses are still counted by the ISR.
  if (mqtt.connect("ccm-cabin-" NODE_ID, TOPIC_STATUS, 1, true, "offline")) {
    mqtt.publish(TOPIC_STATUS, "online", true);
    publishInfo();
    mqttWasUp = true;
    Tee.printf("[mqtt] connected, flushing %u\n", (unsigned)qUnsent());
  } else {
    Tee.printf("[mqtt] connect failed, state=%d\n", mqtt.state());
    mqttNextTryMs = millis() + 5000;
  }
}

// Oldest first. A few per pass so a 10-minute backlog does not starve the sensors.
void serviceQueue() {
  uint32_t now = millis();
  while (qCount && (qAt(0).flags & F_SENT) && now - qAt(0).sentMs > CONFIRM_MS) qPop();
  if (!mqtt.connected()) return;

  char msg[320];
  uint8_t budget = 4;
  for (uint16_t i = 0; i < qCount && budget; i++) {
    Reading &r = qAt(i);
    if (r.flags & F_SENT) continue;
    size_t len = buildTelemetry(r, msg, sizeof(msg));
    if (!len || !mqtt.publish(TOPIC_TELEMETRY, msg, false)) break;
    r.flags |= F_SENT;
    r.sentMs = now;
    budget--;
  }
}

// ---------------------------------------------------------------- display
void drawOled() {
  if (!haveOled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 0);
  if (scdValid) oled.printf("CO2 %u", (unsigned)co2Ppm); else oled.print("CO2 --");
  oled.setCursor(0, 22);
  if (lastCpmValid) oled.printf("CPM %u", (unsigned)lastCpm); else oled.print("CPM --");
  oled.setTextSize(1);
  oled.setCursor(0, 46);
  oled.printf("%s %s q%u", WiFi.status() == WL_CONNECTED ? "wifi" : "WIFI?",
              mqtt.connected() ? "mqtt" : "MQTT?", (unsigned)qUnsent());
  oled.setCursor(0, 56);
  if (WiFi.status() == WL_CONNECTED) oled.print(WiFi.localIP().toString().c_str());
  else                               oled.print("prototype, not safety");
  oled.display();
}

// ---------------------------------------------------------------- setup / loop
uint32_t nextSecondMs = 0, nextReportMs = 0;

void setup() {
  Serial.begin(115200);
  delay(200);
  Tee.printf("\nCCM cabin node %s fw %s\n", NODE_ID, FW_VERSION);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(I2C_HZ);

  haveOled = i2cPresent(ADDR_OLED) && oled.begin(SSD1306_SWITCHCAPVCC, ADDR_OLED);
  if (haveOled) {
    oled.clearDisplay();
    oled.setTextColor(SSD1306_WHITE);
    oled.setCursor(0, 0);
    oled.print("CCM cabin " NODE_ID " boot");
    oled.display();
  }

  haveScd = i2cPresent(ADDR_SCD41);
  if (haveScd) startScd();
  haveSgp = i2cPresent(ADDR_SGP40) && sgp.begin(&Wire);
  Tee.printf("[i2c] scd41=%d sgp40=%d oled=%d\n", haveScd, haveSgp, haveOled);

  Serial2.setRxBufferSize(512);
  Serial2.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);

#if GEIGER_ENABLED
  pinMode(PIN_GEIGER, GEIGER_PIN_MODE);
  attachInterrupt(digitalPinToInterrupt(PIN_GEIGER), onGeigerPulse, GEIGER_EDGE);
#endif

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(3);

  uint32_t now = millis();
  wifiDownSinceMs = now;
  nextSecondMs = now + 1000;
  nextReportMs = now + REPORT_MS;
}

void loop() {
  pollPms();
  serviceWifi();
  serviceMqtt();
  serviceQueue();
#if REMOTE_MONITOR
  if (webStarted) web.handleClient();
#endif

  uint32_t now = millis();
  if ((int32_t)(now - nextSecondMs) >= 0) {
    nextSecondMs += 1000;
    if ((int32_t)(now - nextSecondMs) >= 0) nextSecondMs = now + 1000;   // fell behind: do not burst
    static uint8_t i2cTick = 0;
    if (++i2cTick >= 5) { i2cTick = 0; serviceI2c(); }
    if (haveScd) pollScd();
    if (haveSgp) pollSgp();
    drawOled();
#if SERIAL_SENSOR_LOG
    static uint32_t lastPrintMs = 0;
    if (now - lastPrintMs >= SERIAL_SENSOR_MS) { lastPrintMs = now; printSensors(); }
#endif
  }

  now = millis();
  if ((int32_t)(now - nextReportMs) >= 0) {
    nextReportMs += REPORT_MS;
    if ((int32_t)(now - nextReportMs) >= 0) nextReportMs = now + REPORT_MS;
    takeReading();
  }
}
