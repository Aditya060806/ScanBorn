// Hardware boundary only; tests compile the production calibrate.cpp unchanged below this seam.
#pragma once
#include <algorithm>
#include <cstdint>
#include "golden.h"
extern uint32_t test_millis;
inline uint32_t millis() { return test_millis; }
enum class RoverState { Idle, Running, Estop, Error };
struct TestRover {
  RoverState current = RoverState::Idle;
  const char* refusal = nullptr;
  const char* stop_reason = nullptr;
  RoverState state() const { return current; }
  const char* drive_refusal() const { return refusal; }
  rover::Spec drive_limits() const { return golden::SPEC; }
  void estop(const char* why) { current = RoverState::Estop; stop_reason = why; }
};
struct TestOdometry {
  double left = 0, right = 0, yaw = 0;
  bool imu = true;
  double left_counts() const { return left; }
  double right_counts() const { return right; }
  double yaw_rad() const { return yaw; }
  double left_mps() const { return 0; }
  double right_mps() const { return 0; }
  double metres_per_count() const { return 0.001; }
  bool imu_ok() const { return imu; }
  void reset(rover::Pose) { left = right = yaw = 0; }
};
struct TestMotors {
  int stops = 0, updates = 0;
  double left = 0, right = 0;
  void stop() { stops++; left = right = 0; }
  void set_targets(double l, double r) { left = l; right = r; }
  void update(double, double, double) { updates++; }
};
extern TestRover robot;
extern TestOdometry odometry;
extern TestMotors motors;
