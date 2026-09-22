#include "odometry.h"

#include <Arduino.h>
#include <Wire.h>
#include <esp_log.h>

#include "config.h"

bool Odometry::begin() {
  // GPIO 34-39 have no internal pull resistors; the board fits external 10 kΩ pull-ups.
  ESP32Encoder::useInternalWeakPullResistors = puType::none;
  // ESP-IDF's pulse-counter driver still asks for an internal pull-up on every counter pin. On
  // these input-only pins each request fails, changes nothing, and logs an error (8 of them, seen
  // on the ESP32 emulator). Silence exactly that tag while the encoders attach, then restore it.
  const esp_log_level_t gpio_log = esp_log_level_get("gpio");
  esp_log_level_set("gpio", ESP_LOG_NONE);
  left_.attachFullQuad(config::PIN_LEFT_ENCODER_A, config::PIN_LEFT_ENCODER_B);
  right_.attachFullQuad(config::PIN_RIGHT_ENCODER_A, config::PIN_RIGHT_ENCODER_B);
  esp_log_level_set("gpio", gpio_log);
  left_.clearCount();
  right_.clearCount();

  imu_ok_ = imu_.begin(MPU6050_I2CADDR_DEFAULT, &Wire);
  if (imu_ok_) {
    imu_.setGyroRange(MPU6050_RANGE_500_DEG);
    imu_.setFilterBandwidth(MPU6050_BAND_21_HZ);
  }
  return imu_ok_;
}

double Odometry::read_gyro_z() {
  sensors_event_t accel;
  sensors_event_t gyro;
  sensors_event_t temperature;
  if (!imu_ok_ || !imu_.getEvent(&accel, &gyro, &temperature)) {
    imu_ok_ = false;  // a heading that stops updating must stop the drive, not be guessed
    return bias_;
  }
  return config::GYRO_Z_SIGN * gyro.gyro.z;
}

void Odometry::calibrate_gyro(uint32_t duration_ms) {
  if (!imu_ok_) {
    return;
  }
  bias_ = 0.0;
  double sum = 0.0;
  long samples = 0;
  const uint32_t started = millis();
  while (millis() - started < duration_ms) {
    sum += read_gyro_z();
    ++samples;
    delay(5);
  }
  bias_ = samples > 0 ? sum / samples : 0.0;
}

void Odometry::set_geometry(double wheel_radius_m, double counts_per_rev) {
  metres_per_count_ = 2.0 * rover::MATH_PI * wheel_radius_m / counts_per_rev;
}

void Odometry::reset(const rover::Pose& start) {
  pose_ = start;
  left_last_ = config::LEFT_ENCODER_SIGN * left_.getCount();
  right_last_ = config::RIGHT_ENCODER_SIGN * right_.getCount();
  left_total_ = right_total_ = 0;
  v_ = w_ = left_mps_ = right_mps_ = yaw_ = 0.0;
}

void Odometry::update(double dt) {
  const int64_t left = config::LEFT_ENCODER_SIGN * left_.getCount();
  const int64_t right = config::RIGHT_ENCODER_SIGN * right_.getCount();
  const int64_t left_delta = left - left_last_;
  const int64_t right_delta = right - right_last_;
  left_last_ = left;
  right_last_ = right;
  left_total_ += left_delta;
  right_total_ += right_delta;

  left_mps_ = left_delta * metres_per_count_ / dt;
  right_mps_ = right_delta * metres_per_count_ / dt;
  v_ = (left_mps_ + right_mps_) / 2.0;  // encoders for distance: they do not see wheel slip
  w_ = read_gyro_z() - bias_;           // the gyro for heading: it does not see slip either
  rover::advance(&pose_, v_, w_, dt);
  yaw_ += w_ * dt;
}
