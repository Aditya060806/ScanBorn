"""A two-wheel rover in the twin: speed, turn and acceleration limits, wheel slip, gyro bias,
and a delay between a command and the motors acting on it.

The rover steers from what it can know — encoder wheel speeds for distance, a gyro for heading —
so the simulator keeps two poses. The *true* pose moves with the wheels' real ground speeds,
which slip. The *estimated* pose integrates the encoders (which do not see slip) and the gyro
(which carries a bias). Controllers read `estimated_pose`; collisions and success use
`true_pose`. That gap is exactly what makes a policy that works in a perfect simulation fail on
a real floor, so it has to be in the gate.

Per tick of dt = 1 / control_hz (plan decision 2 of the Phase 3 plan):
  1. the command applied is the one sent `latency_ticks` ago;
  2. v is clamped to [0, v_max] (no reversing: the range sensor faces forward), ω to ±w_max;
  3. actual v and ω move toward the command by at most a_max·dt and alpha_max·dt;
  4. wheel speeds are v ∓ ω·L/2, and each wheel's ground speed is scaled by (1 + ε), ε ~ N(0, slip);
  5. the true pose integrates the ground speeds; the estimated pose integrates the encoder speeds
     and the gyro (true ω + bias). Both use the midpoint heading.

Collision is the true centre where the clearance field is at most the footprint radius — the
same distance field the planner uses — or anywhere off the map.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from policy.planning import EPS, cell_of, clearance_field
from robot.spec import RobotSpec


def wrap_angle(angle: float) -> float:
    """An angle in [-π, π)."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _advance(pose: list, v: float, w: float, dt: float) -> None:
    mid = pose[2] + w * dt / 2.0
    pose[0] += v * math.cos(mid) * dt
    pose[1] += v * math.sin(mid) * dt
    pose[2] = wrap_angle(pose[2] + w * dt)


@dataclass(frozen=True)
class Noise:
    """Disturbances for one simulation. All zero is the perfect rover."""

    wheel_slip: float = 0.0     # 1-sigma fractional ground-speed error per wheel, per tick
    gyro_bias: float = 0.0      # 1-sigma of the constant bias drawn per run, rad/s
    latency_ticks: int = 0
    start_xy: float = 0.0       # placement error, uniform ±, metres
    start_heading: float = 0.0  # placement error, uniform ±, radians

    @classmethod
    def from_spec(cls, spec: RobotSpec) -> "Noise":
        n = spec.noise
        return cls(n.wheel_slip, n.gyro_bias_rad_s, n.latency_ticks, n.start_xy_m,
                   n.start_heading_rad)


class DiffDriveSim:
    """One rover in one twin. `reset` starts a run; `step(v, ω)` advances one tick."""

    def __init__(self, navmesh: dict, spec: RobotSpec, noise: Noise | None = None, seed: int = 0,
                 field: np.ndarray | None = None):
        self.navmesh = navmesh
        self.spec = spec
        self.noise = noise or Noise()
        self.rng = np.random.default_rng(seed)
        self.field = clearance_field(navmesh) if field is None else field
        self.dt = spec.dt
        self.reset((0.0, 0.0, 0.0), place_error=False)

    # ------------------------------------------------------------------------ state

    @property
    def true_pose(self) -> tuple:
        return tuple(self._true)

    @property
    def estimated_pose(self) -> tuple:
        return tuple(self._estimate)

    def clearance_at(self, x: float, y: float) -> float:
        """Metres from (x, y) to the nearest non-free cell; 0 off the map."""
        row, col = cell_of(self.navmesh, x, y)
        height, width = self.field.shape
        return float(self.field[row, col]) if 0 <= row < height and 0 <= col < width else 0.0

    def _collides(self, x: float, y: float) -> bool:
        return self.clearance_at(x, y) <= self.spec.footprint_radius_m + EPS

    # ---------------------------------------------------------------------- dynamics

    def reset(self, pose, place_error: bool = True) -> bool:
        """Put the rover at `pose` (x, y[, heading]); False if it starts in collision.

        With `place_error` the true pose is off by the noise's placement error while the
        estimate is exactly `pose` — the rover believes it is where it was meant to be put.
        """
        heading = float(pose[2]) if len(pose) > 2 and pose[2] is not None else 0.0
        x, y = float(pose[0]), float(pose[1])
        self._estimate = [x, y, heading]
        if place_error:
            x += self.rng.uniform(-self.noise.start_xy, self.noise.start_xy)
            y += self.rng.uniform(-self.noise.start_xy, self.noise.start_xy)
            heading = wrap_angle(heading + self.rng.uniform(-self.noise.start_heading,
                                                            self.noise.start_heading))
        self._true = [x, y, heading]
        self.v = self.w = 0.0
        self.wheel_speeds = (0.0, 0.0)
        gyro_sigma = self.noise.gyro_bias
        self.bias = float(self.rng.normal(0.0, gyro_sigma)) if gyro_sigma else 0.0
        self._pending = deque([(0.0, 0.0)] * self.noise.latency_ticks)
        self.t = 0.0
        self.ticks = 0
        self.collided = self._collides(x, y)
        return not self.collided

    def step(self, v_cmd: float, w_cmd: float) -> bool:
        """Advance one tick under a (v, ω) command. False once the rover has collided."""
        if self.collided:
            return False
        spec, dt = self.spec, self.dt
        self._pending.append((float(v_cmd), float(w_cmd)))
        v_cmd, w_cmd = self._pending.popleft()

        v_target = min(max(v_cmd, 0.0), spec.v_max)
        w_target = min(max(w_cmd, -spec.w_max), spec.w_max)
        self.v += _clip(v_target - self.v, spec.a_max * dt)
        self.w += _clip(w_target - self.w, spec.alpha_max * dt)

        half = spec.wheelbase_m / 2.0
        left, right = self.v - self.w * half, self.v + self.w * half
        self.wheel_speeds = (left, right)
        if self.noise.wheel_slip:
            slip = self.rng.normal(0.0, self.noise.wheel_slip, 2)
            ground_left, ground_right = left * (1.0 + slip[0]), right * (1.0 + slip[1])
        else:
            ground_left, ground_right = left, right

        true_w = (ground_right - ground_left) / spec.wheelbase_m
        _advance(self._true, (ground_left + ground_right) / 2.0, true_w, dt)
        _advance(self._estimate, (left + right) / 2.0, true_w + self.bias, dt)

        self.t += dt
        self.ticks += 1
        self.collided = self._collides(self._true[0], self._true[1])
        return not self.collided
