import math
import time
from collections import deque

import numpy as np
import pytest
from scipy import ndimage

from policy.planning import (
    EPS,
    MissionError,
    astar,
    cell_of,
    centre_of,
    clearance_field,
    inflate,
    plan_mission,
    plan_path,
    shortcut,
    supercover,
    traversable,
)
from twin.navmesh import BLOCKED, UNKNOWN, is_blocked


def room(width, height, radius=0.25, blocked=(), unknown=(), cell=0.1):
    grid = np.zeros((height, width), np.uint8)
    for r0, r1, c0, c1 in blocked:
        grid[r0:r1, c0:c1] = BLOCKED
    for r0, r1, c0, c1 in unknown:
        grid[r0:r1, c0:c1] = UNKNOWN
    return {"origin": [0.0, 0.0], "cell": cell, "width": width, "height": height,
            "grid": grid.tolist(), "profile": {"robot_radius": radius}}


def touched(cells):
    return [cell for a, b in zip(cells, cells[1:]) for cell in supercover(*a, *b)] or list(cells)


def bfs_cells(ok, start, goal):
    """Uniform-cost 8-way search without corner cutting: the planner A* replaced."""
    height, width = ok.shape
    came: dict = {start: None}
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        if (r, c) == goal:
            break
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                if not (dr or dc) or not (0 <= nr < height and 0 <= nc < width):
                    continue
                if not ok[nr, nc] or (dr and dc and not (ok[r, nc] and ok[nr, c])):
                    continue
                if (nr, nc) not in came:
                    came[(nr, nc)] = (r, c)
                    queue.append((nr, nc))
    path, node = [], goal
    while node is not None:
        path.append(node)
        node = came[node]
    return path[::-1]


def test_open_floor_is_one_straight_segment():
    mission = plan_mission(room(60, 60), (1.0, 1.0), (5.0, 4.0))
    assert mission["waypoints"] == [[1.0, 1.0], [5.0, 4.0]]
    assert mission["length_m"] == pytest.approx(5.0, abs=1e-3)
    assert mission["start"] == pytest.approx([1.0, 1.0, math.atan2(3.0, 4.0)], abs=1e-3)
    spacing = [math.dist(a, b) for a, b in zip(mission["path"], mission["path"][1:])]
    assert max(spacing) <= 0.1 + 1e-3
    assert mission["plan_ms"] >= 0


def test_a_given_heading_is_kept():
    assert plan_mission(room(40, 40), (1.0, 1.0, 1.5), (3.0, 3.0))["start"][2] == 1.5


def test_a_star_keeps_its_distance_where_bfs_grazes_the_obstacle():
    navmesh = room(80, 50, blocked=[(20, 30, 35, 45)])  # a 1 m pillar between start and goal
    field = clearance_field(navmesh)
    mission = plan_mission(navmesh, (1.0, 2.5), (7.0, 2.5), field=field)

    ok = traversable(field, 0.25)
    bfs = bfs_cells(ok, cell_of(navmesh, 1.0, 2.5), cell_of(navmesh, 7.0, 2.5))
    assert min(field[r, c] for r, c in touched(bfs)) < 0.35
    assert mission["min_clearance_m"] >= 0.45


def test_shortcut_never_crosses_an_inflated_cell_nor_lowers_clearance():
    checked = 0
    for seed in range(50):
        rng = np.random.default_rng(seed)
        blocks = [(r, r + h, c, c + w) for (r, c), (h, w)
                  in zip(rng.integers(0, 36, (6, 2)), rng.integers(2, 9, (6, 2)))]
        navmesh = room(40, 40, radius=0.15, blocked=blocks)
        field = clearance_field(navmesh)
        ok = traversable(field, 0.15)
        free = np.argwhere(ok)
        a, b = free[rng.choice(len(free), 2, replace=False)]
        cells = astar(navmesh, centre_of(navmesh, *a), centre_of(navmesh, *b), field, 0.15)
        if not cells:
            continue  # different components; unreachable goals are tested below
        short = shortcut(cells, field, 0.15)
        assert short[0] == cells[0] and short[-1] == cells[-1]
        assert len(short) <= len(cells)
        assert all(ok[r, c] for r, c in touched(short))
        closest = min(field[r, c] for r, c in touched(cells))
        assert min(field[r, c] for r, c in touched(short)) >= closest - EPS
        checked += 1
    assert checked >= 30


def test_supercover_includes_both_cells_beside_a_corner():
    assert set(supercover(0, 0, 1, 1)) == {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert supercover(2, 1, 2, 4) == [(2, 1), (2, 2), (2, 3), (2, 4)]


def test_no_diagonal_squeezes_between_two_touching_corners():
    navmesh = room(3, 3, radius=0.0, blocked=[(0, 1, 1, 2), (1, 2, 0, 1)])
    assert astar(navmesh, (0.05, 0.05), (0.25, 0.25)) == []
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (0.05, 0.05), (0.25, 0.25))
    assert err.value.code == "unreachable"


def test_via_points_are_visited_in_order():
    mission = plan_mission(room(60, 60), (1.0, 1.0), (4.0, 1.0), via=[(1.0, 4.0), (4.0, 4.0)])
    waypoints = mission["waypoints"]

    def index_of(point):
        return next(i for i, w in enumerate(waypoints) if math.dist(point, w) < 1e-6)

    assert (index_of((1.0, 1.0)) < index_of((1.0, 4.0))
            < index_of((4.0, 4.0)) < index_of((4.0, 1.0)))
    assert mission["via"] == [[1.0, 4.0], [4.0, 4.0]]
    assert mission["length_m"] == pytest.approx(9.0, abs=0.01)


def test_a_start_on_an_obstacle_is_refused_with_the_nearest_valid_start():
    navmesh = room(40, 40, blocked=[(0, 40, 18, 22)])  # a wall across the room
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (2.0, 2.0), (3.5, 2.0))
    assert err.value.code == "start_blocked"
    assert "obstacle" in str(err.value)
    assert traversable(clearance_field(navmesh), 0.25)[cell_of(navmesh, *err.value.nearest)]


def test_a_goal_on_unscanned_floor_is_refused_with_a_reachable_alternative():
    navmesh = room(40, 40, unknown=[(15, 25, 25, 35)])
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (1.0, 1.0), (3.0, 2.0))
    assert err.value.code == "goal_blocked"
    assert "never saw" in str(err.value)
    detail = err.value.as_detail()
    assert detail["code"] == "goal_blocked" and len(detail["nearest"]) == 2
    assert plan_mission(navmesh, (1.0, 1.0), err.value.nearest)["waypoints"]


def test_a_goal_too_close_to_a_wall_says_so():
    navmesh = room(40, 40, blocked=[(0, 40, 30, 40)])
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (1.0, 1.0), (2.85, 2.0))
    assert err.value.code == "goal_blocked"
    assert "closer than the robot fits" in str(err.value)


def test_a_goal_inside_a_closed_ring_is_unreachable_and_the_alternative_is_reachable():
    ring = [(10, 12, 10, 30), (28, 30, 10, 30), (10, 30, 10, 12), (10, 30, 28, 30)]
    navmesh = room(40, 40, radius=0.1, blocked=ring)
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (0.5, 0.5), (2.0, 2.0))
    assert err.value.code == "unreachable"
    assert plan_mission(navmesh, (0.5, 0.5), err.value.nearest)["waypoints"]


def test_a_via_point_on_an_obstacle_names_its_index():
    navmesh = room(40, 40, blocked=[(18, 22, 18, 22)])
    with pytest.raises(MissionError) as err:
        plan_mission(navmesh, (0.5, 0.5), (3.5, 3.5), via=[(1.0, 3.0), (2.0, 2.0)])
    assert err.value.code == "via_blocked"
    assert err.value.index == 1 and err.value.as_detail()["index"] == 1


def test_start_and_goal_in_one_place_is_a_one_point_mission():
    mission = plan_mission(room(20, 20), (1.0, 1.0), (1.0, 1.0))
    assert mission["waypoints"] == [[1.0, 1.0]]
    assert mission["length_m"] == 0.0


def test_an_empty_navmesh_cannot_be_planned_on():
    empty = {"origin": [0, 0], "cell": 0.1, "width": 0, "height": 0, "grid": []}
    with pytest.raises(MissionError):
        plan_mission(empty, (0.0, 0.0), (1.0, 1.0))


def test_a_large_furnished_grid_plans_well_under_a_second():
    rng = np.random.default_rng(11)
    blocks = [(r, r + 12, c, c + 12) for r, c in rng.integers(10, 180, (25, 2))]
    navmesh = room(200, 200, blocked=blocks)

    began = time.perf_counter()
    field = clearance_field(navmesh)
    ok = traversable(field, 0.25)
    labels, _ = ndimage.label(ok)
    rows, cols = np.nonzero(ok)
    start = centre_of(navmesh, rows[0], cols[0])
    same = labels[rows, cols] == labels[rows[0], cols[0]]
    far = np.argmax(np.where(same, rows + cols, -1))
    goal = centre_of(navmesh, rows[far], cols[far])
    mission = plan_mission(navmesh, start, goal, field=field)
    elapsed = time.perf_counter() - began

    assert mission["length_m"] > 20
    assert elapsed < 1.0, f"{elapsed:.3f} s"


def old_inflate(navmesh, radius):
    """The loop-based inflation this module replaced, kept as the reference."""
    cell, grid = navmesh["cell"], navmesh["grid"]
    height, width = navmesh["height"], navmesh["width"]
    reach = int(np.ceil(radius / cell))
    inflated = [row[:] for row in grid]
    for row in range(height):
        for col in range(width):
            if not grid[row][col]:
                continue
            for drow in range(-reach, reach + 1):
                for dcol in range(-reach, reach + 1):
                    nr, nc = row + drow, col + dcol
                    if 0 <= nr < height and 0 <= nc < width:
                        if (drow * cell) ** 2 + (dcol * cell) ** 2 <= radius ** 2:
                            inflated[nr][nc] = 1
    return inflated


def test_inflate_matches_the_old_loops_and_also_keeps_off_the_map_edge():
    grid = np.random.default_rng(4).choice([0, 1, 2], size=(30, 30), p=[0.9, 0.07, 0.03])
    navmesh = {"origin": [0, 0], "cell": 0.1, "width": 30, "height": 30,
               "grid": grid.tolist(), "profile": {"robot_radius": 0.25}}
    new = np.array(inflate(navmesh)["grid"])
    old = np.array(old_inflate(navmesh, 0.25))

    interior = (slice(2, 28), slice(2, 28))  # more than 0.25 m from the edge
    assert np.array_equal(new[interior] != 0, old[interior] != 0)
    assert (new[grid == UNKNOWN] == UNKNOWN).all()
    assert (new[:2, :] != 0).all() and (new[:, -2:] != 0).all()


def test_plan_path_on_an_inflated_mesh_ends_at_the_goal_one_cell_at_a_time():
    navmesh = inflate(room(40, 40, blocked=[(15, 25, 15, 25)]))
    path = plan_path(navmesh, (0.55, 0.55), (3.45, 3.45))
    assert path[-1] == (3.45, 3.45)
    assert all(math.dist(a, b) <= 0.1 + 1e-6 for a, b in zip(path, path[1:]))
    assert not any(is_blocked(navmesh, x, y) for x, y in path)
    assert plan_path(navmesh, (2.0, 2.0), (3.45, 3.45)) == []  # start inside the block
