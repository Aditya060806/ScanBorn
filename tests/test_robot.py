import numpy as np
import pytest

from policy.controller import K_V, K_W
from policy.finetune.train_bc import LinearPolicy
from policy.planning import plan_mission
from policy.sim import Noise
from robot.adapters.base import ScanBornRobot
from robot.adapters.registry import get_robot
from robot.policy_runner import run_policy
from robot.spec import load_spec
from robot.adapters.sim import SimRobot

# 4x4 m room, 0.1 m cells, with a blob blocked around (2.0, 2.0)
NAVMESH = {
    "origin": [0, 0], "cell": 0.1, "width": 40, "height": 40,
    "grid": [[1 if (18 <= r <= 22 and 18 <= c <= 22) else 0 for c in range(40)]
             for r in range(40)],
}


def test_sim_robot_walks_a_path_and_ends_at_the_last_waypoint():
    robot = SimRobot(NAVMESH)
    path = [(0.5, 0.5, 0.0), (1.0, 0.5, 0.0), (1.5, 0.5, 0.0)]
    trace = robot.execute_path(path)
    assert len(trace) == 3
    assert robot.get_pose() == (1.5, 0.5, 0.0)


def test_sim_robot_refuses_to_enter_a_blocked_cell():
    robot = SimRobot(NAVMESH)
    assert robot.move(0.5, 0.5) is True
    assert robot.move(2.0, 2.0) is False
    assert robot.get_pose() == (0.5, 0.5, 0.0)  # did not move


def test_execute_path_stops_at_the_obstacle():
    robot = SimRobot(NAVMESH)
    trace = robot.execute_path([(0.5, 0.5, 0), (2.0, 2.0, 0), (3.0, 3.0, 0)])
    assert trace == [(0.5, 0.5, 0.0)]  # never reached the waypoint past the obstacle


def test_sim_robot_without_a_navmesh_moves_freely():
    robot = SimRobot()
    assert robot.move(99.0, 99.0) is True


def test_registry_builds_adapters():
    assert isinstance(get_robot("sim"), SimRobot)
    for kind in ("mecha-godzilla", "unoq"):  # the UNO Q adapter was removed in Phase 7
        with pytest.raises(ValueError, match="unknown robot kind"):
            get_robot(kind)


class WaypointOnlyRobot(ScanBornRobot):
    """A robot that takes waypoints but no velocity commands."""

    def move(self, x, y, theta=0.0):
        return True

    def capture_frame(self) -> bytes:
        return b""

    def get_pose(self) -> tuple:
        return (0.0, 0.0, 0.0)


SPEC = load_spec("rover")
GOOD = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2))


def test_the_sim_robot_drives_a_mission_to_its_goal_under_noise():
    mission = plan_mission(NAVMESH, (0.5, 0.5, 0.0), (2.9, 1.0))  # ~2.5 m, within dead reckoning
    robot = SimRobot(NAVMESH, spec=SPEC, noise=Noise.from_spec(SPEC), seed=2)
    result = run_policy(robot, GOOD, mission["goal"], mission["start"], mission["path"], SPEC)
    assert result["reached"] is True, result.get("error")
    assert result["trace"][0] == pytest.approx([0.5, 0.5], abs=0.03)
    assert result["ticks"] > 0 and result["compute_unit"] == "cpu"
    assert robot.get_pose() == robot.true_pose


def test_the_sim_robot_stops_at_a_wall():
    robot = SimRobot(NAVMESH, spec=SPEC)
    # A route straight through the blocked blob around (2.0, 2.0).
    route = [(1.0 + i / 10, 2.05) for i in range(21)]
    result = run_policy(robot, GOOD, route[-1], (1.0, 2.05, 0.0), route, SPEC)
    assert result["reached"] is False and result["error"] == "the robot collided"
    assert max(x for x, _ in result["trace"]) < 1.8


def test_a_robot_without_velocity_control_is_refused_with_a_reason():
    result = run_policy(WaypointOnlyRobot(), GOOD, (1.0, 1.0),
                        (0.0, 0.0, 0.0), [(0.0, 0.0), (1.0, 1.0)], SPEC)
    assert result["reached"] is False and "velocity" in result["error"]


def test_a_sim_robot_without_a_spec_cannot_take_velocity_commands():
    with pytest.raises(RuntimeError, match="spec"):
        SimRobot(NAVMESH).reset((0.5, 0.5, 0.0))
