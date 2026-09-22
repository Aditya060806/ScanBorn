"""Release acceptance cannot hide collisions behind a high aggregate success rate."""
import pytest

from policy import release_evaluation as release
from policy.planning import MissionError, plan_mission
from robot.spec import load_spec


def metrics(reached=100, collided=0):
    return {"runs": 100, "reached": reached, "success_rate": reached / 100,
            "outcomes": {"collided": collided}, "passed": True,
            "samples": [{"miss_m": 0.01}] * 100}


def test_collision_veto_and_sample_count():
    assert release.release_pass(metrics(90))
    assert not release.release_pass(metrics(99, 1))
    assert not release.release_pass(metrics(89))
    assert not release.release_pass({**metrics(), "runs": 30})


def test_fixed_cases_include_real_route_refusals():
    for case in release.representative_cases():
        args = (case["navmesh"], case["start"], case["goal"], case.get("via", []))
        if case.get("expect_refusal"):
            with pytest.raises(MissionError):
                plan_mission(*args)
        else:
            assert plan_mission(*args)["path"]


def test_float_pass_cannot_mask_int8_failure_and_seeds_are_paired(monkeypatch):
    calls = []

    def evaluate(policy, *args, **kwargs):
        calls.append((policy, kwargs))
        return metrics(99, 1) if policy == "int8" else metrics()

    monkeypatch.setattr(release, "evaluate_mission", evaluate)
    result = release.evaluate_release("float", "int8", load_spec("rover"))
    assert not result["passed"]
    assert result["gate_version"] == release.VERSION
    assert len(calls) == 6
    for before, after in zip(calls[::2], calls[1::2]):
        assert before[1] == after[1]
        assert before[1]["runs"] == 100 and before[1]["seed"] >= 93001
    assert all(c["passed"] for c in result["cases"] if c["expected_refusal"])
