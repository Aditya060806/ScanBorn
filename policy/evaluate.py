"""Demonstrations and the sim gates, on the differential-drive rover model.

Navigation is split the way Nav2 splits it (section 11.3): policy/planning.py finds the route,
and the learned policy is the local controller that tracks it (policy/controller.py). Every drive
here runs in policy/sim.py's DiffDriveSim and steers from the pose the rover can estimate.

Two gates decide what may reach hardware:
- with a mission — MISSION_RUNS runs of that mission from its start under noise, at least
  MISSION_GATE of them reaching the goal;
- without one (the pipeline's older path) — EPISODES random starts, at least SIM_GATE.

A run ends in one of four outcomes. `reached`: the rover stopped where its own estimate said the
goal was, and it truly was within GOAL_TOLERANCE. `stopped_off_goal`: it stopped where it believed
the goal was, but dead-reckoning drift had put it elsewhere — the controller did its job and the
odometry did not. `collided`. `timeout`. Reporting them apart is what tells an operator whether a
failed gate means a bad policy or a route too long to drive without a position fix.
"""
import math

import numpy as np

from policy.controller import GOAL_TOLERANCE, at_goal, control_step, demonstrate
from policy.planning import ROBOT_RADIUS, clearance_field, inflate, plan_path  # noqa: F401
from policy.sim import DiffDriveSim, Noise

SIM_GATE = 0.6       # random-start success rate, no mission
MISSION_GATE = 0.9   # success rate over the mission's noisy runs
MISSION_RUNS = 30
EPISODES = 20
OUTCOMES = ("reached", "stopped_off_goal", "collided", "timeout")


def _centre_of(navmesh: dict, row: int, col: int) -> tuple:
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    return x0 + (col + 0.5) * cell, y0 + (row + 0.5) * cell


def free_cells(navmesh: dict) -> list:
    """Every traversable (x, y) centre in the navmesh."""
    return [
        _centre_of(navmesh, row, col)
        for row in range(navmesh["height"])
        for col in range(navmesh["width"])
        if not navmesh["grid"][row][col]
    ]


def path_length(path) -> float:
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(path, path[1:]))


def time_limit_steps(length_m: float, spec) -> int:
    """Ticks allowed for a route: three times its length at top speed, plus ten seconds."""
    return int(math.ceil((3.0 * length_m / spec.v_max + 10.0) / spec.dt))


def drive_outcome(act, sim, path, goal, max_steps: int, trace: list | None = None,
                  observations: list | None = None, commands: list | None = None) -> str:
    """One control loop — estimated pose, observation, command, plant — until the rover stops.

    The rover stops when its *estimate* is within STOP_RADIUS of the goal, as the real one will;
    the outcome is judged on its *true* pose. `act(obs)` is a policy's `act` or the
    demonstrator. `observations` and `commands` collect the pairs a demonstration is made of.
    `sim` is a DiffDriveSim or any robot with the same velocity interface.
    """
    if trace is not None:
        trace.append(sim.true_pose[:2])
    index = 0
    for _ in range(max_steps):
        command, obs, index = control_step(sim.estimated_pose, path, index, goal, act)
        if command is None:
            return "reached" if at_goal(sim.true_pose, goal, GOAL_TOLERANCE) else "stopped_off_goal"
        if observations is not None and commands is not None:
            observations.append(list(obs))
            commands.append(command)
        moved = sim.step(command[0], command[1])
        if trace is not None:
            trace.append(sim.true_pose[:2])
        if not moved:
            return "collided"
    return "timeout"


def drive(act, sim, path, goal, max_steps: int, trace: list | None = None,
          observations: list | None = None, commands: list | None = None) -> bool:
    """drive_outcome(...) == "reached"."""
    return drive_outcome(act, sim, path, goal, max_steps, trace, observations,
                         commands) == "reached"


def record_demos(navmesh: dict, goal, spec, n: int = 10, seed: int = 0, path=None, start=None,
                 noise: Noise | None = None) -> list:
    """Demonstrations driven by the expert in the noisy plant.

    The labels are the demonstrator's unclamped commands: limits belong to the plant, so the
    linear fit is exact. With a mission (`path` and `start`) every demonstration drives it from
    its start, each with its own noise; without one, they start from random cells where the robot
    fits, facing a random way, each with its own A* route.

    A drive that collided or timed out is dropped — a demonstration of a collision is worse than
    none. One that stopped off the goal is kept: its labels are exactly what the expert commanded
    from what the rover believed, and the miss was the odometry's, not the expert's.
    """
    rng = np.random.default_rng(seed)
    noise = Noise.from_spec(spec) if noise is None else noise
    field = clearance_field(navmesh)
    goal = (float(goal[0]), float(goal[1]))
    planning_mesh = inflate(navmesh) if path is None else None
    cells = free_cells(planning_mesh) if planning_mesh is not None else []
    demos: list = []

    for k in range(n):
        if path is not None and start is not None:
            route, pose = path, start
        else:
            if not cells or planning_mesh is None:
                break
            x, y = cells[int(rng.integers(len(cells)))]
            route = plan_path(planning_mesh, (x, y), goal)
            pose = (x, y, float(rng.uniform(-math.pi, math.pi)))
            if not route:
                continue
        sim = DiffDriveSim(navmesh, spec, noise, seed=seed * 1000 + k, field=field)
        if not sim.reset(pose):
            continue
        obs: list = []
        commands: list = []
        outcome = drive_outcome(demonstrate, sim, route, goal,
                                time_limit_steps(path_length(route), spec),
                                observations=obs, commands=commands)
        if outcome in ("reached", "stopped_off_goal") and obs:
            demos.append({"obs": np.array(obs), "actions": np.array(commands)})
    return demos


def evaluate(policy, navmesh: dict, goal, spec, episodes: int = EPISODES, seed: int = 1,
             noise: Noise | None = None) -> dict:
    """Success rate from random starts facing random ways — the gate without a mission.

    A start already inside the goal tolerance succeeds before the first tick and says nothing
    about the policy, so none is used.
    """
    rng = np.random.default_rng(seed)
    noise = Noise.from_spec(spec) if noise is None else noise
    goal = (float(goal[0]), float(goal[1]))
    planning_mesh = inflate(navmesh)
    cells = [c for c in free_cells(planning_mesh) if math.dist(c, goal) > GOAL_TOLERANCE]
    if not cells:
        return {"success_rate": 0.0, "episodes": 0, "passed": False}

    field = clearance_field(navmesh)
    successes = 0
    for k, i in enumerate(rng.choice(len(cells), size=episodes, replace=len(cells) < episodes)):
        x, y = cells[int(i)]
        route = plan_path(planning_mesh, (x, y), goal)
        sim = DiffDriveSim(navmesh, spec, noise, seed=seed * 1000 + k, field=field)
        if not route or not sim.reset((x, y, float(rng.uniform(-math.pi, math.pi)))):
            continue
        limit = time_limit_steps(path_length(route), spec)
        successes += drive(policy.act, sim, route, goal, limit)
    rate = successes / episodes
    return {"success_rate": rate, "episodes": episodes, "passed": rate >= SIM_GATE}


def rms_deviation(trace, path) -> float:
    """Root-mean-square distance from each trace point to the path polyline."""
    points = np.asarray(trace, dtype=np.float64).reshape(-1, 2)
    route = np.asarray(path, dtype=np.float64)[:, :2]
    if not len(points):
        return 0.0
    if len(route) < 2:
        return float(np.sqrt(np.mean(np.sum((points - route[:1]) ** 2, axis=1))))
    a, b = route[:-1], route[1:]
    ab = b - a
    lengths = np.maximum(np.sum(ab ** 2, axis=1), 1e-12)
    # (points, segments): project each point onto each segment, clamped to the segment
    offsets = points[:, None, :] - a[None]
    t = np.clip((offsets * ab[None]).sum(axis=2) / lengths[None], 0.0, 1.0)
    nearest = a[None] + t[..., None] * ab[None]
    distance = np.sqrt(((points[:, None, :] - nearest) ** 2).sum(axis=2)).min(axis=1)
    return float(np.sqrt(np.mean(distance ** 2)))


def _median(values: list, digits: int):
    return round(float(np.median(values)), digits) if values else None


def evaluate_mission(policy, navmesh: dict, mission: dict, spec, runs: int = MISSION_RUNS,
                     seed: int = 1, noise: Noise | None = None,
                     include_samples: bool = False) -> dict:
    """Drive the mission `runs` times from its start, each run with its own noise.

    Returns the gate result and what a person needs to judge it: the outcome counts; the median
    time and RMS distance off the route of the runs that arrived; for every run, the median miss
    (true distance to the goal where the rover stopped) and drift (estimated against true
    position there); the closest any run came to an obstacle (clearance at the true centre); and
    the trace of the median run by deviation — the representative drive, not the best-looking.
    """
    noise = Noise.from_spec(spec) if noise is None else noise
    path = [tuple(p) for p in mission["path"]]
    goal = (float(mission["goal"][0]), float(mission["goal"][1]))
    field = clearance_field(navmesh)
    limit = time_limit_steps(float(mission.get("length_m") or path_length(path)), spec)

    outcomes = {name: 0 for name in OUTCOMES}
    arrived_times: list = []
    arrived_deviation: list = []
    misses: list = []
    drifts: list = []
    closest: list = []
    candidates: list = []  # (deviation, reached, trace)
    samples: list = []
    for run in range(runs):
        sim = DiffDriveSim(navmesh, spec, noise, seed=seed * 1000 + run, field=field)
        trace: list = []
        if sim.reset(mission["start"]):
            outcome = drive_outcome(policy.act, sim, path, goal, limit, trace)
        else:
            outcome, trace = "collided", [sim.true_pose[:2]]
        outcomes[outcome] += 1
        deviation = rms_deviation(trace, path)
        misses.append(math.dist(sim.true_pose[:2], goal))
        drifts.append(math.dist(sim.estimated_pose[:2], sim.true_pose[:2]))
        closest.append(min(sim.clearance_at(x, y) for x, y in trace))
        if include_samples:
            samples.append({"seed": seed * 1000 + run, "outcome": outcome,
                            "miss_m": misses[-1], "drift_m": drifts[-1],
                            "duration_s": sim.t, "rms_deviation_m": deviation,
                            "clearance_m": closest[-1]})
        if outcome == "reached":
            arrived_times.append(sim.t)
            arrived_deviation.append(deviation)
        candidates.append((deviation, outcome == "reached", trace))

    pool = [c for c in candidates if c[1]] or candidates
    pool.sort(key=lambda c: c[0])
    representative = pool[len(pool) // 2][2] if pool else []
    rate = outcomes["reached"] / runs if runs else 0.0
    return {
        **({"samples": samples} if include_samples else {}),
        "gate_version": "mission-v1-success",
        "success_rate": round(rate, 4),
        "runs": runs,
        "reached": outcomes["reached"],
        "passed": rate >= MISSION_GATE,
        "gate": MISSION_GATE,
        "outcomes": outcomes,
        "median_time_s": _median(arrived_times, 2),
        "rms_deviation_m": _median(arrived_deviation, 4),
        "median_miss_m": _median(misses, 4),
        "median_drift_m": _median(drifts, 4),
        "closest_approach_m": round(min(closest), 4) if closest else None,
        "noise": {"source": spec.noise.source, "wheel_slip": noise.wheel_slip,
                  "gyro_bias_rad_s": noise.gyro_bias, "latency_ticks": noise.latency_ticks,
                  "start_xy_m": noise.start_xy, "start_heading_rad": noise.start_heading},
        "trace": [[round(float(x), 3), round(float(y), 3)] for x, y in representative],
    }
