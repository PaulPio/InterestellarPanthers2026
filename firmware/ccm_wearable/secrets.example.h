// Copy this file to secrets.h (ignored by git) and fill in your network. Never commit secrets.h.
#pragma once
#define WIFI_SSID          "your-wifi"       // the WiFi the Pi is on (its hotspot, or a shared one)
#define WIFI_PASS          "your-password"   // that WiFi's password

// Optional. Leave it out when the Pi is the hotspot: the node then uses the WiFi gateway.
// On a shared WiFi such as a phone hotspot, set it to the Pi's address there (on the Pi:
// hostname -I). It is used for both MQTT and the time server.
// #define MQTT_HOST       "192.168.1.50"
