import numpy as np
import pytest

from reconstruction.floor import estimate_floor_z


def plane(x0, x1, y0, y1, z, step=0.02, noise=0.0, seed=0):
    xs, ys = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    zs = np.full(xs.shape, z, dtype=float)
    if noise:
        zs = zs + np.random.default_rng(seed).normal(0.0, noise, xs.shape)
    return np.column_stack([xs.ravel(), ys.ravel(), zs.ravel()])


def test_noisy_floor_under_a_large_table_is_still_the_floor():
    floor = plane(0, 4, 0, 4, 0.0, noise=0.005)
    table = plane(1, 3.2, 1, 3.2, 0.75, noise=0.005, seed=1)  # 30 % of the floor's area
    assert abs(estimate_floor_z(np.vstack([floor, table]))) < 0.01


def test_outliers_below_the_floor_do_not_move_it():
    floor = plane(0, 4, 0, 4, 0.0, noise=0.005)
    rng = np.random.default_rng(2)
    outliers = np.column_stack([rng.uniform(0, 4, 500), rng.uniform(0, 4, 500),
                                rng.uniform(-1.2, -0.2, 500)])
    cloud = np.vstack([floor, outliers])
    assert cloud[:, 2].min() < -0.2  # what a min-z floor would have used
    assert abs(estimate_floor_z(cloud)) < 0.01


def test_a_tight_clump_of_outliers_is_not_a_floor():
    floor = plane(0, 4, 0, 4, 0.0)
    clump = np.column_stack([np.full(600, 2.0), np.full(600, 2.0), np.full(600, -1.0)])
    assert abs(estimate_floor_z(np.vstack([floor, clump]))) < 0.01


def test_mostly_hidden_floor_is_found_below_a_denser_surface():
    # Furniture covers most of the floor: the table top is ~6x denser than the floor.
    floor = plane(0, 2, 0, 2, 0.0, step=0.05)
    table = plane(0, 2, 0, 2, 0.72, step=0.02)
    assert abs(estimate_floor_z(np.vstack([floor, table]))) < 0.01


def test_floor_at_any_height_and_heights_only():
    floor = plane(0, 3, 0, 3, 1.30, noise=0.003)
    assert abs(estimate_floor_z(floor) - 1.30) < 0.01
    assert abs(estimate_floor_z(floor[:, 2]) - 1.30) < 0.01


def test_far_garbage_heights_do_not_allocate_a_giant_histogram():
    floor = plane(0, 3, 0, 3, 0.0)
    garbage = np.array([[0.0, 0.0, -5e6], [0.0, 0.0, 5e6]])
    assert abs(estimate_floor_z(np.vstack([floor, garbage]))) < 0.01


def test_nan_heights_are_ignored_and_empty_input_raises():
    floor = plane(0, 2, 0, 2, 0.2)
    floor[::7, 2] = np.nan
    assert abs(estimate_floor_z(floor) - 0.2) < 0.01
    with pytest.raises(ValueError):
        estimate_floor_z(np.empty((0, 3)))
    with pytest.raises(ValueError):
        estimate_floor_z(np.array([[0.0, 0.0, np.nan]]))
