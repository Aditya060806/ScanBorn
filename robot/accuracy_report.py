"""Evaluate recorded A3/A8 measurements; never create measurements or certify a rover."""
import hashlib
import json
import math
from datetime import datetime

import numpy as np


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a nonempty identifier of at most 128 characters")
    return value


def summarize(record: dict) -> dict:
    """Require a complete predeclared trial roster so failures cannot disappear from denominators.

    Physical provenance is an operator assertion, not something software can independently verify.
    No returned field enables deployment or modifies calibrated specifications.
    """
    if (type(record.get("version")) is not int or record["version"] != 1 or
            record.get("kind") not in ("dimensions", "goal_error")):
        raise ValueError("Use measurement version 1 and dimensions or goal_error")
    if record.get("provenance") not in ("physical", "simulated"):
        raise ValueError("Declare physical or simulated provenance")
    for field in ("session_id", "operator", "measured_at", "spec_sha256"):
        _identifier(record.get(field), field)
    timestamp = datetime.fromisoformat(record["measured_at"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("measured_at must include its timezone")
    digest = record["spec_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("spec_sha256 must be the recorded specification's lowercase SHA-256")
    roster, trials = record.get("planned_trial_ids"), record.get("trials")
    if not isinstance(roster, list) or not roster or not isinstance(trials, list):
        raise ValueError("Provide the predeclared trial roster and every attempted trial")
    for identifier in roster:
        _identifier(identifier, "planned trial")
    if len(set(roster)) != len(roster):
        raise ValueError("Trial roster contains duplicate IDs")
    if any(not isinstance(t, dict) for t in trials):
        raise ValueError("Every trial must be an object")
    ids = [t.get("id") for t in trials]
    for identifier in ids:
        _identifier(identifier, "trial id")
    if len(set(ids)) != len(ids) or set(ids) != set(roster):
        raise ValueError("Every planned trial must appear exactly once, including failed attempts")
    errors, outcomes = [], []
    collisions = 0
    scans = set()
    for trial in trials:
        if record["kind"] == "dimensions":
            scans.add(_identifier(trial.get("scan_id"), "scan_id"))
            actual = _number(trial.get("tape_m"), "tape_m", positive=True)
            reconstructed = _number(trial.get("reconstructed_m"), "reconstructed_m", positive=True)
            error = abs(reconstructed - actual)
            limit = max(0.05, 0.02 * actual)
            passed = error <= limit + 1e-12
        else:
            if type(trial.get("completed")) is not bool or type(trial.get("collision")) is not bool:
                raise ValueError("Each goal attempt must declare completed and collision booleans")
            _identifier(trial.get("stop_reason"), "stop_reason")
            _identifier(trial.get("deployment_id"), "deployment_id")
            collisions += int(trial["collision"])
            value = trial.get("tape_radial_error_m")
            error = None if value is None else _number(value, "tape_radial_error_m")
            if trial["completed"] and error is None:
                raise ValueError("Completed attempts require tape-measured radial error")
            limit = 0.10
            passed = (trial["completed"] and not trial["collision"] and
                      error is not None and error <= limit)
        if error is not None:
            errors.append(error)
        outcomes.append({**trial, "error_m": error, "limit_m": limit, "passed": passed})
    successes = sum(t["passed"] for t in outcomes)
    if record["kind"] == "dimensions":
        enough = len(trials) >= 5 and len(scans) >= 3
        passed = enough and successes == len(trials)
        gate = "A3-v1-5-spans-3-scans"
    else:
        enough = len(trials) >= 10
        passed = enough and successes / len(trials) >= 0.9 and collisions == 0
        gate = "A8-v1-90pct-10cm-zero-collision"
    return {"gate_version": gate, "source_sha256": hashlib.sha256(json.dumps(
        record, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "provenance": record["provenance"], "session_id": record["session_id"],
        "operator": record["operator"], "measured_at": record["measured_at"],
        "spec_sha256": digest, "passed_declared_measurements": passed,
        "physical_acceptance_candidate": passed and record["provenance"] == "physical",
        "provenance_verification": "operator-declared; independent physical review required",
        "sufficient_samples": enough, "attempts": len(trials), "within_target": successes,
        "collisions": collisions, "measured_errors": len(errors),
        "unmeasured_attempts": len(trials) - len(errors),
        "median_m": float(np.median(errors)) if errors else None,
        "p95_m": float(np.percentile(errors, 95)) if errors else None,
        "max_m": max(errors) if errors else None, "trials": outcomes}
