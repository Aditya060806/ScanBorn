// Rover firmware settings: pins (master plan §4.4), the protocol's constants
// (docs/robot/rover-protocol.md) and the safety limits. The rover's geometry and speed limits come
// from rover_spec.h, generated from robot/specs/rover.yaml, and nothing here repeats them.
//
// Values marked PLACEHOLDER are not measured; Phase 6 measures them on the assembled rover.
#pragma once

#include <cstdint>

#define FIRMWARE_VERSION "0.4.0"

namespace config {

// ------------------------------------------------------------------ pins (ESP32 DevKit V1)
const uint8_t PIN_LEFT_MOTOR_A = 25;
const uint8_t PIN_LEFT_MOTOR_B = 26;
const uint8_t PIN_RIGHT_MOTOR_A = 27;
const uint8_t PIN_RIGHT_MOTOR_B = 19;   // not 14/15, which pulse at boot
const uint8_t PIN_LEFT_ENCODER_A = 34;  // 34-39 are input-only: fit the external 10 kΩ pull-ups
const uint8_t PIN_LEFT_ENCODER_B = 35;
const uint8_t PIN_RIGHT_ENCODER_A = 36;
const uint8_t PIN_RIGHT_ENCODER_B = 39;
const uint8_t PIN_SDA = 21;             // MPU6050 and VL53L0X share the bus
const uint8_t PIN_SCL = 22;
const uint8_t PIN_BATTERY = 33;         // ADC1 (ADC2 stops working with WiFi on)
const uint8_t PIN_BUTTON = 23;          // to ground, INPUT_PULLUP
const uint8_t PIN_LED = 2;

// -------------------------------------------------------------------------- protocol
const int PROTOCOL_VERSION = 1;
const char* const MDNS_NAME = "scanborn-rover";
const uint16_t HTTP_PORT = 80;
const uint16_t HEARTBEAT_PORT = 4210;
const int MAX_PATH_POINTS = 256;
const int MAX_ID_LENGTH = 64;
const int MAX_REASON_LENGTH = 32;
const double V_CAP = 0.30;              // m/s: the rover's own cap, whatever a mission asks for
const uint32_t DEADMAN_MS = 1000;       // running this long without a heartbeat latches an E-STOP
const int POLICY_HZ = 20;               // the control step; robot/specs/rover.yaml control_hz
const int MOTOR_HZ = 50;                // odometry, wheel PI, safety checks, deadman
const int TELEMETRY_HZ = 20;

// ---------------------------------------------------------------------------- safety
const double TOF_STOP_M = 0.15;
const bool TOF_REQUIRED = true;         // refuse to drive without the front range sensor
const double BATTERY_CUTOFF_V = 9.9;    // 3S Li-ion at 3.3 V per cell
const double BATTERY_PRESENT_V = 3.0;   // below this no battery is connected (USB bench power)
const uint32_t BATTERY_LOW_MS = 2000;   // under the cut-off this long: motor starts dip it briefly
// 100 kΩ over 33 kΩ: a full 12.6 V pack reads 3.13 V, the top of the ADC's default range.
const double BATTERY_DIVIDER = (100.0 + 33.0) / 33.0;
const uint32_t BUTTON_DEBOUNCE_MS = 30;
const uint32_t GYRO_CALIBRATION_MS = 2000;  // at boot, standing still, to measure the gyro bias

// ---------------------------------------------------------------------------- motors
const uint32_t PWM_FREQUENCY_HZ = 20000;    // above hearing
const uint8_t PWM_RESOLUTION_BITS = 10;
const double MOTOR_FULL_DUTY_MPS = 0.60;    // PLACEHOLDER: wheel speed at full duty (feed-forward)
const double DEFAULT_PI_KP = 1.5;           // PLACEHOLDER: duty per m/s of wheel speed error
const double DEFAULT_PI_KI = 6.0;           // PLACEHOLDER: duty per metre of accumulated error

// PLACEHOLDER sensor orientation, checked on the assembled rover in Phase 6.
const int LEFT_ENCODER_SIGN = 1;
const int RIGHT_ENCODER_SIGN = -1;          // the right motor is mounted mirrored
const int GYRO_Z_SIGN = 1;                  // IMU flat, chip up: counter-clockwise is positive

// ------------------------------------------------------------------------ calibration
const double CALIBRATE_SPEED_MPS = 0.15;
const double CALIBRATE_STRAIGHT_M = 1.0;
const int CALIBRATE_SPIN_TURNS = 3;
const double CALIBRATE_SPIN_RADPS = 1.0;
const uint32_t CALIBRATE_SETTLE_MS = 500;   // counts keep arriving while the rover coasts to rest
const uint32_t CALIBRATE_TIMEOUT_MS = 25000;

}  // namespace config
