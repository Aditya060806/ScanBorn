import json
import math

import numpy as np
import pytest

from twin.generator import generate_twin
from twin.navmesh import (
    BLOCKED,
    CELL_COARSE,
    CELL_FINE,
    DROP,
    FINE_FLOOR_POINTS,
    FLOOR_CLEARANCE,
    FREE,
    MIN_POINTS,
    UNKNOWN,
    as_array,
    build_navmesh,
    choose_cell,
    decode_cells,
    encode_cells,
    fill_gaps,
    is_blocked,
    nearest_free,
    rasterise,
)
from twin.profile import profile_for

CELL = 0.1
FLOOR = {"id": "f", "label": "floor", "bbox3d": [0, 0, 0, 4, 4, 0.02], "confidence": 0.9}


def plane(x0, x1, y0, y1, z, step=0.025):
    """Horizontal surface sampled at cell-centred offsets, so no point sits on a cell edge."""
    gx, gy = np.meshgrid(np.arange(x0 + step / 2, x1, step), np.arange(y0 + step / 2, y1, step))
    return np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, float(z))])


def state_at(navmesh, x, y):
    row = math.floor((y - navmesh["origin"][1]) / navmesh["cell"])
    col = math.floor((x - navmesh["origin"][0]) / navmesh["cell"])
    return int(as_array(navmesh)[row, col])


def slow_rasterise(points, origin, cell, width, height, floor_z, sweep):
    """Point-by-point reference for the vectorised rasteriser."""
    floor, band, drop = (np.zeros((height, width), int) for _ in range(3))
    for x, y, z in points:
        col, row = math.floor((x - origin[0]) / cell), math.floor((y - origin[1]) / cell)
        if not (0 <= col < width and 0 <= row < height):
            continue
        dz = z - floor_z
        if dz < -DROP:
            drop[row, col] += 1
        elif dz < FLOOR_CLEARANCE:
            floor[row, col] += 1
        elif dz <= sweep:
            band[row, col] += 1
    grid = np.full((height, width), UNKNOWN, np.uint8)
    grid[floor >= MIN_POINTS] = FREE
    grid[(band >= MIN_POINTS) | (drop >= MIN_POINTS)] = BLOCKED
    return fill_gaps(grid)


def test_vectorised_rasteriser_matches_a_point_by_point_reference():
    rng = np.random.default_rng(3)
    n = 20_000
    xy = rng.uniform(-0.3, 4.3, (n, 2))
    z = rng.choice([0.0, 0.4, 1.5, -0.3], n, p=[0.7, 0.2, 0.05, 0.05]) + rng.normal(0, 0.01, n)
    unseen = (xy[:, 0] > 1) & (xy[:, 0] < 2) & (xy[:, 1] > 1) & (xy[:, 1] < 2)
    points = np.column_stack([xy, z])[~unseen]

    fast = rasterise(points, (0.0, 0.0), CELL, 40, 40, 0.0, 1.0)
    slow = slow_rasterise(points, (0.0, 0.0), CELL, 40, 40, 0.0, 1.0)
    assert np.array_equal(fast, slow)
    assert set(np.unique(fast).tolist()) == {FREE, BLOCKED, UNKNOWN}


def test_an_unscanned_hole_is_unknown_and_not_traversable():
    floor = plane(0, 4, 0, 4, 0.0)
    hole = (floor[:, 0] > 1.5) & (floor[:, 0] < 2.0) & (floor[:, 1] > 1.5) & (floor[:, 1] < 2.0)
    navmesh = build_navmesh([FLOOR], CELL, points=floor[~hole])

    assert state_at(navmesh, 1.75, 1.75) == UNKNOWN
    assert is_blocked(navmesh, 1.75, 1.75)
    assert state_at(navmesh, 3.0, 3.0) == FREE
    assert navmesh["counts"] == {"free": 1600 - 25, "blocked": 0, "unknown": 25}
    assert navmesh["version"] == 2
    assert navmesh["floor_z"] == pytest.approx(0.0)


def test_gap_fill_closes_gaps_one_cell_wide():
    grid = np.full((7, 9), FREE, np.uint8)
    grid[3, 2:6] = UNKNOWN
    assert (fill_gaps(grid) == FREE).all()


def test_gap_fill_leaves_a_real_hole_unknown():
    grid = np.full((9, 9), FREE, np.uint8)
    grid[3:6, 3:6] = UNKNOWN
    assert np.array_equal(fill_gaps(grid), grid)


def test_gap_fill_does_not_reach_into_the_shadow_beside_an_obstacle():
    grid = np.full((9, 12), FREE, np.uint8)
    grid[2:7, 9:11] = BLOCKED
    grid[2:7, 8] = UNKNOWN  # the unseen strip at the obstacle's foot
    filled = fill_gaps(grid)
    assert (filled[2:7, 8] == UNKNOWN).all()
    assert (filled[2:7, 9:11] == BLOCKED).all()


def test_two_stray_points_do_not_block_a_cell_but_three_do():
    floor = plane(0, 2, 0, 2, 0.0)
    room = [{"id": "f", "label": "floor", "bbox3d": [0, 0, 0, 2, 2, 0], "confidence": 0.9}]
    strays = np.array([[0.51, 0.51, 0.5], [0.52, 0.52, 0.5], [0.53, 0.53, 0.5]])

    two = build_navmesh(room, CELL, points=np.vstack([floor, strays[:2]]))
    three = build_navmesh(room, CELL, points=np.vstack([floor, strays]))
    assert state_at(two, 0.55, 0.55) == FREE
    assert state_at(three, 0.55, 0.55) == BLOCKED


def test_a_six_centimetre_object_blocks_and_a_three_centimetre_one_does_not():
    floor = plane(0, 4, 0, 4, 0.0)
    six = plane(1.0, 1.5, 1.0, 1.5, 0.06)
    three = plane(2.5, 3.0, 2.5, 3.0, 0.03)
    navmesh = build_navmesh([FLOOR], CELL, points=np.vstack([floor, six, three]))
    assert state_at(navmesh, 1.25, 1.25) == BLOCKED
    assert state_at(navmesh, 2.75, 2.75) == FREE
    assert FLOOR_CLEARANCE == 0.05


def test_a_step_down_blocks_its_cells():
    floor = plane(0, 4, 0, 4, 0.0)
    pit = (floor[:, 0] > 2.0) & (floor[:, 0] < 3.0) & (floor[:, 1] > 1.0) & (floor[:, 1] < 2.0)
    floor[pit, 2] = -0.20
    navmesh = build_navmesh([FLOOR], CELL, points=floor)
    assert state_at(navmesh, 2.5, 1.5) == BLOCKED
    assert state_at(navmesh, 0.5, 0.5) == FREE


def test_outliers_below_the_floor_do_not_shift_the_sweep_band():
    floor = plane(0, 4, 0, 4, 0.0)
    rng = np.random.default_rng(9)
    outliers = np.column_stack([rng.uniform(0, 4, 400), rng.uniform(0, 4, 400),
                                rng.uniform(-1.5, -0.5, 400)])
    navmesh = build_navmesh([FLOOR], CELL, points=np.vstack([floor, outliers]))
    assert navmesh["floor_z"] == pytest.approx(0.0, abs=0.01)
    # A min-z floor would sit ~1.5 m down and put the real floor inside the sweep band.
    assert navmesh["counts"]["free"] > 0.95 * 1600


LEGACY = {"origin": [0, 0], "cell": 0.1, "width": 3, "height": 2, "grid": [[0, 1, 0], [0, 0, 0]]}


def test_a_two_state_navmesh_from_before_reads_as_it_did():
    assert is_blocked(LEGACY, 0.15, 0.05)
    assert not is_blocked(LEGACY, 0.05, 0.05)
    assert is_blocked(LEGACY, -0.01, 0.05)  # just outside the origin is outside
    assert is_blocked(LEGACY, 0.05, 0.25)   # beyond the last row
    assert nearest_free(LEGACY, 0.12, 0.05) == pytest.approx((0.05, 0.05))


def old_nearest_free(navmesh, x, y):
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    best, best_d2 = None, None
    for row in range(navmesh["height"]):
        for col in range(navmesh["width"]):
            if navmesh["grid"][row][col]:
                continue
            cx, cy = x0 + (col + 0.5) * cell, y0 + (row + 0.5) * cell
            d2 = (cx - x) ** 2 + (cy - y) ** 2
            if best_d2 is None or d2 < best_d2:
                best, best_d2 = (cx, cy), d2
    return best


def test_nearest_free_never_returns_unknown_and_matches_the_old_loop():
    rng = np.random.default_rng(5)
    grid = rng.choice([FREE, BLOCKED, UNKNOWN], size=(30, 40), p=[0.3, 0.3, 0.4]).tolist()
    navmesh = {"origin": [-1.0, 2.0], "cell": 0.1, "width": 40, "height": 30, "grid": grid}
    for x, y in rng.uniform([-1.5, 1.5], [3.5, 5.5], (25, 2)):
        got = nearest_free(navmesh, x, y)
        assert got == pytest.approx(old_nearest_free(navmesh, x, y))
        assert state_at(navmesh, *got) == FREE
    assert nearest_free({**navmesh, "grid": [[UNKNOWN] * 40] * 30}, 0.0, 3.0) is None


def test_cells_round_trip_through_base64_with_row_zero_at_minimum_y():
    floor = plane(0, 4, 0, 4, 0.0)
    low_box = plane(0.0, 1.0, 0.0, 0.5, 0.5)  # an obstacle in the low-y strip
    navmesh = build_navmesh([FLOOR], CELL, points=np.vstack([floor, low_box]))
    grid = as_array(navmesh)
    decoded = decode_cells(encode_cells(grid), navmesh["width"], navmesh["height"])
    assert np.array_equal(decoded, grid)
    assert decoded[0, 0] == BLOCKED and decoded[-1, 0] == FREE


def test_without_a_cloud_the_boxes_decide_and_nothing_is_unknown():
    table = {"id": "t", "label": "table", "bbox3d": [2, 2, 0.7, 3, 3, 0.75], "confidence": 0.6}
    navmesh = build_navmesh([FLOOR, table], CELL)
    assert navmesh["floor_z"] is None
    assert navmesh["counts"]["unknown"] == 0 and navmesh["counts"]["blocked"] > 0
    assert is_blocked(navmesh, 2.5, 2.5) and not is_blocked(navmesh, 0.5, 0.5)


def test_with_no_objects_the_cloud_sets_the_extent():
    navmesh = build_navmesh([], CELL, points=plane(1, 3, -1, 1, 0.0))
    assert navmesh["origin"] == pytest.approx([1.0125, -0.9875])
    assert navmesh["width"] == navmesh["height"] == 20
    assert navmesh["counts"]["free"] > 0


def test_nothing_to_map_gives_an_empty_grid():
    navmesh = build_navmesh([], CELL)
    assert navmesh["width"] == navmesh["height"] == 0
    assert nearest_free(navmesh, 0.0, 0.0) is None
    assert is_blocked(navmesh, 0.0, 0.0)


def test_generate_twin_reports_the_floor_and_the_counts(tmp_path):
    result = generate_twin([FLOOR], str(tmp_path), points=plane(0, 4, 0, 4, 0.05),
                           profile=profile_for("home"))
    navmesh = json.load(open(result["navmesh_path"]))
    assert result["counts"] == navmesh["counts"]
    assert result["floor_z"] == pytest.approx(0.05)
    assert navmesh["profile"]["environment"] == "home"


def test_the_grid_is_fine_only_where_the_scan_is_dense_enough():
    dense = plane(0, 4, 0, 4, 0.0, step=0.02)   # ~25 floor points per 10 cm cell
    sparse = plane(0, 4, 0, 4, 0.0, step=0.04)  # ~6

    cell, evidence = choose_cell([FLOOR], dense)
    assert cell == CELL_FINE
    assert evidence["floor_points_per_10cm_cell_p25"] >= FINE_FLOOR_POINTS
    cell, evidence = choose_cell([FLOOR], sparse)
    assert cell == CELL_COARSE and evidence["reason"] == "too sparse for 5 cm cells"
    assert choose_cell([FLOOR])[0] == CELL_COARSE
    assert choose_cell([FLOOR], np.full((10, 3), np.nan))[0] == CELL_COARSE


def test_a_dense_scan_stays_seen_on_the_fine_grid():
    navmesh = build_navmesh([FLOOR], CELL_FINE, points=plane(0, 4, 0, 4, 0.0, step=0.02))
    assert navmesh["width"] == navmesh["height"] == 80
    assert navmesh["counts"]["unknown"] == 0


def test_generate_twin_picks_the_grid_from_the_scan_unless_told(tmp_path):
    dense = plane(0, 4, 0, 4, 0.0, step=0.02)
    auto = generate_twin([FLOOR], str(tmp_path / "auto"), points=dense)
    assert auto["resolution"]["cell_m"] == CELL_FINE
    assert auto["resolution"]["chosen"] == "auto"
    with open(auto["navmesh_path"]) as f:
        navmesh = json.load(f)
    assert navmesh["cell"] == CELL_FINE and navmesh["resolution"] == auto["resolution"]

    told = generate_twin([FLOOR], str(tmp_path / "told"), cell=CELL_COARSE, points=dense)
    assert told["resolution"] == {"cell_m": CELL_COARSE, "chosen": "requested"}
    assert generate_twin([FLOOR], str(tmp_path / "boxes"))["resolution"]["cell_m"] == CELL_COARSE
