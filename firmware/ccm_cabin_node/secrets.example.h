// Copy this file to secrets.h (ignored by git) and fill in your network.
#pragma once
#define WIFI_SSID          "ccm-pi"          // the WiFi the Pi is on (its hotspot, or a shared one)
#define WIFI_PASS          "change-me"       // that WiFi's password

// Optional. Leave these out to use the Pi's own hotspot (192.168.4.1) with MQTT off.
// On a shared WiFi, set MQTT_HOST to the Pi's address there (on the Pi: hostname -I).
// #define MQTT_HOST       "10.0.0.5"
// #define MQTT_ENABLED    1
