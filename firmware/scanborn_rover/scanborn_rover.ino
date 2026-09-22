// ScanBorn rover firmware, protocol version 1 (docs/robot/rover-protocol.md).
//
// The rover runs its mission itself: dead reckoning, the control step, its own safety stops. The
// orchestrator loads the mission, starts and stops it, and must keep sending heartbeats.
//
// Build:     nox -s firmware        (arduino-cli, board esp32:esp32:esp32, core 3.x)
// Self-test: the SELFTEST build prints the golden-vector checks on serial and never drives.
// Upload:    arduino-cli upload --fqbn esp32:esp32:esp32 -p <port> firmware/scanborn_rover
// WiFi:      copy secrets.example.h to secrets.h first.
#include <Arduino.h>
#include <Wire.h>

#include "app.h"
#include "calibrate.h"
#include "config.h"
#include "net.h"
#include "rover_spec.h"
#ifdef SELFTEST
#include "selftest.h"
#endif

Odometry odometry;
Motors motors;
Safety safety;
Rover robot;
RoverSettings settings;

#ifdef SELFTEST

static void print_line(const char* line) { Serial.println(line); }

void setup() {
  motors.begin();  // outputs low: the self-test build never drives
  Serial.begin(115200);
  delay(1000);
  Serial.println("ScanBorn rover self-test, firmware " FIRMWARE_VERSION);
  const int failures = selftest::run(print_line);
  Serial.println(failures == 0 ? "PASS" : "FAIL");
  pinMode(config::PIN_LED, OUTPUT);
  digitalWrite(config::PIN_LED, failures == 0 ? HIGH : LOW);
}

void loop() { delay(1000); }

#else

namespace {

struct Timer {
  uint32_t period_us;
  uint32_t next_us;
  uint32_t last_us;
};

Timer motor_timer = {1000000UL / config::MOTOR_HZ, 0, 0};
Timer policy_timer = {1000000UL / config::POLICY_HZ, 0, 0};
Timer telemetry_timer = {1000000UL / config::TELEMETRY_HZ, 0, 0};

// True when the timer is due, with the seconds since it last fired. A loop that fell behind (a
// calibration move, a slow request) skips the missed ticks instead of replaying them.
bool due(Timer* timer, double* dt) {
  const uint32_t now = micros();
  if (static_cast<int32_t>(now - timer->next_us) < 0) {
    return false;
  }
  *dt = std::min((now - timer->last_us) / 1e6, 0.1);
  timer->last_us = now;
  timer->next_us += timer->period_us;
  if (static_cast<int32_t>(now - timer->next_us) >= 0) {
    timer->next_us = now + timer->period_us;
  }
  return true;
}

void start_timer(Timer* timer, uint32_t now) {
  timer->next_us = now + timer->period_us;
  timer->last_us = now;
}

void show_state() {
  const RoverState state = robot.state();
  bool lit = state == RoverState::Running;
  if (state == RoverState::Estop || state == RoverState::Error) {
    lit = (millis() / 250) % 2 == 0;  // fast blink: latched, needs a reset
  }
  digitalWrite(config::PIN_LED, lit ? HIGH : LOW);
}

}  // namespace

void setup() {
  motors.begin();  // first of all: the motor outputs are low before anything else happens
  Serial.begin(115200);
  pinMode(config::PIN_LED, OUTPUT);
  Serial.printf("ScanBorn rover, firmware %s, protocol %d, spec %s (%s)\n", FIRMWARE_VERSION,
                config::PROTOCOL_VERSION, rover_spec::NAME, rover_spec::SOURCE);

  settings = load_settings();
  motors.set_gains(settings.pi_kp, settings.pi_ki);
  Serial.printf("settings: %s\n", settings.calibrated ? "calibrated (flash)"
                                                      : "not calibrated (placeholders)");
  Wire.begin(config::PIN_SDA, config::PIN_SCL);
  if (!odometry.begin()) {
    Serial.println("IMU (MPU6050) not found: the rover will refuse to drive");
  }
  odometry.set_geometry(settings.wheel_radius_m, settings.counts_per_rev);
  safety.begin();
  if (!safety.tof_ok()) {
    Serial.println("ToF (VL53L0X) not found: the rover will refuse to drive");
  }

  if (odometry.imu_ok()) {
    digitalWrite(config::PIN_LED, HIGH);
    Serial.println("Measuring the gyro bias: keep the rover still");
    odometry.calibrate_gyro(config::GYRO_CALIBRATION_MS);
    Serial.printf("gyro bias %.5f rad/s\n", odometry.gyro_bias());
    digitalWrite(config::PIN_LED, LOW);
  } else {
    Serial.println("gyro bias not measured: no IMU");  // never print a number nobody measured
  }

  net_begin();
  const uint32_t now = micros();
  start_timer(&motor_timer, now);
  start_timer(&policy_timer, now);
  start_timer(&telemetry_timer, now);
}

void loop() {
  net_poll();
  double dt = 0.0;
  if (due(&motor_timer, &dt)) {
    odometry.update(dt);
    const char* reason = safety.check(robot.state() == RoverState::Running || calibration_active());
    if (reason != nullptr) {
      robot.estop(reason);
    }
    if (calibration_active()) calibration_tick(dt);
    else robot.motor_tick(dt);
    show_state();
  }
  if (due(&policy_timer, &dt)) {
    robot.policy_tick();
  }
  if (due(&telemetry_timer, &dt)) {
    net_send_telemetry();
  }
}

#endif
