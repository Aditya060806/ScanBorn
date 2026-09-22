// Dead reckoning: distance from the wheel encoders, heading from the gyro, integrated the way
// policy/sim.py integrates the rover model's estimate (rover::advance, midpoint heading).
#pragma once

#include <Adafruit_MPU6050.h>
#include <ESP32Encoder.h>

#include "rover_math.h"

class Odometry {
 public:
  // Encoders, then the IMU (Wire must already be started). False when the IMU does not answer.
  bool begin();
  bool imu_ok() const { return imu_ok_; }
  // The mean gyro z rate over `duration_ms` while the rover stands still becomes the bias.
  void calibrate_gyro(uint32_t duration_ms);
  // Counts are full-quadrature: four per encoder line, as counts_per_rev is measured.
  void set_geometry(double wheel_radius_m, double counts_per_rev);
  void reset(const rover::Pose& start);
  // One motor tick: wheel speeds from the encoder counts, turn rate from the gyro, pose update.
  void update(double dt);

  const rover::Pose& pose() const { return pose_; }
  double v() const { return v_; }
  double w() const { return w_; }
  double left_mps() const { return left_mps_; }
  double right_mps() const { return right_mps_; }
  int64_t left_counts() const { return left_total_; }  // since reset
  int64_t right_counts() const { return right_total_; }
  double metres_per_count() const { return metres_per_count_; }
  double yaw_rad() const { return yaw_; }  // gyro heading change since reset, unwrapped
  double gyro_bias() const { return bias_; }

 private:
  double read_gyro_z();

  ESP32Encoder left_;
  ESP32Encoder right_;
  Adafruit_MPU6050 imu_;
  bool imu_ok_ = false;
  double bias_ = 0.0;
  double metres_per_count_ = 0.0;
  int64_t left_last_ = 0;
  int64_t right_last_ = 0;
  int64_t left_total_ = 0;
  int64_t right_total_ = 0;
  rover::Pose pose_ = {0.0, 0.0, 0.0};
  double v_ = 0.0;
  double w_ = 0.0;
  double left_mps_ = 0.0;
  double right_mps_ = 0.0;
  double yaw_ = 0.0;
};
