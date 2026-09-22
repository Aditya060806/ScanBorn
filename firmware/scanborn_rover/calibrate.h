// Nonblocking calibration sessions. Mission protocol version 1 is unchanged.
#pragma once
#include <ArduinoJson.h>
bool calibration_active();
int begin_calibration(JsonVariantConst body, JsonDocument& answer);
int calibration_status(const char* id, JsonDocument& answer);
void calibration_tick(double dt);
void calibration_heartbeat(const char* session);
void calibration_stop(const char* reason);
int run_calibration(JsonVariantConst body, JsonDocument& answer);
