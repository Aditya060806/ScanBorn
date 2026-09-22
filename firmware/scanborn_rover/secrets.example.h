// Copy this file to secrets.h (gitignored) and set the rover's WiFi.
//
// Use a private 2.4 GHz network: the rover's HTTP API has no authentication
// (docs/robot/rover-protocol.md, Safety). Without a secrets.h the firmware still builds, with these
// values, and says on serial that WiFi is not configured.
#pragma once

#define WIFI_SSID "your-network"
#define WIFI_PASSWORD "your-password"
