// The mission and the rover's state machine (docs/robot/rover-protocol.md §2-3): payload
// validation, idle -> ready -> running -> done | estop | error, the deadman, and the two loops
// that drive a mission.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include "config.h"
#include "rover_math.h"

enum class RoverState : uint8_t { Idle, Ready, Running, Done, Estop, Error };

const char* state_name(RoverState state);

struct Mission {
  char session[config::MAX_ID_LENGTH + 1];
  char mission_id[config::MAX_ID_LENGTH + 1];
  rover::Pose start;
  rover::Point goal;
  rover::Point path[config::MAX_PATH_POINTS];
  int path_count;
  rover::Policy policy;
  double v_max;
  double w_max;
  double a_max;
  double alpha_max;
  double lookahead_m;
  double stop_radius_m;
  uint16_t telemetry_port;
};

// Checks a POST /mission body as robot/protocol.py:validate_mission does, adding one message per
// problem to `errors`, each naming its field in the same words. Fills `mission`; true when valid.
bool parse_mission(JsonVariantConst body, Mission* mission, JsonArray errors);

class Rover {
 public:
  RoverState state() const { return state_; }
  const char* reason() const { return reason_[0] ? reason_ : nullptr; }
  bool has_mission() const { return has_mission_; }
  const Mission& mission() const { return mission_; }
  int index() const { return index_; }
  const IPAddress& telemetry_host() const { return telemetry_host_; }
  double elapsed_s() const;

  // HTTP requests. Each returns the status code to answer, with the answer in `answer`.
  int load(JsonVariantConst body, const IPAddress& from, JsonDocument& answer);
  int start(JsonDocument& answer);
  int stop(JsonDocument& answer);
  int reset(JsonDocument& answer);
  int wrong_state(JsonDocument& answer) const;
  // Why the rover must not turn its motors now (a protocol reason), or nullptr.
  const char* drive_refusal() const;
  // The stricter of the mission's limits and the rover's own, with its current geometry.
  rover::Spec drive_limits() const;

  void estop(const char* reason);  // from any state; a second E-STOP keeps the first reason
  void fault(const char* reason);  // latched error
  void heartbeat(const char* session);

  void policy_tick();          // POLICY_HZ: control_step on the odometry estimate
  void motor_tick(double dt);  // MOTOR_HZ: deadman, rate limits, wheel speed targets

 private:
  void set_reason(const char* reason);
  void halt();

  RoverState state_ = RoverState::Idle;
  char reason_[config::MAX_REASON_LENGTH + 1] = {0};
  Mission mission_;
  bool has_mission_ = false;
  IPAddress telemetry_host_;
  int index_ = 0;
  rover::Spec limits_ = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
  rover::Command target_ = {0.0, 0.0};
  rover::Command body_ = {0.0, 0.0};
  uint32_t last_heartbeat_ms_ = 0;
  uint32_t started_ms_ = 0;
};
