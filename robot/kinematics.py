"""Rover kinematics as pure functions: command limits, wheel speeds, encoder counts.

These are the arithmetic the ESP32 firmware repeats every tick. They take no state, so the golden
vectors (scripts/gen_rover_golden.py) can pin them exactly; acceleration limits and latency depend
on the previous tick and are tested in policy/sim.py instead.
"""
import math


def limit_command(v: float, omega: float, spec) -> tuple:
    """Clamp a policy command to what the rover accepts: forward only, within its limits."""
    return min(max(v, 0.0), spec.v_max), min(max(omega, -spec.w_max), spec.w_max)


def wheel_speeds(v: float, omega: float, spec) -> tuple:
    """(left, right) wheel ground speeds in m/s for a body speed and turn rate."""
    half = spec.wheelbase_m / 2.0
    return v - omega * half, v + omega * half


def wheel_rates(left: float, right: float, spec) -> tuple:
    """Wheel speeds in m/s -> wheel rotation rates in rad/s."""
    return left / spec.wheel_radius_m, right / spec.wheel_radius_m


def counts_per_tick(rate_rad_s: float, spec) -> float:
    """Encoder counts one wheel turning at `rate_rad_s` produces in one control tick."""
    return rate_rad_s * spec.dt / (2.0 * math.pi) * spec.counts_per_rev
