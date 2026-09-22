"""Fast-path reconstruction: depth-frame fusion into a voxel-deduped point cloud.

Frames are dicts with keys: depth (H,W float32 metres), intrinsics (3,3),
pose (4,4 camera->world), optional color (H,W,3 uint8).
Uses Open3D TSDF when available; the numpy path is the always-works fallback.
"""
import numpy as np

MIN_VIEWS = 2  # a voxel must be seen from this many frames to survive fusion


def backproject(frame: dict) -> tuple:
    """One RGB-D frame -> (Nx3 world points, Nx3 uint8 colors)."""
    depth = np.asarray(frame["depth"], dtype=np.float64)
    K = np.asarray(frame["intrinsics"], dtype=np.float64)
    pose = np.asarray(frame["pose"], dtype=np.float64)
    h, w = depth.shape
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    valid = depth > 0
    z = depth[valid]
    x = (u[valid] - K[0, 2]) * z / K[0, 0]
    y = (v[valid] - K[1, 2]) * z / K[1, 1]
    pts_cam = np.stack([x, y, z, np.ones_like(z)], axis=1)
    pts = (pose @ pts_cam.T).T[:, :3]
    if frame.get("color") is not None:
        colors = np.asarray(frame["color"])[valid]
    else:
        colors = np.full((len(pts), 3), 200, dtype=np.uint8)
    return pts, colors


def voxel_dedup(points, colors, voxel_size: float):
    """Keep one point (first seen) per voxel cell."""
    keys = np.floor(points / voxel_size).astype(np.int64)
    _, idx = np.unique(keys, axis=0, return_index=True)
    idx.sort()
    return points[idx], colors[idx]


def _voxel_codes(points, voxel_size: float) -> np.ndarray:
    """Each point's voxel as one int64, so voxels can be counted with a 1-D unique."""
    keys = np.floor(points / voxel_size).astype(np.int64)
    keys -= keys.min(axis=0)
    span = keys.max(axis=0) + 1
    return (keys[:, 0] * span[1] + keys[:, 1]) * span[2] + keys[:, 2]


def fuse_frames_with_stats(frames, voxel_size: float = 0.02,
                           min_views: int = MIN_VIEWS) -> tuple:
    """Fuse RGB-D frames -> (points, colors, stats), keeping voxels several frames saw.

    A voxel survives when points from at least `min_views` distinct frames fall in it (one frame
    hitting a voxel with many pixels counts once): a surface the phone saw from several frames
    stays, a speckle in one frame does not. A scan with fewer frames than `min_views` keeps every
    voxel.
    The kept point per voxel is the first one seen, as in `voxel_dedup`.
    """
    all_pts, all_cols, frame_ids = [], [], []
    for index, frame in enumerate(frames):
        points, colors = backproject(frame)
        all_pts.append(points)
        all_cols.append(colors)
        frame_ids.append(np.full(len(points), index, dtype=np.int64))
    count = len(all_pts)
    effective = max(1, min(int(min_views), count))
    stats = {"frames": count, "min_views": effective, "voxels": 0, "dropped_voxels": 0}
    if count == 0 or not sum(len(p) for p in all_pts):
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.uint8), stats

    points = np.concatenate(all_pts)
    colors = np.concatenate(all_cols)
    frame_of = np.concatenate(frame_ids)
    _, first, voxel_of = np.unique(_voxel_codes(points, voxel_size), return_index=True,
                                   return_inverse=True)
    voxel_of = voxel_of.reshape(-1)
    seen = np.unique(voxel_of * count + frame_of)  # distinct (voxel, frame) pairs
    views = np.bincount(seen // count, minlength=len(first))
    keep = views >= effective
    stats.update(voxels=int(len(first)), dropped_voxels=int((~keep).sum()))
    idx = np.sort(first[keep])
    return points[idx], colors[idx], stats


def fuse_frames(frames, voxel_size: float = 0.02, min_views: int = MIN_VIEWS) -> tuple:
    """Fuse RGB-D frames into a voxel-deduped point cloud (see `fuse_frames_with_stats`)."""
    points, colors, _ = fuse_frames_with_stats(frames, voxel_size, min_views)
    return points, colors


def tsdf_fusion(frames, voxel_size: float = 0.02):
    """Open3D ScalableTSDFVolume backend; raises ImportError without open3d."""
    import open3d as o3d

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel_size,
        sdf_trunc=voxel_size * 4,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    for f in frames:
        depth = np.asarray(f["depth"], dtype=np.float32)
        color = np.asarray(
            f.get("color") if f.get("color") is not None
            else np.full((*depth.shape, 3), 200, dtype=np.uint8)
        )
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(color)),
            o3d.geometry.Image(depth),
            depth_scale=1.0, depth_trunc=10.0, convert_rgb_to_intensity=False,
        )
        K = np.asarray(f["intrinsics"])
        h, w = depth.shape
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            w, h, K[0, 0], K[1, 1], K[0, 2], K[1, 2]
        )
        volume.integrate(rgbd, intrinsic, np.linalg.inv(np.asarray(f["pose"])))
    return volume
