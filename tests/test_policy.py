import numpy as np
import pytest

from policy.controller import K_V, K_W
from policy.evaluate import (
    MISSION_GATE,
    SIM_GATE,
    evaluate,
    evaluate_mission,
    record_demos,
    rms_deviation,
)
from policy.finetune.train_bc import (
    LinearPolicy,
    dequantize_int8,
    finetune_bc,
    make_baseline,
)
from policy.planning import plan_mission
from policy.sim import Noise
from robot.spec import load_spec

SPEC = load_spec("rover")

# A 4 x 4 m room with a 1 m pillar in the middle (x and y from 1.5 to 2.5 m).
NAVMESH = {
    "origin": [0, 0], "cell": 0.1, "width": 40, "height": 40,
    "grid": [[1 if (15 <= r <= 24 and 15 <= c <= 24) else 0 for c in range(40)]
             for r in range(40)],
    "profile": {"robot_radius": 0.2},
}
# About 2 m around the pillar's corner, starting the wrong way round. Dead reckoning holds the
# 10 cm bar at this length (the Phase 3 plan measures where it stops holding).
MISSION = plan_mission(NAVMESH, (0.6, 2.0, 0.0), (2.0, 3.3))
LONG = plan_mission(NAVMESH, (0.6, 0.6, 0.0), (3.4, 3.4))  # 4.6 m, corner to corner
GOOD = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2))


@pytest.fixture(scope="module")
def cloned():
    demos = record_demos(NAVMESH, MISSION["goal"], SPEC, n=6, path=MISSION["path"],
                         start=MISSION["start"])
    return demos, finetune_bc(make_baseline(2, 2), demos)


def test_the_mission_turns_a_corner():
    assert len(MISSION["waypoints"]) > 2
    assert 1.5 < MISSION["length_m"] < 3.0


def test_behaviour_cloning_recovers_the_demonstrators_gains(cloned):
    demos, policy = cloned
    assert len(demos) >= 5
    assert policy.W[0, 0] == pytest.approx(K_V, rel=0.01)
    assert policy.W[1, 1] == pytest.approx(K_W, rel=0.01)
    assert abs(policy.W[0, 1]) < 0.01 and abs(policy.W[1, 0]) < 0.01
    assert np.abs(policy.b).max() < 0.01


def test_the_cloned_policy_clears_the_mission_gate_under_noise(cloned):
    _, policy = cloned
    result = evaluate_mission(policy, NAVMESH, MISSION, SPEC, runs=30)
    assert result["success_rate"] >= MISSION_GATE and result["passed"] is True
    assert result["reached"] == result["outcomes"]["reached"]
    assert sum(result["outcomes"].values()) == 30 and result["outcomes"]["collided"] == 0
    assert 0 < result["median_time_s"] < 60
    assert 0 <= result["rms_deviation_m"] < 0.15
    assert result["median_miss_m"] <= 0.10
    assert result["closest_approach_m"] > SPEC.footprint_radius_m
    assert result["noise"]["source"] == "assumed"
    assert result["trace"][0] == pytest.approx(MISSION["start"][:2], abs=0.02)


def test_the_zero_policy_never_arrives():
    result = evaluate_mission(make_baseline(2, 2), NAVMESH, MISSION, SPEC, runs=4)
    assert result["outcomes"]["timeout"] == 4 and result["passed"] is False
    assert result["median_time_s"] is None and result["trace"]


def test_the_gate_fails_a_policy_that_cannot_steer():
    cannot_steer = LinearPolicy(np.diag([K_V, 0.0]), np.zeros(2))
    assert evaluate_mission(cannot_steer, NAVMESH, MISSION, SPEC, runs=5)["reached"] == 0


def test_drift_on_a_long_route_is_reported_as_stopping_off_the_goal():
    """A ±3° placement over 4.6 m: the controller steers true to its estimate and still misses."""
    sloppy = Noise(start_heading=0.05)
    result = evaluate_mission(GOOD, NAVMESH, LONG, SPEC, runs=10, noise=sloppy)
    assert result["outcomes"]["stopped_off_goal"] > 0
    assert result["outcomes"]["collided"] == 0
    assert result["median_drift_m"] > 0.05
    assert result["passed"] is False


def test_mission_runs_are_reproducible_with_a_seed():
    first = evaluate_mission(GOOD, NAVMESH, MISSION, SPEC, runs=3, seed=4)
    again = evaluate_mission(GOOD, NAVMESH, MISSION, SPEC, runs=3, seed=4)
    assert first == again


def test_without_a_mission_the_random_start_gate_still_applies():
    demos = record_demos(NAVMESH, (3.4, 3.4), SPEC, n=8)
    assert demos, "no demonstration finished"
    policy = finetune_bc(make_baseline(2, 2), demos)
    result = evaluate(policy, NAVMESH, (3.4, 3.4), SPEC, episodes=10)
    assert result["success_rate"] >= SIM_GATE and result["passed"] is True
    zero = evaluate(make_baseline(2, 2), NAVMESH, (3.4, 3.4), SPEC, episodes=5)
    assert zero["success_rate"] == 0.0


def test_rms_deviation_measures_distance_from_the_route():
    route = [(0.0, 0.0), (2.0, 0.0)]
    assert rms_deviation([(0.5, 0.0), (1.5, 0.0)], route) == pytest.approx(0.0)
    assert rms_deviation([(0.5, 0.1), (1.5, -0.1)], route) == pytest.approx(0.1)
    assert rms_deviation([(3.0, 0.0)], route) == pytest.approx(1.0)  # past the end of the route


def test_finetune_on_no_demos_returns_the_warm_start():
    baseline = make_baseline(2, 2)
    assert finetune_bc(baseline, []) is baseline


def test_policy_save_load_round_trip(tmp_path):
    policy = LinearPolicy(np.array([[0.4, 0.0], [0.0, 0.4]]), np.array([0.1, -0.1]))
    path = policy.save(str(tmp_path / "p.npz"))
    loaded = LinearPolicy.load(path)
    assert np.allclose(loaded.W, policy.W) and np.allclose(loaded.b, policy.b)
    assert loaded.obs_dim == 2 and loaded.act_dim == 2


def test_int8_quantization_round_trips_within_tolerance():
    rng = np.random.default_rng(0)
    policy = LinearPolicy(rng.uniform(-1, 1, (2, 2)), rng.uniform(-1, 1, 2))
    restored = dequantize_int8(policy.quantize_int8())
    assert np.abs(restored.W - policy.W).max() < 0.05
    assert np.abs(restored.b - policy.b).max() < 0.05


def test_quantizing_a_zero_policy_does_not_divide_by_zero():
    restored = dequantize_int8(make_baseline(2, 2).quantize_int8())
    assert np.allclose(restored.W, 0) and np.allclose(restored.b, 0)
