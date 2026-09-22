// The rover's own protections, which need no network: the front range sensor, the battery cut-off
// and the on-board button (docs/robot/rover-protocol.md §2). The power switch is the last one.
#pragma once

#include <VL53L0X.h>

#include <cstdint>

class Safety {
 public:
  // The button and battery pins, then the ToF sensor (Wire must already be started).
  void begin();
  bool tof_ok() const;
  // One motor tick. Returns the protocol reason the rover must E-STOP for now, or nullptr.
  const char* check(bool driving);
  bool battery_present() const { return battery_v_ >= 3.0; }
  double battery_v() const { return battery_v_; }  // low-pass filtered
  double range_m() const { return range_m_; }      // the last ToF reading; negative for none

 private:
  VL53L0X tof_;
  bool tof_ok_ = false;
  bool tof_measured_ = false;
  uint32_t tof_last_ms_ = 0;
  double range_m_ = -1.0;
  double battery_v_ = 0.0;
  bool battery_read_ = false;
  bool battery_low_ = false;
  uint32_t battery_low_since_ms_ = 0;
  bool button_raw_ = false;
  bool button_pressed_ = false;
  uint32_t button_changed_ms_ = 0;
};
