#pragma once

#include <cstdint>

namespace calibration {
enum class Fault { None, RoverStopped, ImuLost, HeartbeatLost, Timeout };

// Unsigned subtraction preserves millis() rollover. Safety faults take precedence over timeout.
inline Fault fault(bool rover_stopped, bool imu_ok, uint32_t now, uint32_t last_beat,
                   uint32_t started, uint32_t deadman_ms, uint32_t timeout_ms) {
  if (rover_stopped) return Fault::RoverStopped;
  if (!imu_ok) return Fault::ImuLost;
  if (uint32_t(now - last_beat) > deadman_ms) return Fault::HeartbeatLost;
  if (uint32_t(now - started) > timeout_ms) return Fault::Timeout;
  return Fault::None;
}
}
