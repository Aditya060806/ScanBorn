"""The rover's local controller: pure pursuit on the mission path, as a linear map.

The observation is what the rover can compute from its estimated pose and the path it was sent:
`[d·cos e, e]`, the forward distance to a lookahead point in the rover's own frame and the heading
error to it. The demonstrator is `v = K_V·d·cos e`, `ω = K_W·e` — linear in the observation, so a
linear policy represents it exactly and behaviour cloning recovers the gains.

When the lookahead point is behind the rover, `d·cos e` is negative; the plant clamps `v` to 0
and the rover turns in place. Labels are the unclamped output: limits belong to the plant.
"""
import math

from policy.sim import wrap_angle

LOOKAHEAD = 0.35       # m
GOAL_TOLERANCE = 0.10  # m, the plan's bar for a real drive (§11)
# m: the rover stops once its own estimate is this close to the goal. Measured in the rover model:
# 5 cm left a 5.9 cm median miss on a 2.5 m route; 2 cm leaves 3.8 cm, for 0.8 s more driving.
STOP_RADIUS = 0.02
K_V = 1.0              # 1/s
K_W = 2.5              # 1/s
OBSERVATION = ("forward_distance_to_lookahead_m", "heading_error_rad")
ACTION = ("v_mps", "omega_radps")


def _distance(pose, point) -> float:
    return math.hypot(point[0] - pose[0], point[1] - pose[1])


def lookahead_index(pose, path, index: int, distance: float = LOOKAHEAD) -> int:
    """The path point to steer at: never behind `index`, and at least `distance` from `pose`.

    The index first slides forward to the nearest path point ahead of it — so a rover part way
    along its route does not steer back towards the start — then on to the first point at least
    `distance` away. It never moves backwards, so a route that doubles back is followed in order.
    """
    last = len(path) - 1
    while index < last and _distance(pose, path[index + 1]) <= _distance(pose, path[index]):
        index += 1
    while index < last and _distance(pose, path[index]) < distance:
        index += 1
    return index


def observe(pose, path, index: int, distance: float = LOOKAHEAD) -> tuple:
    """([d·cos e, e], index) for the lookahead point from `pose` (x, y, heading)."""
    index = lookahead_index(pose, path, index, distance)
    dx, dy = path[index][0] - pose[0], path[index][1] - pose[1]
    d = math.hypot(dx, dy)
    e = wrap_angle(math.atan2(dy, dx) - pose[2]) if d > 1e-9 else 0.0
    return [d * math.cos(e), e], index


def demonstrate(obs) -> list:
    """The expert the policy is cloned from."""
    return [K_V * obs[0], K_W * obs[1]]


def at_goal(pose, goal, tolerance: float = GOAL_TOLERANCE) -> bool:
    return math.hypot(pose[0] - goal[0], pose[1] - goal[1]) <= tolerance


def control_step(estimate, path, index: int, goal, act, stop_radius: float = STOP_RADIUS) -> tuple:
    """One tick of the rover's loop, from the pose it believes: (command, obs, index).

    `command` is None once the final path point is current and the estimate is within
    `stop_radius` of the goal. A route returning to its start must still traverse its path.
    Otherwise it is the policy's (v, ω) for the lookahead observation, before the plant's limits.
    Every loop runs exactly this: the gates (policy/evaluate.py), the fake rover, and the firmware
    (firmware/scanborn_rover/rover_math.h), which the golden vectors hold to it.
    """
    obs, index = observe(estimate, path, index)
    if index == len(path) - 1 and at_goal(estimate, goal, stop_radius):
        return None, None, index
    command = act(obs)
    return [float(command[0]), float(command[1])], obs, index


def policy_manifest(spec) -> dict:
    """What an exported policy reads and commands: written into its manifest for the firmware.

    An artifact without `observation` predates the differential-drive simulator, and the
    orchestrator refuses to deploy it rather than feed its weights the wrong inputs.
    """
    return {
        "observation": list(OBSERVATION),
        "action": list(ACTION),
        "controller": {"lookahead_m": LOOKAHEAD, "goal_tolerance_m": GOAL_TOLERANCE,
                       "stop_radius_m": STOP_RADIUS, "demonstrator": {"k_v": K_V, "k_w": K_W}},
        "robot_spec": {"name": spec.name, "source": spec.source},
        "robot_spec_snapshot": spec.as_dict(),
    }
