"""The axis check on synthetic walks (labelled synthetic until the S24 FE walk is recorded)."""
import numpy as np

from scripts.check_scan_axes import check, heading, poses_from_scan

# Camera -> map rotations, columns = the OpenCV camera's +X (right), +Y (down), +Z (view).
FACING_Y = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
FACING_X = np.array([[0, 0, 1.0], [-1.0, 0, 0], [0, -1.0, 0]])
LOOKING_DOWN = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])


def walk(dx: float, dy: float, steps: int = 11, rotation=FACING_Y) -> list:
    """A phone carried at 1.3 m, moving (dx, dy) per frame."""
    poses = []
    for i in range(steps):
        pose = np.eye(4)
        pose[:3, :3] = rotation
        pose[:3, 3] = [dx * i, dy * i, 1.3]
        poses.append(pose.tolist())
    return poses


def test_a_straight_2m_walk_along_y_passes():
    result = check(walk(0.0, 0.2), walk_m=2.0)
    assert result["passed"] and result["along_y_m"] == 2.0 and result["sideways_x_m"] == 0.0
    assert result["first_heading_error_deg"] == 0.0


def test_sideways_drift_or_a_short_walk_fails():
    drifted = check(walk(0.05, 0.2), walk_m=2.0)
    assert not drifted["passed"] and not drifted["sideways_ok"] and drifted["along_ok"]
    short = check(walk(0.0, 0.15), walk_m=2.0)
    assert not short["passed"] and not short["along_ok"]


def test_a_scan_whose_start_faced_x_fails():
    result = check(walk(0.0, 0.2, rotation=FACING_X), walk_m=2.0)
    assert not result["passed"] and not result["facing_ok"]
    assert result["first_heading_error_deg"] == 90.0


def test_looking_straight_down_has_no_heading():
    assert heading(walk(0.0, 0.2, steps=1, rotation=LOOKING_DOWN)[0]) is None
    assert not check(walk(0.0, 0.2, rotation=LOOKING_DOWN), walk_m=2.0)["facing_ok"]


def test_one_frame_is_not_a_walk():
    assert not check(walk(0.0, 0.2, steps=1), walk_m=2.0)["passed"]


def test_poses_are_read_from_a_scan_directory_in_frame_order(tmp_path):
    for i, pose in enumerate(walk(0.0, 0.2, steps=3)):
        np.savez(tmp_path / f"frame_{i:05d}.npz", depth=np.zeros((2, 2), dtype=np.float32),
                 intrinsics=np.eye(3), pose=np.asarray(pose, dtype=np.float32))
    poses = poses_from_scan(str(tmp_path))
    assert [round(p[1][3], 3) for p in poses] == [0.0, 0.2, 0.4]
