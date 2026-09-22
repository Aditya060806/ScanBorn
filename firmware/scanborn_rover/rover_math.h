// The rover's per-tick arithmetic: policy/controller.py:control_step and the kinematics in
// robot/kinematics.py and policy/sim.py, in plain C++14 with no Arduino headers.
//
// Every function mirrors a Python one in the same order of operations, so the results agree with
// tests/fixtures/rover_golden.json to 1e-9. selftest.h checks that — on the laptop through
// firmware/tests/host_selftest.cpp (nox -s firmware_host) and on the rover in the SELFTEST build.
// Change the Python and this file together, then regenerate: python scripts/gen_rover_golden.py
//
// C++14 only: the host check runs under MinGW GCC 6.3, and the ESP32's GCC must accept it too.
#pragma once

#include <algorithm>
#include <cmath>

namespace rover {

const double MATH_PI = 3.141592653589793;  // math.pi

struct Pose {
  double x;
  double y;
  double th;  // heading, counter-clockwise from +x
};

struct Point {
  double x;
  double y;
};

struct Command {
  double v;  // body speed, m/s
  double w;  // turn rate, rad/s
};

struct Wheels {
  double left;
  double right;
};

// [d·cos e, e]: forward distance to the lookahead point in the rover's frame, heading error to it.
struct Observation {
  double forward;
  double heading_error;
};

// The robot spec fields the loop uses (robot/specs/rover.yaml).
struct Spec {
  double wheel_radius_m;
  double wheelbase_m;
  double counts_per_rev;
  double v_max;
  double w_max;
  double a_max;
  double alpha_max;
  double dt;  // 1 / control_hz
};

// The int8 policy as the protocol sends it: W = W_int8 · W_scale, b = b_int8 · b_scale.
struct Policy {
  int W[2][2];
  double W_scale;
  int b[2];
  double b_scale;
};

// policy/sim.py:wrap_angle, an angle in [-π, π). Python's % takes the sign of the divisor and
// fmod the sign of the dividend, so a negative remainder moves up by 2π.
inline double wrap_angle(double angle) {
  const double two_pi = 2.0 * MATH_PI;
  double remainder = std::fmod(angle + MATH_PI, two_pi);
  if (remainder < 0.0) {
    remainder += two_pi;
  }
  return remainder - MATH_PI;
}

inline double distance(const Pose& pose, const Point& point) {
  return std::hypot(point.x - pose.x, point.y - pose.y);
}

// policy/controller.py:lookahead_index. Slide to the nearest path point ahead of `index`, then on
// to the first point at least `lookahead` away. Never moves backwards.
inline int lookahead_index(const Pose& pose, const Point* path, int count, int index,
                           double lookahead) {
  const int last = count - 1;
  while (index < last && distance(pose, path[index + 1]) <= distance(pose, path[index])) {
    ++index;
  }
  while (index < last && distance(pose, path[index]) < lookahead) {
    ++index;
  }
  return index;
}

// policy/controller.py:observe. `index` is read and advanced.
inline Observation observe(const Pose& pose, const Point* path, int count, int* index,
                           double lookahead) {
  *index = lookahead_index(pose, path, count, *index, lookahead);
  const double dx = path[*index].x - pose.x;
  const double dy = path[*index].y - pose.y;
  const double d = std::hypot(dx, dy);
  const double e = d > 1e-9 ? wrap_angle(std::atan2(dy, dx) - pose.th) : 0.0;
  Observation obs = {d * std::cos(e), e};
  return obs;
}

// policy/finetune/train_bc.py: LinearPolicy.act on the dequantized int8 policy, W·obs + b.
inline Command policy_act(const Policy& policy, const Observation& obs) {
  const double w00 = policy.W[0][0] * policy.W_scale;
  const double w01 = policy.W[0][1] * policy.W_scale;
  const double w10 = policy.W[1][0] * policy.W_scale;
  const double w11 = policy.W[1][1] * policy.W_scale;
  const double b0 = policy.b[0] * policy.b_scale;
  const double b1 = policy.b[1] * policy.b_scale;
  Command command = {w00 * obs.forward + w01 * obs.heading_error + b0,
                     w10 * obs.forward + w11 * obs.heading_error + b1};
  return command;
}

// policy/controller.py:at_goal
inline bool at_goal(const Pose& pose, const Point& goal, double tolerance) {
  return std::hypot(pose.x - goal.x, pose.y - goal.y) <= tolerance;
}

// policy/controller.py:control_step, one tick of the loop from the pose the rover believes.
// Returns false once the estimate is within `stop_radius` of the goal: the rover stops. Otherwise
// `command` is the policy's (v, ω) before limits and `index` has moved along the path.
inline bool control_step(const Pose& estimate, const Point* path, int count, int* index,
                         const Point& goal, const Policy& policy, double lookahead,
                         double stop_radius, Command* command) {
  const Observation obs = observe(estimate, path, count, index, lookahead);
  if (*index == count - 1 && at_goal(estimate, goal, stop_radius)) {
    return false;
  }
  *command = policy_act(policy, obs);
  return true;
}

// robot/kinematics.py:limit_command, forward only, within the limits.
inline Command limit_command(const Command& command, const Spec& spec) {
  Command limited = {std::min(std::max(command.v, 0.0), spec.v_max),
                     std::min(std::max(command.w, -spec.w_max), spec.w_max)};
  return limited;
}

// policy/sim.py:_clip
inline double clip(double value, double limit) {
  return std::max(-limit, std::min(limit, value));
}

// policy/sim.py:DiffDriveSim.step, step 3: move toward the target by at most a_max·dt and
// alpha_max·dt.
inline Command rate_limit(const Command& current, const Command& target, const Spec& spec,
                          double dt) {
  Command next = {current.v + clip(target.v - current.v, spec.a_max * dt),
                  current.w + clip(target.w - current.w, spec.alpha_max * dt)};
  return next;
}

// robot/kinematics.py:wheel_speeds, (left, right) ground speeds in m/s.
inline Wheels wheel_speeds(const Command& command, const Spec& spec) {
  const double half = spec.wheelbase_m / 2.0;
  Wheels wheels = {command.v - command.w * half, command.v + command.w * half};
  return wheels;
}

// robot/kinematics.py:wheel_rates, for one wheel: m/s to rad/s.
inline double wheel_rate(double speed_mps, const Spec& spec) {
  return speed_mps / spec.wheel_radius_m;
}

// robot/kinematics.py:counts_per_tick
inline double counts_per_tick(double rate_rad_s, const Spec& spec) {
  return rate_rad_s * spec.dt / (2.0 * MATH_PI) * spec.counts_per_rev;
}

// policy/sim.py:_advance, the pose update with the midpoint heading.
inline void advance(Pose* pose, double v, double w, double dt) {
  const double mid = pose->th + w * dt / 2.0;
  pose->x += v * std::cos(mid) * dt;
  pose->y += v * std::sin(mid) * dt;
  pose->th = wrap_angle(pose->th + w * dt);
}

}  // namespace rover
