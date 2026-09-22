"""Run a quantized policy on a robot, measuring the inference cost per control tick.

The control law is policy/evaluate.py:drive — the same loop the gates run — so what is measured
here is what was validated. The robot is anything with the velocity interface
(robot/adapters/base.py): today the simulated rover; in Phase 4 the ESP32 rover runs the same law
on its microcontroller and this runner becomes its fake. Adapters without velocity control are
refused with a reason, never driven open-loop.

The per-tick timing is a measurement on this computer's CPU, not a claim about any device.
"""
import time

import numpy as np

from policy.evaluate import drive_outcome, path_length, time_limit_steps
from policy.finetune.train_bc import LinearPolicy, dequantize_int8

# What a failed drive means, in words an operator can act on.
FAILURES = {
    "stopped_off_goal": "stopped where its odometry put the goal, but drift had moved it",
    "collided": "the robot collided",
    "timeout": "ran out of time before reaching the goal",
}


def load_int8_policy(bundle_path: str) -> LinearPolicy:
    """Reconstruct the policy the export path quantized — the artifact that ships."""
    with np.load(bundle_path) as npz:
        return dequantize_int8({
            "W": {"data": npz["W"], "scale": float(npz["W_scale"])},
            "b": {"data": npz["b"], "scale": float(npz["b_scale"])},
        })


def run_policy(robot, policy, goal, start, path, spec, max_steps: int | None = None) -> dict:
    """Drive `robot` from `start` along `path` to `goal` under `policy`, timing every inference.

    Returns `reached`, the `trace` of true positions, and latency percentiles.
    """
    if not getattr(robot, "supports_drive", False):
        return {"reached": False, "trace": [],
                "error": "this robot adapter cannot take velocity commands"}
    if not path:
        return {"reached": False, "trace": [], "error": "no path to the goal"}
    if not robot.reset(start):
        return {"reached": False, "trace": [], "error": "the start pose is in collision"}

    latencies: list = []

    def timed(obs):
        began = time.perf_counter_ns()
        action = policy.act(obs)
        latencies.append((time.perf_counter_ns() - began) / 1e6)  # ms
        return action

    trace: list = []
    limit = max_steps or time_limit_steps(path_length(path), spec)
    outcome = drive_outcome(timed, robot, path, (float(goal[0]), float(goal[1])), limit, trace)
    result = {"reached": outcome == "reached", "outcome": outcome, "trace": trace,
              **_latency_stats(latencies)}
    if outcome in FAILURES:
        result["error"] = FAILURES[outcome]
    return result


def _latency_stats(latencies: list) -> dict:
    """p50/p95 rather than a mean — a control loop is judged on its tail, not average."""
    if not latencies:
        return {"ticks": 0}
    arr = np.asarray(latencies)
    return {
        "ticks": len(arr),
        "inference_p50_ms": round(float(np.percentile(arr, 50)), 4),
        "inference_p95_ms": round(float(np.percentile(arr, 95)), 4),
        "inference_max_ms": round(float(arr.max()), 4),
        "compute_unit": "cpu",
        "precision": "int8",
    }


def demo():
    """Self-check: a demonstrator-gain policy drives the rover model across an open room."""
    from policy.controller import K_V, K_W
    from policy.planning import plan_mission
    from policy.sim import Noise
    from robot.adapters.sim import SimRobot
    from robot.spec import load_spec

    spec = load_spec("rover")
    navmesh = {"origin": [-2.0, -2.0], "cell": 0.1, "width": 40, "height": 40,
               "grid": [[0] * 40 for _ in range(40)]}
    mission = plan_mission(navmesh, (-1.0, -1.0, 0.0), (1.0, 1.0))
    policy = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2))
    robot = SimRobot(navmesh, spec=spec, noise=Noise.from_spec(spec), seed=1)

    result = run_policy(robot, policy, mission["goal"], mission["start"], mission["path"], spec)
    assert result["reached"], result
    print(f"reached in {result['ticks']} ticks, "
          f"p50={result['inference_p50_ms']}ms p95={result['inference_p95_ms']}ms")


if __name__ == "__main__":
    demo()
