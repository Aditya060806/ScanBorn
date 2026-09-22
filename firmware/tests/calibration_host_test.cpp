#include <cstdio>
#include <cstring>
#include <limits>
#include "calibrate.h"
#include "config.h"
#include "calibration_host_stubs.h"

uint32_t test_millis = 100;
TestRover robot;
TestOdometry odometry;
TestMotors motors;
int failures = 0, checks = 0;
void check(bool condition, const char* name) {
  checks++;
  if (!condition) { failures++; std::printf("FAIL %s\n", name); }
}
void reset() {
  calibration_stop("test_cleanup"); robot = {}; odometry = {}; motors = {};
  test_millis += 100;
}
int start(const char* mode = "wheel_step", const char* id = "session", double speed = 0.1) {
  JsonDocument body, answer;
  body["mode"] = mode; body["session"] = id;
  body["duration_s"] = 0.1; body["target_mps"] = speed;
  return begin_calibration(body.as<JsonVariantConst>(), answer);
}
void status(const char* expected, const char* why = nullptr) {
  JsonDocument answer;
  check(calibration_status("session", answer) == 200, "status stays available");
  check(strcmp(answer["status"].as<const char*>(), expected) == 0, expected);
  if (why) check(strcmp(answer["reason"].as<const char*>(), why) == 0, why);
}
int main() {
  reset(); check(start("invalid") == 400, "invalid mode refused");
  check(start("spin", "") == 400, "missing identity refused");
  check(start("spin", "session", std::numeric_limits<double>::quiet_NaN()) == 400, "NaN speed refused");
  check(motors.updates == 0, "invalid inputs never move");
  robot.refusal = "tof_fault"; check(start() == 409, "missing range sensor refuses start");
  reset(); check(start("spin") == 202, "start accepted");
  check(start("spin", "other") == 409, "duplicate active start refused");
  calibration_tick(0.02); check(motors.updates == 1, "motor tick advances");
  calibration_stop("stop"); status("stopped", "stop");
  int before = motors.updates; calibration_tick(0.02);
  check(motors.updates == before && motors.left == 0 && motors.right == 0, "Stop prevents further targets");
  check(start() == 202, "terminal session permits explicit new start");

  reset(); start("spin"); test_millis += config::DEADMAN_MS;
  calibration_tick(0.02); check(calibration_active(), "exact deadman boundary retained");
  calibration_heartbeat("wrong-session"); test_millis++;
  calibration_tick(0.02); status("stopped", "heartbeat_lost");
  check(robot.current == RoverState::Estop && motors.stops == 1, "deadman latches E-STOP and stops motors");

  reset(); start("spin"); test_millis += config::DEADMAN_MS;
  calibration_heartbeat("session"); test_millis++;
  calibration_tick(0.02); check(calibration_active(), "matching heartbeat extends lease");
  odometry.imu = false; calibration_tick(0.02); status("stopped", "imu_lost");
  check(robot.current == RoverState::Estop && motors.stops == 1, "IMU loss stops motors");

  for (const char* reason : {"estop", "obstacle", "tof_fault", "battery_low"}) {
    reset(); start("spin"); robot.estop(reason); calibration_tick(0.02);
    status("stopped", "rover_safety_stop");
    check(motors.stops == 1 && motors.updates == 0, "external safety latch prevents actuation");
  }
  reset(); start("spin"); test_millis += config::CALIBRATE_TIMEOUT_MS + 1;
  calibration_heartbeat("session"); calibration_tick(0.02); status("timeout", "calibration_timeout");
  check(motors.stops == 1, "timeout stops motors");

  reset(); start(); calibration_tick(0.02); test_millis += 100; calibration_tick(0.02);
  check(motors.stops == 1, "wheel step stops at duration");
  test_millis += config::CALIBRATE_SETTLE_MS; calibration_tick(0.02); status("done");
  before = motors.updates; calibration_tick(0.02);
  check(before == motors.updates, "done session stays stopped");
  JsonDocument empty, answer;
  check(run_calibration(empty.as<JsonVariantConst>(), answer) == 410, "blocking calibration remains refused");
  check(calibration_status("missing", answer) == 404, "wrong status identity refused");
  std::printf("%d calibration checks; %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
