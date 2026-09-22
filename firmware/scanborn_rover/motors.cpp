#include "motors.h"

#include <Arduino.h>

#include <algorithm>
#include <cmath>

#include "config.h"

namespace {

const uint32_t PWM_MAX = (1u << config::PWM_RESOLUTION_BITS) - 1;
const uint8_t PWM_PINS[] = {config::PIN_LEFT_MOTOR_A, config::PIN_LEFT_MOTOR_B,
                            config::PIN_RIGHT_MOTOR_A, config::PIN_RIGHT_MOTOR_B};

}  // namespace

void Motors::begin() {
  for (uint8_t pin : PWM_PINS) {
    ledcAttach(pin, config::PWM_FREQUENCY_HZ, config::PWM_RESOLUTION_BITS);
    ledcWrite(pin, 0);
  }
  stop();
}

void Motors::set_gains(double kp, double ki) {
  kp_ = kp;
  ki_ = ki;
}

void Motors::set_targets(double left_mps, double right_mps) {
  left_.target = left_mps;
  right_.target = right_mps;
}

double Motors::drive(Wheel* wheel, double measured, double dt) const {
  if (wheel->target == 0.0) {
    wheel->integral = 0.0;  // asked to stand still: coast, and do not wind up against the floor
    return 0.0;
  }
  const double error = wheel->target - measured;
  const double integral = wheel->integral + error * dt;
  const double duty =
      wheel->target / config::MOTOR_FULL_DUTY_MPS + kp_ * error + ki_ * integral;
  if (duty > 1.0 || duty < -1.0) {
    return std::max(-1.0, std::min(1.0, duty));  // saturated: hold the integral (anti-windup)
  }
  wheel->integral = integral;
  return duty;
}

void Motors::write(uint8_t pin_a, uint8_t pin_b, double duty) {
  const uint32_t level = static_cast<uint32_t>(std::fabs(duty) * PWM_MAX + 0.5);
  ledcWrite(pin_a, duty > 0.0 ? level : 0);
  ledcWrite(pin_b, duty < 0.0 ? level : 0);
}

void Motors::update(double left_measured_mps, double right_measured_mps, double dt) {
  left_.duty = drive(&left_, left_measured_mps, dt);
  right_.duty = drive(&right_, right_measured_mps, dt);
  write(config::PIN_LEFT_MOTOR_A, config::PIN_LEFT_MOTOR_B, left_.duty);
  write(config::PIN_RIGHT_MOTOR_A, config::PIN_RIGHT_MOTOR_B, right_.duty);
}

void Motors::stop() {
  left_ = Wheel{0.0, 0.0, 0.0};
  right_ = Wheel{0.0, 0.0, 0.0};
  for (uint8_t pin : PWM_PINS) {
    ledcWrite(pin, 0);
  }
}
