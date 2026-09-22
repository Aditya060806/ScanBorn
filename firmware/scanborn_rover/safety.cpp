#include "safety.h"

#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "range_health.h"

namespace {

const uint16_t TOF_OUT_OF_RANGE_MM = 8000;  // the sensor reports 8190 or 8191 when it sees nothing
const uint32_t TOF_PERIOD_MS = 33;

}  // namespace

void Safety::begin() {
  tof_measured_ = false;
  pinMode(config::PIN_BUTTON, INPUT_PULLUP);
  tof_.setTimeout(50);
  tof_ok_ = tof_.init();
  if (tof_ok_) {
    tof_.startContinuous(TOF_PERIOD_MS);
  }
}

bool Safety::tof_ok() const {
  return range_health::fresh(tof_ok_, tof_measured_, millis(), tof_last_ms_);
}

const char* Safety::check(bool driving) {
  const uint32_t now = millis();

  // The button pulls its pin low. Debounced, and only the press latches: holding it down does not
  // stop the rover again after a reset.
  const bool raw = digitalRead(config::PIN_BUTTON) == LOW;
  if (raw != button_raw_) {
    button_raw_ = raw;
    button_changed_ms_ = now;
  }
  const bool was_pressed = button_pressed_;
  if (now - button_changed_ms_ >= config::BUTTON_DEBOUNCE_MS) {
    button_pressed_ = button_raw_;
  }
  if (button_pressed_ && !was_pressed) {
    return "button";
  }

  const double volts =
      analogReadMilliVolts(config::PIN_BATTERY) / 1000.0 * config::BATTERY_DIVIDER;
  battery_v_ = battery_read_ ? battery_v_ + 0.1 * (volts - battery_v_) : volts;
  battery_read_ = true;
  // No battery at all is USB bench power, where nothing drives; while driving it means the pack
  // or its sense wire came off, which is a stop too.
  if ((battery_present() || driving) && battery_v_ < config::BATTERY_CUTOFF_V) {
    if (!battery_low_) {
      battery_low_ = true;
      battery_low_since_ms_ = now;
    } else if (now - battery_low_since_ms_ >= config::BATTERY_LOW_MS) {
      return "battery_low";
    }
  } else {
    battery_low_ = false;
  }

  // Read the ToF only when it has a new measurement, so this tick never waits on it.
  if (tof_ok_ && (tof_.readReg(VL53L0X::RESULT_INTERRUPT_STATUS) & 0x07) != 0) {
    const uint16_t millimetres = tof_.readRangeContinuousMillimeters();
    if (tof_.timeoutOccurred()) {
      tof_ok_ = false;
      range_m_ = -1.0;
    } else {
      tof_measured_ = true;
      tof_last_ms_ = now;
      range_m_ = millimetres >= TOF_OUT_OF_RANGE_MM ? -1.0 : millimetres / 1000.0;
    }
  }
  if (driving) {
    if (config::TOF_REQUIRED && !tof_ok()) {
      return "tof_fault";
    }
    if (range_m_ >= 0.0 && range_m_ < config::TOF_STOP_M) {
      return "obstacle";
    }
  }
  return nullptr;
}
