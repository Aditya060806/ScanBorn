// The golden-vector self-test: rover_math.h against tests/fixtures/rover_golden.json.
//
// One implementation for both places it runs: the laptop (firmware/tests/host_selftest.cpp,
// nox -s firmware_host) and the rover (the SELFTEST build prints the same lines on serial).
#pragma once

#include <cmath>
#include <cstdio>

#include "golden.h"
#include "rover_math.h"
#include "calibration_safety.h"

namespace selftest {

// The bar tests/test_rover_golden.py holds the Python reference to.
const double TOLERANCE = 1e-9;

typedef void (*Report)(const char* line);

inline int check(Report report, const char* where, const char* what, double got, double want) {
  if (std::fabs(got - want) <= TOLERANCE) {
    return 0;
  }
  char line[200];
  std::snprintf(line, sizeof line, "FAIL %s / %s: got %.17g, want %.17g", where, what, got, want);
  report(line);
  return 1;
}

inline int check_pair(Report report, const char* where, const char* what, double got0,
                      double got1, const double want[2]) {
  char name[48];
  std::snprintf(name, sizeof name, "%s[0]", what);
  int failures = check(report, where, name, got0, want[0]);
  std::snprintf(name, sizeof name, "%s[1]", what);
  return failures + check(report, where, name, got1, want[1]);
}

// Every check; returns the number that failed, so 0 is PASS.
inline int run(Report report) {
  const rover::Spec& spec = golden::SPEC;
  const rover::Point& goal = golden::PATH[golden::PATH_COUNT - 1];
  int failures = 0;
  char line[200];

  struct FaultCase {
    const char* name;
    bool stopped, imu;
    uint32_t now, beat, start;
    calibration::Fault expected;
  };
  using Fault = calibration::Fault;
  const FaultCase faults[] = {
    {"healthy", false, true, 500, 0, 0, Fault::None},
    {"deadman boundary", false, true, 1000, 0, 0, Fault::None},
    {"deadman expired", false, true, 1001, 0, 0, Fault::HeartbeatLost},
    {"imu lost", false, false, 500, 0, 0, Fault::ImuLost},
    {"imu before deadman", false, false, 1001, 0, 0, Fault::ImuLost},
    {"external stop before faults", true, false, 26000, 0, 0, Fault::RoverStopped},
    {"timeout boundary", false, true, 25000, 25000, 0, Fault::None},
    {"timeout expired", false, true, 25001, 25001, 0, Fault::Timeout},
    {"deadman before timeout", false, true, 25001, 0, 0, Fault::HeartbeatLost},
    {"clock wrap healthy", false, true, 499, UINT32_MAX - 500, UINT32_MAX - 500, Fault::None},
    {"clock wrap deadman", false, true, 500, UINT32_MAX - 500, UINT32_MAX - 500, Fault::HeartbeatLost},
    {"clock wrap timeout", false, true, 24500, 24500, UINT32_MAX - 500, Fault::Timeout},
  };
  for (const auto& test : faults) {
    const auto actual = calibration::fault(test.stopped, test.imu, test.now, test.beat,
                                            test.start, 1000, 25000);
    failures += check(report, "calibration safety", test.name,
                      static_cast<int>(actual), static_cast<int>(test.expected));
  }
  report("12 calibration safety decision cases checked");

  for (int i = 0; i < golden::ROW_COUNT; ++i) {
    const golden::Row& row = golden::ROWS[i];
    int index = row.index_in;
    const rover::Observation obs =
        rover::observe(row.pose, golden::PATH, golden::PATH_COUNT, &index, golden::LOOKAHEAD_M);
    if (index != row.index_out) {
      std::snprintf(line, sizeof line, "FAIL %s / path index: got %d, want %d", row.name, index,
                    row.index_out);
      report(line);
      ++failures;
    }
    failures += check_pair(report, row.name, "observation", obs.forward, obs.heading_error,
                           row.observation);
    const rover::Command action = rover::policy_act(golden::POLICY, obs);
    failures += check_pair(report, row.name, "action", action.v, action.w, row.action);
    const rover::Command command = rover::limit_command(action, spec);
    failures += check_pair(report, row.name, "command", command.v, command.w, row.command);
    const rover::Wheels wheels = rover::wheel_speeds(command, spec);
    failures += check_pair(report, row.name, "wheel_mps", wheels.left, wheels.right,
                           row.wheel_mps);
    const double left_rate = rover::wheel_rate(wheels.left, spec);
    const double right_rate = rover::wheel_rate(wheels.right, spec);
    failures += check_pair(report, row.name, "wheel_radps", left_rate, right_rate,
                           row.wheel_radps);
    failures += check_pair(report, row.name, "encoder_counts_per_tick",
                           rover::counts_per_tick(left_rate, spec),
                           rover::counts_per_tick(right_rate, spec), row.encoder_counts_per_tick);

    // control_step is observe + policy_act while the rover is outside the stop radius.
    int step_index = row.index_in;
    rover::Command stepped = {0.0, 0.0};
    const bool moving =
        rover::control_step(row.pose, golden::PATH, golden::PATH_COUNT, &step_index, goal,
                            golden::POLICY, golden::LOOKAHEAD_M, golden::STOP_RADIUS_M, &stepped);
    if (!moving || step_index != row.index_out) {
      std::snprintf(line, sizeof line, "FAIL %s / control_step: stopped or wrong index",
                    row.name);
      report(line);
      ++failures;
    }
    failures += check_pair(report, row.name, "control_step", stepped.v, stepped.w, row.action);
  }

  // Inside the stop radius the loop stops.
  const rover::Pose arrived = {goal.x - golden::STOP_RADIUS_M / 2.0, goal.y, 0.0};
  int arrived_index = golden::PATH_COUNT - 1;
  rover::Command unused = {0.0, 0.0};
  if (rover::control_step(arrived, golden::PATH, golden::PATH_COUNT, &arrived_index, goal,
                          golden::POLICY, golden::LOOKAHEAD_M, golden::STOP_RADIUS_M, &unused)) {
    report("FAIL control_step: did not stop inside the stop radius");
    ++failures;
  }

  // Commands through the limits, the rate limits and the pose update, across the ±π wrap.
  rover::Pose pose = golden::ODOMETRY_START;
  rover::Command body = {0.0, 0.0};
  for (int i = 0; i < golden::ODOMETRY_COUNT; ++i) {
    const golden::OdometryTick& tick = golden::ODOMETRY[i];
    char where[32];
    std::snprintf(where, sizeof where, "odometry tick %d", i);
    body = rover::rate_limit(body, rover::limit_command(tick.command, spec), spec, spec.dt);
    rover::advance(&pose, body.v, body.w, spec.dt);
    failures += check(report, where, "v", body.v, tick.body.v);
    failures += check(report, where, "w", body.w, tick.body.w);
    failures += check(report, where, "x", pose.x, tick.pose.x);
    failures += check(report, where, "y", pose.y, tick.pose.y);
    failures += check(report, where, "th", pose.th, tick.pose.th);
  }

  std::snprintf(line, sizeof line, "%d golden rows, %d odometry ticks, %d failed checks",
                golden::ROW_COUNT, golden::ODOMETRY_COUNT, failures);
  report(line);
  return failures;
}

}  // namespace selftest
