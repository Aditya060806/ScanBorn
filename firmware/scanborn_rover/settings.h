// The rover's measured values, kept in flash and written with POST /config. Until Phase 6 writes
// them they are rover_spec.h's placeholders and `calibrated` is false.
#pragma once

#include <ArduinoJson.h>

struct RoverSettings {
  double wheel_radius_m;
  double wheelbase_m;
  double counts_per_rev;
  double pi_kp;
  double pi_ki;
  bool calibrated;  // true once a wheel or encoder value has been written
};

RoverSettings load_settings();

// Checks and saves a POST /config body. Returns the status code, with the answer in `answer`.
int save_settings(JsonVariantConst body, RoverSettings* settings, JsonDocument& answer);
