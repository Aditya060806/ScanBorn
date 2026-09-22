"""Versioned release evidence, separate from historical mission deployment validation.

Fixed geometry and reserved evaluation seeds must not be used for training or tuning.
This is simulated acceptance evidence, never a physical accuracy measurement.
"""
import hashlib
import json

import numpy as np

from policy.evaluate import evaluate_mission
from policy.planning import MissionError, plan_mission

VERSION = "release-v1-100-zero-collision"
RUNS = 100
MIN_SUCCESS = 0.9


def representative_cases() -> list:
    def room():
        return {"origin": [0, 0], "cell": 0.1, "width": 50, "height": 50,
                "grid": [[0] * 50 for _ in range(50)], "profile": {"robot_radius": 0.2}}

    open_room = room()
    obstacle = room()
    for row in range(16, 24):
        for col in range(19, 23):
            obstacle["grid"][row][col] = 1
    blocked = room()
    for row in blocked["grid"]:
        row[25] = 2  # An unknown strip cannot silently become traversable.
    return [
        {"id": "straight-1m", "navmesh": open_room, "start": [1, 1, 0], "goal": [2, 1]},
        {"id": "corner-2m", "navmesh": open_room, "start": [1, 1, 0], "goal": [2, 2],
         "via": [[2, 1]]},
        {"id": "obstacle-detour", "navmesh": obstacle, "start": [1.3, 2, 0], "goal": [2.8, 2]},
        {"id": "blocked-goal", "navmesh": obstacle, "start": [1, 1, 0], "goal": [2, 2],
         "expect_refusal": True},
        {"id": "unknown-barrier", "navmesh": blocked, "start": [1, 2, 0], "goal": [3, 2],
         "expect_refusal": True},
    ]


def release_pass(metrics: dict) -> bool:
    """A high success rate cannot hide even one collision in the release suite."""
    return (metrics["runs"] == RUNS and metrics["reached"] >= MIN_SUCCESS * RUNS and
            metrics["outcomes"]["collided"] == 0)


def evaluate_release(float_policy, exported_policy, spec) -> dict:
    cases = representative_cases()
    for case in cases:
        case["navmesh"]["profile"]["robot_radius"] = max(0.2, spec.footprint_radius_m)
    fingerprint = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()
    results = []
    for index, case in enumerate(cases):
        entry = {"id": case["id"], "expected_refusal": case.get("expect_refusal", False)}
        try:
            mission = plan_mission(case["navmesh"], case["start"], case["goal"],
                                   case.get("via", []))
        except MissionError as exc:
            entry.update(refused=True, reason=str(exc), passed=entry["expected_refusal"])
            results.append(entry)
            continue
        if entry["expected_refusal"]:
            entry.update(refused=False, passed=False,
                         reason="Unsafe route was unexpectedly accepted")
            results.append(entry)
            continue
        measurements = {}
        for name, policy in (("float", float_policy), ("int8", exported_policy)):
            metrics = evaluate_mission(policy, case["navmesh"], mission, spec,
                                       runs=RUNS, seed=93001 + index, include_samples=True)
            samples = metrics["samples"]
            metrics.update(gate_version=VERSION, passed=release_pass(metrics),
                           p95_miss_m=float(np.percentile([s["miss_m"] for s in samples], 95)))
            measurements[name] = metrics
        entry.update(refused=False, mission=mission, measurements=measurements,
                     passed=all(m["passed"] for m in measurements.values()),
                     quantization_success_delta=(measurements["int8"]["success_rate"] -
                                                 measurements["float"]["success_rate"]))
        results.append(entry)
    return {"gate_version": VERSION, "provenance": "simulated", "suite_sha256": fingerprint,
            "robot_spec": spec.as_dict(), "runs_per_policy_per_case": RUNS,
            "required_success_rate": MIN_SUCCESS, "maximum_collisions": 0,
            "held_out_seed_bases": [93001, 93002, 93003],
            "training_or_tuning_on_these_seeds_invalidates_held_out_claim": True,
            "passed": all(case["passed"] for case in results), "cases": results}
