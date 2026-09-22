#include "mission.h"

#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstring>

#include "app.h"
#include "rover_spec.h"

const char* state_name(RoverState state) {
  switch (state) {
    case RoverState::Idle:
      return "idle";
    case RoverState::Ready:
      return "ready";
    case RoverState::Running:
      return "running";
    case RoverState::Done:
      return "done";
    case RoverState::Estop:
      return "estop";
    case RoverState::Error:
      return "error";
  }
  return "error";
}

// ---------------------------------------------------------------------------- validation

namespace {

void add_error(JsonArray errors, const char* format, ...) {
  char message[96];
  va_list args;
  va_start(args, format);
  vsnprintf(message, sizeof message, format, args);
  va_end(args);
  errors.add(String(message));
}

// robot/protocol.py:_finite. ArduinoJson's is<double>() is true for integers and false for bools.
bool finite(JsonVariantConst value) {
  return value.is<double>() && std::isfinite(value.as<double>());
}

// robot/protocol.py:_int8, an integer (not 1.0, not true) in -127..127.
bool int8_value(JsonVariantConst value) {
  if (!value.is<long>() || value.is<bool>()) {
    return false;
  }
  const long number = value.as<long>();
  return number >= -127 && number <= 127;
}

bool numbers(JsonVariantConst value, size_t size, double* out) {
  if (!value.is<JsonArrayConst>() || value.size() != size) {
    return false;
  }
  size_t i = 0;
  for (JsonVariantConst item : value.as<JsonArrayConst>()) {
    if (!finite(item)) {
      return false;
    }
    out[i++] = item.as<double>();
  }
  return true;
}

void identifier(JsonVariantConst body, const char* key, char* out, JsonArray errors) {
  JsonVariantConst value = body[key];
  const char* text = value.is<const char*>() ? value.as<const char*>() : nullptr;
  if (text == nullptr || text[0] == '\0') {
    add_error(errors, "%s: must be a non-empty string", key);
  } else if (strlen(text) > static_cast<size_t>(config::MAX_ID_LENGTH)) {
    add_error(errors, "%s: at most %d characters", key, config::MAX_ID_LENGTH);
  } else {
    strcpy(out, text);
  }
}

void positive(JsonVariantConst container, const char* key, const char* where, double* out,
              JsonArray errors) {
  JsonVariantConst value = container[key];
  if (finite(value) && value.as<double>() > 0.0) {
    *out = value.as<double>();
  } else {
    add_error(errors, "%s.%s: must be a positive number", where, key);
  }
}

}  // namespace

bool parse_mission(JsonVariantConst body, Mission* mission, JsonArray errors) {
  if (!body.is<JsonObjectConst>()) {
    errors.add("payload: must be a JSON object");
    return false;
  }
  JsonVariantConst protocol = body["protocol"];
  if (!(protocol.is<long>() && !protocol.is<bool>() &&
        protocol.as<long>() == config::PROTOCOL_VERSION)) {
    add_error(errors, "protocol: must be %d", config::PROTOCOL_VERSION);
  }
  identifier(body, "session", mission->session, errors);
  identifier(body, "mission_id", mission->mission_id, errors);

  double start[3];
  if (numbers(body["start"], 3, start)) {
    mission->start = rover::Pose{start[0], start[1], start[2]};
  } else {
    errors.add("start: must be 3 finite numbers");
  }
  double goal[2];
  if (numbers(body["goal"], 2, goal)) {
    mission->goal = rover::Point{goal[0], goal[1]};
  } else {
    errors.add("goal: must be 2 finite numbers");
  }

  JsonVariantConst path = body["path"];
  if (!path.is<JsonArrayConst>() || path.size() < 2) {
    errors.add("path: must have at least 2 points");
  } else if (path.size() > static_cast<size_t>(config::MAX_PATH_POINTS)) {
    add_error(errors, "path: at most %d points (got %u)", config::MAX_PATH_POINTS,
              static_cast<unsigned>(path.size()));
  } else {
    int count = 0;
    bool valid = true;
    for (JsonVariantConst point : path.as<JsonArrayConst>()) {
      double xy[2];
      if (!numbers(point, 2, xy)) {
        valid = false;
        break;
      }
      mission->path[count++] = rover::Point{xy[0], xy[1]};
    }
    if (valid) {
      mission->path_count = count;
    } else {
      errors.add("path: every point must be [x, y] finite numbers");
    }
  }

  JsonVariantConst policy = body["policy"];
  if (!policy.is<JsonObjectConst>()) {
    errors.add("policy: must be an object");
  } else {
    JsonVariantConst weights = policy["W"];
    bool weights_valid = weights.is<JsonArrayConst>() && weights.size() == 2;
    for (int row = 0; weights_valid && row < 2; ++row) {
      JsonVariantConst values = weights[row];
      weights_valid = values.is<JsonArrayConst>() && values.size() == 2 &&
                      int8_value(values[0]) && int8_value(values[1]);
      if (weights_valid) {
        mission->policy.W[row][0] = values[0].as<int>();
        mission->policy.W[row][1] = values[1].as<int>();
      }
    }
    if (!weights_valid) {
      errors.add("policy.W: must be a 2x2 array of int8 (-127..127)");
    }
    JsonVariantConst bias = policy["b"];
    if (bias.is<JsonArrayConst>() && bias.size() == 2 && int8_value(bias[0]) &&
        int8_value(bias[1])) {
      mission->policy.b[0] = bias[0].as<int>();
      mission->policy.b[1] = bias[1].as<int>();
    } else {
      errors.add("policy.b: must be 2 int8 values (-127..127)");
    }
    positive(policy, "W_scale", "policy", &mission->policy.W_scale, errors);
    positive(policy, "b_scale", "policy", &mission->policy.b_scale, errors);
  }

  JsonVariantConst limits = body["limits"];
  if (!limits.is<JsonObjectConst>()) {
    errors.add("limits: must be an object");
  } else {
    positive(limits, "v_max", "limits", &mission->v_max, errors);
    positive(limits, "w_max", "limits", &mission->w_max, errors);
    positive(limits, "a_max", "limits", &mission->a_max, errors);
    positive(limits, "alpha_max", "limits", &mission->alpha_max, errors);
    if (finite(limits["v_max"]) && limits["v_max"].as<double>() > config::V_CAP) {
      errors.add("limits.v_max: above the rover's cap of 0.3 m/s");
    }
  }

  JsonVariantConst controller = body["controller"];
  if (!controller.is<JsonObjectConst>()) {
    errors.add("controller: must be an object");
  } else {
    positive(controller, "lookahead_m", "controller", &mission->lookahead_m, errors);
    positive(controller, "stop_radius_m", "controller", &mission->stop_radius_m, errors);
  }

  JsonVariantConst port = body["telemetry_port"];
  if (port.is<long>() && !port.is<bool>() && port.as<long>() >= 1 && port.as<long>() <= 65535) {
    mission->telemetry_port = static_cast<uint16_t>(port.as<long>());
  } else {
    errors.add("telemetry_port: must be a port number 1..65535");
  }
  return errors.size() == 0;
}

// --------------------------------------------------------------------------- state machine

double Rover::elapsed_s() const {
  return state_ == RoverState::Idle || state_ == RoverState::Ready
             ? 0.0
             : (millis() - started_ms_) / 1000.0;
}

void Rover::set_reason(const char* reason) {
  if (reason == nullptr) {
    reason_[0] = '\0';
    return;
  }
  strncpy(reason_, reason, config::MAX_REASON_LENGTH);
  reason_[config::MAX_REASON_LENGTH] = '\0';
}

void Rover::halt() {
  target_ = rover::Command{0.0, 0.0};
  body_ = rover::Command{0.0, 0.0};
  motors.stop();
}

int Rover::wrong_state(JsonDocument& answer) const {
  answer.clear();
  answer["error"] = String("not allowed in state '") + state_name(state_) + "'";
  answer["state"] = state_name(state_);
  return 409;
}

const char* Rover::drive_refusal() const {
  if (!odometry.imu_ok()) {
    return "imu_missing";
  }
  if (config::TOF_REQUIRED && !safety.tof_ok()) {
    return "tof_missing";
  }
  if (!safety.battery_present()) {
    return "battery_absent";
  }
  return nullptr;
}

rover::Spec Rover::drive_limits() const {
  const double v_max = has_mission_ ? mission_.v_max : config::V_CAP;
  const double w_max = has_mission_ ? mission_.w_max : rover_spec::W_MAX;
  const double a_max = has_mission_ ? mission_.a_max : rover_spec::A_MAX;
  const double alpha_max = has_mission_ ? mission_.alpha_max : rover_spec::ALPHA_MAX;
  rover::Spec limits = {settings.wheel_radius_m,
                        settings.wheelbase_m,
                        settings.counts_per_rev,
                        std::min(std::min(v_max, rover_spec::V_MAX), config::V_CAP),
                        std::min(w_max, rover_spec::W_MAX),
                        std::min(a_max, rover_spec::A_MAX),
                        std::min(alpha_max, rover_spec::ALPHA_MAX),
                        1.0 / config::POLICY_HZ};
  return limits;
}

int Rover::load(JsonVariantConst body, const IPAddress& from, JsonDocument& answer) {
  if (state_ != RoverState::Idle && state_ != RoverState::Ready && state_ != RoverState::Done) {
    return wrong_state(answer);
  }
  static Mission incoming;  // about 4 KB: too big for the loop task's stack
  answer.clear();
  JsonArray errors = answer["errors"].to<JsonArray>();
  if (!parse_mission(body, &incoming, errors)) {
    return 400;
  }
  mission_ = incoming;
  has_mission_ = true;
  telemetry_host_ = from;
  index_ = 0;
  set_reason(nullptr);
  state_ = RoverState::Ready;
  answer.clear();
  answer["state"] = state_name(state_);
  return 200;
}

int Rover::start(JsonDocument& answer) {
  if (state_ != RoverState::Ready || !has_mission_) {
    return wrong_state(answer);
  }
  const char* refusal = drive_refusal();
  if (refusal != nullptr) {
    answer.clear();
    answer["error"] = String("the rover cannot drive: ") + refusal;
    answer["state"] = state_name(state_);
    answer["reason"] = refusal;
    return 409;
  }
  limits_ = drive_limits();
  odometry.reset(mission_.start);  // the rover was placed where the mission starts
  index_ = 0;
  target_ = rover::Command{0.0, 0.0};
  body_ = rover::Command{0.0, 0.0};
  last_heartbeat_ms_ = started_ms_ = millis();
  set_reason(nullptr);
  state_ = RoverState::Running;
  answer.clear();
  answer["state"] = state_name(state_);
  return 200;
}

int Rover::stop(JsonDocument& answer) {
  if (state_ != RoverState::Running) {
    return wrong_state(answer);
  }
  halt();
  state_ = RoverState::Ready;
  answer.clear();
  answer["state"] = state_name(state_);
  return 200;
}

int Rover::reset(JsonDocument& answer) {
  if (state_ == RoverState::Running) {
    return wrong_state(answer);
  }
  halt();
  state_ = RoverState::Idle;
  set_reason(nullptr);
  has_mission_ = false;
  index_ = 0;
  answer.clear();
  answer["state"] = state_name(state_);
  return 200;
}

void Rover::estop(const char* reason) {
  halt();
  if (state_ != RoverState::Estop) {
    state_ = RoverState::Estop;
    set_reason(reason);
  }
}

void Rover::fault(const char* reason) {
  halt();
  if (state_ != RoverState::Estop && state_ != RoverState::Error) {
    state_ = RoverState::Error;
    set_reason(reason);
  }
}

void Rover::heartbeat(const char* session) {
  if (has_mission_ && session != nullptr && strcmp(session, mission_.session) == 0) {
    last_heartbeat_ms_ = millis();
  }
}

void Rover::policy_tick() {
  if (state_ != RoverState::Running) {
    return;
  }
  rover::Command command = {0.0, 0.0};
  if (!rover::control_step(odometry.pose(), mission_.path, mission_.path_count, &index_,
                           mission_.goal, mission_.policy, mission_.lookahead_m,
                           mission_.stop_radius_m, &command)) {
    halt();
    state_ = RoverState::Done;
    return;
  }
  if (!std::isfinite(command.v) || !std::isfinite(command.w)) {
    fault("bad_command");
    return;
  }
  target_ = rover::limit_command(command, limits_);
}

void Rover::motor_tick(double dt) {
  if (state_ == RoverState::Running) {
    if (millis() - last_heartbeat_ms_ > config::DEADMAN_MS) {
      estop("heartbeat_lost");
    } else if (!odometry.imu_ok()) {
      fault("imu_lost");
    }
  }
  if (state_ != RoverState::Running) {
    return;  // every way out of running already stopped the motors
  }
  body_ = rover::rate_limit(body_, target_, limits_, dt);
  const rover::Wheels wheels = rover::wheel_speeds(body_, limits_);
  motors.set_targets(wheels.left, wheels.right);
  motors.update(odometry.left_mps(), odometry.right_mps(), dt);
}
