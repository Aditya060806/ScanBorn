// Two DC motors on a two-input driver (Cytron MDD3A, or two DRV8871): PWM on input A turns a
// wheel forward, on input B backward, both low lets it coast. A PI loop per wheel holds the wheel
// speed the control loop asks for, on top of a feed-forward duty.
#pragma once

#include <cstdint>

class Motors {
 public:
  // Attaches the PWM outputs and holds them low: the motors are off from boot.
  void begin();
  void set_gains(double kp, double ki);
  void set_targets(double left_mps, double right_mps);
  // One motor tick: PI on each wheel's measured speed, written as PWM.
  void update(double left_measured_mps, double right_measured_mps, double dt);
  // Outputs low, targets and integrators cleared.
  void stop();
  double left_duty() const { return left_.duty; }
  double right_duty() const { return right_.duty; }

 private:
  struct Wheel {
    double target;
    double integral;
    double duty;
  };

  double drive(Wheel* wheel, double measured, double dt) const;
  static void write(uint8_t pin_a, uint8_t pin_b, double duty);

  Wheel left_ = {0.0, 0.0, 0.0};
  Wheel right_ = {0.0, 0.0, 0.0};
  double kp_ = 0.0;
  double ki_ = 0.0;
};
