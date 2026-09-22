import numpy as np
import pytest

from reconstruction.fast_path.fusion import fuse_frames, fuse_frames_with_stats
from reconstruction.reconstruct import read_ply, reconstruct

K = np.array([[10.0, 0, 8.0], [0, 10.0, 8.0], [0, 0, 1.0]])


def flat_floor_frame(z=2.0):
    # camera looking straight down at a floor plane z metres away
    return {
        "depth": np.full((16, 16), z, dtype=np.float32),
        "intrinsics": K,
        "pose": np.eye(4),
        "color": np.full((16, 16, 3), 128, dtype=np.uint8),
    }


def test_fuse_frames_bounds_and_dedup():
    f = flat_floor_frame()
    single, _ = fuse_frames([f], voxel_size=0.02)
    double, _ = fuse_frames([f, f], voxel_size=0.02)
    assert len(double) == len(single)  # identical frame dedups away
    assert np.allclose(single[:, 2], 2.0)  # all points on the plane
    assert single[:, 0].min() >= -2.0 and single[:, 0].max() <= 2.0


def test_fusion_keeps_what_two_frames_saw_and_drops_one_frame_speckles():
    first, second = flat_floor_frame(), flat_floor_frame()
    first["depth"][5, 5] = 1.0     # a speckle hovering 1 m under the camera, in one frame only
    second["depth"][10, 10] = 1.5  # and another in the other frame
    points, _, stats = fuse_frames_with_stats([first, second], voxel_size=0.02)
    assert not np.isclose(points[:, 2], 1.0).any() and not np.isclose(points[:, 2], 1.5).any()
    # The floor under each speckle was seen by one frame only, so it goes too.
    assert len(points) == 16 * 16 - 2
    assert stats == {"frames": 2, "min_views": 2, "voxels": 16 * 16 + 2, "dropped_voxels": 4}

    everything, _, loose = fuse_frames_with_stats([first, second], voxel_size=0.02, min_views=1)
    assert len(everything) == 16 * 16 + 2 and loose["dropped_voxels"] == 0


def test_a_one_frame_scan_keeps_every_voxel():
    points, _, stats = fuse_frames_with_stats([flat_floor_frame()], voxel_size=0.02)
    assert len(points) == 16 * 16 and stats["min_views"] == 1 and stats["dropped_voxels"] == 0


def test_many_pixels_of_one_frame_in_one_voxel_count_as_one_view():
    frame = flat_floor_frame()
    points, _, stats = fuse_frames_with_stats([frame], voxel_size=10.0, min_views=2)
    assert stats["min_views"] == 1  # one frame: nothing to require a second view from
    both, _, stats = fuse_frames_with_stats([frame, {**frame, "depth": frame["depth"] * 0}],
                                            voxel_size=10.0, min_views=2)
    assert len(both) == 0 and stats["dropped_voxels"] == stats["voxels"]


def test_reconstruct_writes_ply(tmp_path):
    f = flat_floor_frame()
    np.savez(tmp_path / "frame_00000.npz", **f)
    result = reconstruct(str(tmp_path), mode="fast")
    assert result["point_count"] == 16 * 16
    assert result["fusion"] == {"frames": 1, "min_views": 1, "voxels": 256, "dropped_voxels": 0}
    points, colors = read_ply(result["ply_path"])
    assert len(points) == 16 * 16
    assert tuple(colors[0]) == (128, 128, 128)


@pytest.mark.parametrize("colmap_on_path", [False, True])
def test_fidelity_is_not_implemented_whatever_is_installed(tmp_path, monkeypatch, colmap_on_path):
    # It once returned a status dict when COLMAP was on PATH, which /reconstruct turned into a 500.
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/colmap" if colmap_on_path else None)
    np.savez(tmp_path / "frame_00000.npz", **flat_floor_frame())
    with pytest.raises(NotImplementedError, match="mode='fast'"):
        reconstruct(str(tmp_path), mode="fidelity")


def test_unknown_mode_is_refused_before_any_work(tmp_path):
    np.savez(tmp_path / "frame_00000.npz", **flat_floor_frame())
    with pytest.raises(ValueError, match="unknown reconstruction mode"):
        reconstruct(str(tmp_path), mode="colmap")
    assert not (tmp_path / "mesh.ply").exists()


def test_empty_scan_raises(tmp_path):
    with pytest.raises(ValueError, match="no frame"):
        reconstruct(str(tmp_path), mode="fast")
