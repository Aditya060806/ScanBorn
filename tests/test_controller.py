import math

import pytest

from policy.controller import (
    GOAL_TOLERANCE,
    K_V,
    K_W,
    LOOKAHEAD,
    at_goal,
    demonstrate,
    lookahead_index,
    observe,
    control_step,
)
from policy.sim import wrap_angle

STRAIGHT = [(x / 10, 0.0) for x in range(0, 31)]  # 3 m along +x, a point every 10 cm


def test_a_point_straight_ahead_is_all_forward_and_no_turn():
    obs, index = observe((0.0, 0.0, 0.0), STRAIGHT, 0)
    assert obs[1] == pytest.approx(0.0)
    assert obs[0] == pytest.approx(STRAIGHT[index][0])
    assert obs[0] >= LOOKAHEAD
    v, w = demonstrate(obs)
    assert v == pytest.approx(K_V * obs[0]) and w == pytest.approx(0.0)


def test_a_point_behind_asks_for_no_forward_speed_so_the_rover_turns_in_place():
    obs, _ = observe((0.0, 0.0, math.pi), STRAIGHT, 0)
    v, w = demonstrate(obs)
    assert v <= 0.0
    assert abs(w) == pytest.approx(K_W * math.pi)


def test_a_point_to_the_left_turns_left():
    obs, _ = observe((0.0, 0.0, -math.pi / 2), STRAIGHT, 0)
    assert obs[1] == pytest.approx(math.pi / 2)
    assert demonstrate(obs)[1] > 0


def test_the_lookahead_never_moves_back_along_the_path():
    assert lookahead_index((0.0, 0.0, 0.0), STRAIGHT, 20) == 20
    assert lookahead_index((2.95, 0.0, 0.0), STRAIGHT, 0) == len(STRAIGHT) - 1


def test_angles_wrap_into_one_turn():
    assert abs(wrap_angle(3 * math.pi)) == pytest.approx(math.pi)
    assert wrap_angle(-0.5) == pytest.approx(-0.5)
    assert wrap_angle(2 * math.pi + 0.25) == pytest.approx(0.25)


def test_the_goal_tolerance_is_the_real_drive_bar():
    assert GOAL_TOLERANCE == 0.10
    assert at_goal((1.0, 1.05), (1.0, 1.0)) and not at_goal((1.0, 1.2), (1.0, 1.0))


def test_closed_route_does_not_finish_before_traversing_its_path():
    from policy.planning import densify

    square = densify([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)], 0.1)
    command, _, index = control_step((0, 0, 0), square, 0, (0, 0), demonstrate)
    assert command is not None and command[0] > 0
    assert index < len(square) - 1
    command, _, _ = control_step((0, 0, -math.pi / 2), square, len(square) - 2,
                                 (0, 0), demonstrate)
    assert command is None
