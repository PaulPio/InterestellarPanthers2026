// One telemetry sample, as it sits in the store-and-forward queue.
// Kept in a header so the Arduino prototype generator sees the type first.
#pragma once
#include <stdint.h>

enum : uint8_t {
  F_SCD  = 1 << 0,   // co2, tempC, rh valid
  F_VOC  = 1 << 1,   // voc valid
  F_PM   = 1 << 2,   // pm1, pm25, pm10 valid
  F_C10S = 1 << 3,   // c10s valid
  F_CPM  = 1 << 4,   // cpm valid (a full 60 s of windows exists)
  F_SENT = 1 << 7    // published, waiting out CONFIRM_MS
};

struct Reading {
  uint32_t seq;
  uint32_t takenMs;   // millis() when measured; converted to epoch at publish time
  uint32_t sentMs;    // millis() when published (valid only with F_SENT)
  uint16_t co2;       // ppm
  float    tempC;
  float    rh;
  uint16_t voc;       // Sensirion VOC index, 1..500
  uint16_t pm1, pm25, pm10;   // ug/m3, atmospheric
  uint16_t cpm;       // rolling 60 s count
  uint16_t c10s;      // counts in this 10 s window
  uint8_t  flags;
};
