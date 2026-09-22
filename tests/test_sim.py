import math

import numpy as np
import pytest

from policy.sim import DiffDriveSim, Noise, wrap_angle
from robot.spec import load_spec, spec_from_dict

SPEC = load_spec("rover")
OPEN = {"origin": [0.0, 0.0], "cell": 0.1, "width": 60, "height": 60,
        "grid": [[0] * 60 for _ in range(60)], "profile": {"robot_radius": 0.2}}


def spec_with(**changes):
    doc = SPEC.as_dict()
    doc.update(changes)
    return spec_from_dict(doc)


def test_speed_and_acceleration_limits_are_never_exceeded():
    sim = DiffDriveSim(OPEN, SPEC)
    sim.reset((1.0, 3.0, 0.0), place_error=False)
    last_v, last_w = 0.0, 0.0
    for tick in range(60):
        assert sim.step(5.0, 10.0 if tick < 30 else -10.0)
        assert 0.0 <= sim.v <= SPEC.v_max + 1e-12
        assert abs(sim.w) <= SPEC.w_max + 1e-12
        assert abs(sim.v - last_v) <= SPEC.a_max * SPEC.dt + 1e-12
        assert abs(sim.w - last_w) <= SPEC.alpha_max * SPEC.dt + 1e-12
        last_v, last_w = sim.v, sim.w


def test_the_rover_never_reverses():
    sim = DiffDriveSim(OPEN, SPEC)
    sim.reset((3.0, 3.0, 0.0), place_error=False)
    for _ in range(20):
        sim.step(-1.0, 0.0)
    assert sim.v == 0.0 and sim.true_pose == (3.0, 3.0, 0.0)


def test_a_perfect_rover_covers_exactly_speed_times_time():
    quick = spec_with(a_max=100.0)
    sim = DiffDriveSim(OPEN, quick)
    sim.reset((1.0, 3.0, 0.0), place_error=False)
    for _ in range(20):
        sim.step(0.3, 0.0)
    assert sim.true_pose[0] == pytest.approx(1.0 + 0.3 * 1.0, abs=1e-9)
    assert sim.estimated_pose == pytest.approx(sim.true_pose, abs=1e-12)


def test_the_same_seed_gives_the_same_run_and_another_seed_does_not():
    noise = Noise.from_spec(SPEC)

    def run(seed):
        sim = DiffDriveSim(OPEN, SPEC, noise, seed=seed)
        sim.reset((1.0, 3.0, 0.0))
        for _ in range(40):
            sim.step(0.3, 0.4)
        return sim.true_pose

    assert run(7) == run(7)
    assert run(7) != run(8)


def test_gyro_bias_makes_the_estimated_heading_drift_at_the_bias_rate():
    sim = DiffDriveSim(OPEN, SPEC, Noise(gyro_bias=0.05), seed=3)
    sim.reset((3.0, 3.0, 0.0), place_error=False)
    assert sim.bias != 0.0
    for _ in range(40):
        sim.step(0.0, 0.0)
    drift = wrap_angle(sim.estimated_pose[2] - sim.true_pose[2])
    assert drift == pytest.approx(sim.bias * 40 * SPEC.dt, abs=1e-9)


def test_slip_moves_the_true_pose_while_the_encoders_report_the_commanded_distance():
    quick = spec_with(a_max=100.0)
    sim = DiffDriveSim(OPEN, quick, Noise(wheel_slip=0.1), seed=5)
    sim.reset((1.0, 3.0, 0.0), place_error=False)
    encoder_distance = 0.0
    for _ in range(20):
        sim.step(0.3, 0.0)
        encoder_distance += sum(sim.wheel_speeds) / 2 * SPEC.dt
    # The encoders see the wheels turn as commanded; the ground did not go along exactly.
    assert encoder_distance == pytest.approx(0.3, abs=1e-9)
    assert math.dist(sim.estimated_pose[:2], sim.true_pose[:2]) > 1e-4


def test_one_tick_of_latency_delays_the_first_motion_by_one_tick():
    quick = spec_with(a_max=100.0)
    sim = DiffDriveSim(OPEN, quick, Noise(latency_ticks=1))
    sim.reset((1.0, 3.0, 0.0), place_error=False)
    sim.step(0.3, 0.0)
    assert sim.v == 0.0
    sim.step(0.3, 0.0)
    assert sim.v == pytest.approx(0.3)


def test_placement_error_moves_the_truth_but_not_the_belief():
    sim = DiffDriveSim(OPEN, SPEC, Noise(start_xy=0.02, start_heading=0.05), seed=11)
    sim.reset((3.0, 3.0, 1.0))
    assert sim.estimated_pose == (3.0, 3.0, 1.0)
    assert sim.true_pose != sim.estimated_pose
    assert abs(sim.true_pose[0] - 3.0) <= 0.02 and abs(sim.true_pose[2] - 1.0) <= 0.05


def test_the_footprint_collides_before_the_centre_reaches_the_wall():
    grid = np.zeros((60, 60), dtype=np.uint8)
    grid[:, 40:] = 1  # a wall from x = 4.0 m
    walled = {**OPEN, "grid": grid.tolist()}
    sim = DiffDriveSim(walled, SPEC)
    assert sim.reset((3.5, 3.05, 0.0), place_error=False)       # 0.5 m from the wall's cells
    assert not sim.reset((3.95, 3.05, 0.0), place_error=False)  # centre free, footprint is not
    assert not sim.reset((9.0, 9.0, 0.0), place_error=False)    # off the map
    assert sim.step(0.3, 0.0) is False


def test_noise_from_the_spec_uses_its_numbers():
    noise = Noise.from_spec(SPEC)
    assert noise.wheel_slip == SPEC.noise.wheel_slip
    assert noise.latency_ticks == SPEC.noise.latency_ticks
