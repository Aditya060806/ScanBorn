import copy

import pytest

from robot.accuracy_report import summarize


def record(kind="goal_error"):
    trials = [{"id": str(i), "deployment_id": f"drive-{i}", "completed": True,
               "collision": False, "stop_reason": "goal", "tape_radial_error_m": 0.04}
              for i in range(10)]
    if kind == "dimensions":
        trials = [{"id": str(i), "scan_id": f"scan-{i % 3}", "tape_m": 2.0,
                   "reconstructed_m": 2.03} for i in range(5)]
    return {"version": 1, "kind": kind, "provenance": "simulated", "session_id": "session-1",
            "operator": "test", "measured_at": "2026-09-20T00:00:00Z", "spec_sha256": "a" * 64,
            "planned_trial_ids": [t["id"] for t in trials], "trials": trials}


def test_simulation_never_becomes_physical_acceptance():
    report = summarize(record())
    assert report["passed_declared_measurements"]
    assert not report["physical_acceptance_candidate"]
    assert report["p95_m"] == 0.04


def test_failures_remain_in_denominator_and_collisions_veto():
    data = record()
    data["trials"][0].update(completed=False, tape_radial_error_m=None, stop_reason="timeout")
    result = summarize(data)
    assert result["attempts"] == 10 and result["unmeasured_attempts"] == 1
    assert result["within_target"] == 9 and result["passed_declared_measurements"]
    data["trials"][0]["collision"] = True
    assert not summarize(data)["passed_declared_measurements"]


def test_dimensions_need_three_scans_and_all_spans_within_target():
    data = record("dimensions")
    assert summarize(data)["passed_declared_measurements"]
    data["trials"][0]["reconstructed_m"] = 2.1
    assert not summarize(data)["passed_declared_measurements"]
    data = record("dimensions")
    for trial in data["trials"]:
        trial["scan_id"] = "same-scan"
    assert not summarize(data)["sufficient_samples"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, True, "0.01"])
def test_invalid_measurements_refused(value):
    data = record()
    data["trials"][0]["tape_radial_error_m"] = value
    with pytest.raises(ValueError):
        summarize(data)


def test_dropped_duplicate_and_unmeasured_completed_attempts_refused():
    for change in (lambda t: t.pop(), lambda t: t.append(copy.deepcopy(t[0])),
                   lambda t: t[0].update(tape_radial_error_m=None)):
        data = record()
        change(data["trials"])
        with pytest.raises(ValueError):
            summarize(data)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("measured_at", "yesterday"),
    ("measured_at", "2026-09-20T00:00:00"),
    ("spec_sha256", "not-a-checksum"), ("provenance", "unknown"),
])
def test_invalid_evidence_identity_is_refused(field, value):
    data = record()
    data[field] = value
    with pytest.raises(ValueError):
        summarize(data)
