#include "settings.h"

#include <Preferences.h>

#include <cmath>
#include <cstring>

#include "config.h"
#include "rover_spec.h"

namespace {

const char* const NAMESPACE = "scanborn";

struct Field {
  const char* key;
  double RoverSettings::*member;
  bool geometry;
};

const Field FIELDS[] = {
    {"counts_per_rev", &RoverSettings::counts_per_rev, true},
    {"wheel_radius_m", &RoverSettings::wheel_radius_m, true},
    {"wheelbase_m", &RoverSettings::wheelbase_m, true},
    {"pi_kp", &RoverSettings::pi_kp, false},
    {"pi_ki", &RoverSettings::pi_ki, false},
};

}  // namespace

RoverSettings load_settings() {
  RoverSettings settings = {rover_spec::WHEEL_RADIUS_M, rover_spec::WHEELBASE_M,
                            rover_spec::COUNTS_PER_REV, config::DEFAULT_PI_KP,
                            config::DEFAULT_PI_KI, false};
  Preferences flash;
  if (flash.begin(NAMESPACE, true)) {  // false until the first POST /config creates it
    for (const Field& field : FIELDS) {
      if (flash.isKey(field.key)) {
        settings.*(field.member) = flash.getDouble(field.key, settings.*(field.member));
      }
    }
    settings.calibrated = flash.getBool("calibrated", false);
    flash.end();
  }
  return settings;
}

int save_settings(JsonVariantConst body, RoverSettings* settings, JsonDocument& answer) {
  JsonArray errors = answer["errors"].to<JsonArray>();
  RoverSettings next = *settings;
  int given = 0;
  bool geometry = false;
  for (const Field& field : FIELDS) {
    JsonVariantConst value = body[field.key];
    if (value.isNull()) {
      continue;
    }
    ++given;
    if (value.is<double>() && std::isfinite(value.as<double>()) && value.as<double>() > 0.0) {
      next.*(field.member) = value.as<double>();
      geometry = geometry || field.geometry;
    } else {
      errors.add(String(field.key) + ": must be a positive number");
    }
  }
  if (given == 0) {
    errors.add("config: give counts_per_rev, wheel_radius_m, wheelbase_m, pi_kp or pi_ki");
  }
  if (errors.size() > 0) {
    return 400;
  }

  answer.clear();
  Preferences flash;
  if (!flash.begin(NAMESPACE, false)) {
    answer["error"] = "flash storage is not available";
    return 500;
  }
  for (const Field& field : FIELDS) {
    if (!body[field.key].isNull()) {
      flash.putDouble(field.key, next.*(field.member));
    }
  }
  if (geometry) {
    next.calibrated = true;
    flash.putBool("calibrated", true);
  }
  flash.end();
  *settings = next;

  JsonObject saved = answer["saved"].to<JsonObject>();
  for (const Field& field : FIELDS) {
    saved[field.key] = next.*(field.member);
  }
  answer["calibrated"] = next.calibrated;
  answer["source"] = "flash";
  return 200;
}
