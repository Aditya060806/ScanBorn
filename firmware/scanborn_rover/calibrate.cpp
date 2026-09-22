#include "calibrate.h"
#ifdef CALIBRATION_HOST_TEST
#include "../tests/calibration_host_stubs.h"
#else
#include <Arduino.h>
#include "app.h"
#endif
#include <cmath>
#include <cstring>
#include "config.h"
#include "calibration_safety.h"

namespace {
char session[65] = {0};
char mode[16] = {0};
const char* state = "idle";
const char* reason = nullptr;
uint32_t started = 0, last_beat = 0, arrived = 0;
double step_duration = 1.0, step_speed = 0.1;
rover::Command command = {0.0, 0.0};
double final_elapsed = 0, final_left = 0, final_right = 0, final_yaw = 0;
void snapshot_result() {
  final_elapsed = (millis() - started) / 1000.0;
  final_left = odometry.left_counts(); final_right = odometry.right_counts();
  final_yaw = odometry.yaw_rad();
}
}

bool calibration_active() { return strcmp(state, "running") == 0; }

void calibration_stop(const char* why) {
  if (!calibration_active()) return;
  motors.stop();
  snapshot_result();
  state = "stopped";
  reason = why;
}

void calibration_heartbeat(const char* id) {
  if (calibration_active() && strcmp(id, session) == 0) last_beat = millis();
}

int calibration_status(const char* id, JsonDocument& answer) {
  answer.clear();
  if (!session[0] || (id && strcmp(id, session) != 0)) {
    answer["error"] = "calibration not found";
    return 404;
  }
  answer["id"] = session;
  answer["session"] = session;
  answer["mode"] = mode;
  answer["status"] = state;
  answer["reason"] = reason;
  answer["source"] = "measured";
  const bool active = calibration_active();
  answer["elapsed_s"] = active ? (millis() - started) / 1000.0 : final_elapsed;
  auto counts = answer["encoder_counts"].to<JsonArray>();
  counts.add(active ? odometry.left_counts() : final_left);
  counts.add(active ? odometry.right_counts() : final_right);
  answer["gyro_yaw_rad"] = active ? odometry.yaw_rad() : final_yaw;
  answer["left_mps"] = active ? odometry.left_mps() : 0;
  answer["right_mps"] = active ? odometry.right_mps() : 0;
  answer["target_mps"] = strcmp(mode, "wheel_step") == 0 ? step_speed :
                        strcmp(mode, "straight") == 0 ? config::CALIBRATE_SPEED_MPS : 0.0;
  return 200;
}

int begin_calibration(JsonVariantConst body, JsonDocument& answer) {
  if (calibration_active() || robot.state() != RoverState::Idle) {
    answer["error"] = "rover must be idle with no active calibration";
    return 409;
  }
  const char* requested = body["mode"] | "";
  const char* id = body["session"] | "";
  const bool straight = strcmp(requested, "straight") == 0;
  const bool spin = strcmp(requested, "spin") == 0;
  const bool step = strcmp(requested, "wheel_step") == 0;
  step_duration = body["duration_s"] | 1.0;
  step_speed = body["target_mps"] | 0.1;
  if ((!straight && !spin && !step) || strlen(id) == 0 || strlen(id) > 64 ||
      !std::isfinite(step_duration) || step_duration < 0.1 || step_duration > 3.0 ||
      !std::isfinite(step_speed) || step_speed <= 0 || step_speed > std::min(0.2, config::V_CAP)) {
    answer["error"] = "give mode straight/spin/wheel_step, a session ID, duration 0.1..3 s and speed 0..0.2 m/s";
    return 400;
  }
  const char* refusal = robot.drive_refusal();
  if (refusal) { answer["error"] = refusal; return 409; }
  strcpy(session, id);
  strcpy(mode, requested);
  state = "running"; reason = nullptr;
  arrived = 0; started = last_beat = millis();
  command = {0.0, 0.0};
  odometry.reset({0.0, 0.0, 0.0});
  calibration_status(session, answer);
  return 202;
}

void calibration_tick(double dt) {
  if (!calibration_active()) return;
  const auto fault = calibration::fault(
      robot.state() == RoverState::Estop || robot.state() == RoverState::Error,
      odometry.imu_ok(), millis(), last_beat, started,
      config::DEADMAN_MS, config::CALIBRATE_TIMEOUT_MS);
  if (fault == calibration::Fault::RoverStopped) {
    calibration_stop("rover_safety_stop");
    return;
  }
  if (fault == calibration::Fault::ImuLost || fault == calibration::Fault::HeartbeatLost) {
    calibration_stop(fault == calibration::Fault::ImuLost ? "imu_lost" : "heartbeat_lost");
    robot.estop(reason);
    return;
  }
  if (fault == calibration::Fault::Timeout) {
    calibration_stop("calibration_timeout"); state = "timeout"; return;
  }
  if (arrived) {
    if (millis() - arrived >= config::CALIBRATE_SETTLE_MS) {
      snapshot_result(); state = "done";
    }
    return;
  }
  const bool straight = strcmp(mode, "straight") == 0, spin = strcmp(mode, "spin") == 0;
  const double distance = (odometry.left_counts() + odometry.right_counts()) / 2.0 *
                          odometry.metres_per_count();
  const bool done = straight ? distance >= config::CALIBRATE_STRAIGHT_M :
                    spin ? std::fabs(odometry.yaw_rad()) >= 2 * rover::MATH_PI * config::CALIBRATE_SPIN_TURNS :
                    (millis() - started) / 1000.0 >= step_duration;
  if (done) { motors.stop(); arrived = millis(); return; }
  const rover::Command target = spin ? rover::Command{0.0, config::CALIBRATE_SPIN_RADPS} :
      rover::Command{straight ? config::CALIBRATE_SPEED_MPS : step_speed, 0.0};
  const rover::Spec limits = robot.drive_limits();
  command = rover::rate_limit(command, target, limits, dt);
  const rover::Wheels wheels = rover::wheel_speeds(command, limits);
  motors.set_targets(wheels.left, wheels.right);
  motors.update(odometry.left_mps(), odometry.right_mps(), dt);
}

int run_calibration(JsonVariantConst, JsonDocument& answer) {
  answer["error"] = "Blocking calibration removed: POST /calibrations, send session heartbeats, poll /calibrations/{id}";
  return 410;
}
