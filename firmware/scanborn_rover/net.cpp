#include "net.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include <cmath>

#include "app.h"
#include "calibrate.h"
#include "config.h"

#if __has_include("secrets.h")
#include "secrets.h"
const bool HAVE_SECRETS = true;
#else
#include "secrets.example.h"
const bool HAVE_SECRETS = false;
#endif

namespace {

WebServer server(config::HTTP_PORT);
WiFiUDP heartbeats;
WiFiUDP telemetry;
bool connected = false;

double rounded(double value, double scale) { return std::round(value * scale) / scale; }

void reply(int code, const JsonDocument& answer) {
  String text;
  serializeJson(answer, text);
  server.send(code, "application/json", text);
}

// The request body as JSON; an empty body is {}. False when it is not JSON.
bool read_body(JsonDocument& body) {
  const String text = server.arg("plain");
  if (text.length() == 0) {
    body.to<JsonObject>();
    return true;
  }
  return !deserializeJson(body, text);
}

void refuse_body() {
  JsonDocument answer;
  answer["errors"].to<JsonArray>().add("payload: must be a JSON object");
  reply(400, answer);
}

void handle_status() {
  JsonDocument answer;
  answer["protocol"] = config::PROTOCOL_VERSION;
  answer["firmware"] = FIRMWARE_VERSION;
  answer["fake"] = false;
  auto capabilities = answer["capabilities"].to<JsonArray>();
  capabilities.add("async_calibration_v1");
  capabilities.add("config_readback_v1");
  answer["calibration_active"] = calibration_active();
  answer["state"] = state_name(robot.state());
  answer["reason"] = robot.reason();  // null when there is none
  if (robot.has_mission()) {
    answer["mission_id"] = robot.mission().mission_id;
    answer["session"] = robot.mission().session;
  } else {
    answer["mission_id"] = nullptr;
    answer["session"] = nullptr;
  }
  const rover::Pose& pose = odometry.pose();
  JsonArray estimate = answer["pose"].to<JsonArray>();
  estimate.add(rounded(pose.x, 1e4));
  estimate.add(rounded(pose.y, 1e4));
  estimate.add(rounded(pose.th, 1e4));
  answer["v"] = rounded(odometry.v(), 1e4);
  answer["w"] = rounded(odometry.w(), 1e4);
  answer["index"] = robot.index();
  if (safety.battery_present()) {
    answer["battery_v"] = rounded(safety.battery_v(), 100.0);
  } else {
    answer["battery_v"] = nullptr;
  }
  answer["calibrated"] = settings.calibrated;
  answer["heartbeat_port"] = config::HEARTBEAT_PORT;
  reply(200, answer);
}

void handle_mission() {
  if (calibration_active()) { server.send(409, "application/json", "{\"error\":\"calibration active\"}"); return; }
  JsonDocument body;
  if (!read_body(body)) {
    refuse_body();
    return;
  }
  JsonDocument answer;
  const int code = robot.load(body.as<JsonVariantConst>(), server.client().remoteIP(), answer);
  reply(code, answer);
}

void handle_start() {
  JsonDocument answer;
  reply(robot.start(answer), answer);
}

void handle_stop() {
  JsonDocument answer;
  if (calibration_active()) { calibration_stop("operator"); answer["state"] = "stopped"; reply(200, answer); return; }
  reply(robot.stop(answer), answer);
}

void handle_estop() {
  JsonDocument body;
  if (!read_body(body)) {
    body.to<JsonObject>();  // a broken body must not prevent an E-STOP
  }
  const char* reason = body["reason"] | "requested";
  calibration_stop("estop");
  robot.estop(reason[0] ? reason : "requested");
  JsonDocument answer;
  answer["state"] = state_name(robot.state());
  answer["reason"] = robot.reason();
  reply(200, answer);
}

void handle_reset() {
  JsonDocument answer;
  if (calibration_active()) { answer["error"] = "stop calibration before reset"; reply(409, answer); return; }
  reply(robot.reset(answer), answer);
}

void handle_calibrate() {
  JsonDocument body;
  if (!read_body(body)) {
    refuse_body();
    return;
  }
  JsonDocument answer;
  reply(run_calibration(body.as<JsonVariantConst>(), answer), answer);
}

void handle_config() {
  JsonDocument answer;
  if (calibration_active() || robot.state() != RoverState::Idle) {
    reply(robot.wrong_state(answer), answer);
    return;
  }
  JsonDocument body;
  if (!read_body(body)) {
    refuse_body();
    return;
  }
  const int code = save_settings(body.as<JsonVariantConst>(), &settings, answer);
  if (code == 200) {
    odometry.set_geometry(settings.wheel_radius_m, settings.counts_per_rev);
    motors.set_gains(settings.pi_kp, settings.pi_ki);
  }
  reply(code, answer);
}

void handle_calibrations() {
  JsonDocument body, answer;
  if (!read_body(body)) { refuse_body(); return; }
  reply(begin_calibration(body.as<JsonVariantConst>(), answer), answer);
}

void handle_config_read() {
  JsonDocument answer;
  auto values = answer["settings"].to<JsonObject>();
  values["wheel_radius_m"] = settings.wheel_radius_m;
  values["wheelbase_m"] = settings.wheelbase_m;
  values["counts_per_rev"] = settings.counts_per_rev;
  values["pi_kp"] = settings.pi_kp;
  values["pi_ki"] = settings.pi_ki;
  answer["source"] = "flash";
  reply(200, answer);
}

void handle_not_found() {
  if (server.method() == HTTP_GET && server.uri().startsWith("/calibrations/")) {
    JsonDocument answer;
    const String id = server.uri().substring(14);
    reply(calibration_status(id.c_str(), answer), answer);
    return;
  }
  // Includes /sim/world, which only the fake rover has.
  JsonDocument answer;
  answer["error"] = String("no route ") + server.uri();
  reply(404, answer);
}

void read_heartbeats() {
  while (heartbeats.parsePacket() > 0) {
    char buffer[192];
    const int length = heartbeats.read(buffer, sizeof buffer - 1);
    if (length <= 0) {
      continue;
    }
    buffer[length] = '\0';
    JsonDocument beat;
    if (deserializeJson(beat, buffer)) {
      continue;
    }
    robot.heartbeat(beat["session"] | "");
    calibration_heartbeat(beat["session"] | "");
  }
}

}  // namespace

void net_begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // modem sleep delays heartbeats by hundreds of milliseconds
  WiFi.setAutoReconnect(true);
  if (!HAVE_SECRETS) {
    Serial.println("No secrets.h: copy secrets.example.h to secrets.h and set the WiFi");
  }
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  server.on("/status", HTTP_GET, handle_status);
  server.on("/mission", HTTP_POST, handle_mission);
  server.on("/start", HTTP_POST, handle_start);
  server.on("/stop", HTTP_POST, handle_stop);
  server.on("/estop", HTTP_POST, handle_estop);
  server.on("/reset", HTTP_POST, handle_reset);
  server.on("/calibrate", HTTP_POST, handle_calibrate);
  server.on("/calibrations", HTTP_POST, handle_calibrations);
  server.on("/config", HTTP_GET, handle_config_read);
  server.on("/config", HTTP_POST, handle_config);
  server.onNotFound(handle_not_found);
  server.begin();
  heartbeats.begin(config::HEARTBEAT_PORT);
}

void net_poll() {
  const bool now_connected = WiFi.status() == WL_CONNECTED;
  if (now_connected && !connected) {
    Serial.printf("WiFi %s: http://%s/  heartbeat UDP %u\n", WIFI_SSID,
                  WiFi.localIP().toString().c_str(), config::HEARTBEAT_PORT);
    if (MDNS.begin(config::MDNS_NAME)) {
      MDNS.addService("http", "tcp", config::HTTP_PORT);
      Serial.printf("mDNS: http://%s.local/\n", config::MDNS_NAME);
    }
  } else if (!now_connected && connected) {
    Serial.println("WiFi lost: a running mission stops on its heartbeat deadman");
  }
  connected = now_connected;
  server.handleClient();
  read_heartbeats();
}

void net_send_telemetry() {
  if (!connected || robot.state() == RoverState::Idle || !robot.has_mission()) {
    return;
  }
  const rover::Pose& pose = odometry.pose();
  JsonDocument frame;
  frame["session"] = robot.mission().session;
  frame["t"] = rounded(robot.elapsed_s(), 1e3);
  frame["x"] = rounded(pose.x, 1e4);
  frame["y"] = rounded(pose.y, 1e4);
  frame["th"] = rounded(pose.th, 1e4);
  frame["v"] = rounded(odometry.v(), 1e4);
  frame["w"] = rounded(odometry.w(), 1e4);
  frame["state"] = state_name(robot.state());
  frame["reason"] = robot.reason();
  frame["index"] = robot.index();
  if (safety.battery_present()) {
    frame["battery_v"] = rounded(safety.battery_v(), 100.0);
  } else {
    frame["battery_v"] = nullptr;
  }
  char datagram[256];
  const size_t length = serializeJson(frame, datagram, sizeof datagram);
  if (length == 0 || length >= sizeof datagram) {
    return;  // cannot happen with ids of at most 64 characters; never send a truncated frame
  }
  telemetry.beginPacket(robot.telemetry_host(), robot.mission().telemetry_port);
  telemetry.write(static_cast<const uint8_t*>(static_cast<const void*>(datagram)), length);
  telemetry.endPacket();
}
