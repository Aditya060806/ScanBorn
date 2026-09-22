"""Deterministic scenes with known answers, in both capture formats (execution plan E2).

One room is described once (`ROOM`) and rendered two ways: as phone-format depth frames in the
home frame (metres, Z up, the phone at the origin, so the floor sits at a nonzero height) and
as a mesh export in another convention (Y up, centimetres, the way many mesh tools write).
Whatever path a scan takes, the pipeline must recover the same map coordinates.

Every generator is pure and seeded; nothing reads the clock or a global random state.

- known_axis_frames: two top-down frames; a red box on +X, a green box on +Y, different
  heights, so a mirrored or swapped axis or a dropped floor offset is detected.
- sparse_frames: the same room with 3 % of depth pixels kept.
- missing_color: frames without a colour array (grey is the documented default).
- room_mesh / write_mesh_ply / write_mesh_obj: the room as triangles in Y-up centimetres,
  with one face whose index is out of range; the valid faces must survive.
- large_cloud: more than 500k points, so both preview levels are exercised.
"""
from __future__ import annotations

import io
import struct
from typing import Any

import numpy as np

# The room in map coordinates (metres, right-handed, Z up; origin at the phone at Start).
FLOOR_Z = -1.35
ROOM: dict[str, Any] = {
    "floor_z": FLOOR_Z,
    "floor": {"x": (-0.6, 2.6), "y": (-0.6, 3.1)},
    "boxes": [
        # name, footprint centre (x, y), footprint size (dx, dy), height above floor, RGB
        {"name": "east", "centre": (1.6, 0.4), "size": (0.6, 0.4), "height": 0.50,
         "color": (220, 40, 40)},
        {"name": "north", "centre": (0.3, 2.2), "size": (0.4, 0.6), "height": 0.80,
         "color": (40, 200, 60)},
    ],
}
FLOOR_COLOR = (150, 150, 150)
GREY = (200, 200, 200)

# Camera: 160x160 pixels, focal 80 px, looking straight down from 2.6 m above the floor.
SIZE, FOCAL, CAMERA_HEIGHT = 160, 80.0, 2.6
K = np.array([[FOCAL, 0.0, SIZE / 2], [0.0, FOCAL, SIZE / 2], [0.0, 0.0, 1.0]])
# Camera x -> map +X, camera y (image down) -> map -Y, camera z (forward) -> map -Z.
DOWN = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
CAMERA_CENTRES = ((0.6, 0.9), (0.9, 1.3))


def _pose(centre) -> np.ndarray:
    pose = np.eye(4)
    pose[:3, :3] = DOWN
    pose[:3, 3] = (centre[0], centre[1], FLOOR_Z + CAMERA_HEIGHT)
    return pose


def _box_at(x: np.ndarray, y: np.ndarray, box: dict) -> np.ndarray:
    (cx, cy), (dx, dy) = box["centre"], box["size"]
    return (np.abs(x - cx) <= dx / 2) & (np.abs(y - cy) <= dy / 2)


def _frame(centre, color: bool = True, keep: float = 1.0, seed: int = 0) -> dict:
    """One top-down frame: each pixel's ray meets the highest surface it reaches."""
    u, v = np.meshgrid(np.arange(SIZE, dtype=np.float64), np.arange(SIZE, dtype=np.float64))
    depth = np.full((SIZE, SIZE), CAMERA_HEIGHT)
    rgb = np.empty((SIZE, SIZE, 3), dtype=np.uint8)
    rgb[:] = FLOOR_COLOR
    # Tallest first: a ray that meets a box top never reaches a lower surface behind it.
    for box in sorted(ROOM["boxes"], key=lambda b: -b["height"]):
        d = CAMERA_HEIGHT - box["height"]
        x = centre[0] + (u - K[0, 2]) * d / FOCAL
        y = centre[1] - (v - K[1, 2]) * d / FOCAL
        hit = _box_at(x, y, box) & (depth == CAMERA_HEIGHT)
        depth[hit] = d
        rgb[hit] = box["color"]
    x = centre[0] + (u - K[0, 2]) * depth / FOCAL
    y = centre[1] - (v - K[1, 2]) * depth / FOCAL
    fx, fy = ROOM["floor"]["x"], ROOM["floor"]["y"]
    outside = (depth == CAMERA_HEIGHT) & ~((x >= fx[0]) & (x <= fx[1]) &
                                           (y >= fy[0]) & (y <= fy[1]))
    depth[outside] = 0.0  # no return beyond the room
    if keep < 1.0:
        depth[np.random.default_rng(seed).random(depth.shape) >= keep] = 0.0
    frame = {"depth": depth.astype(np.float32), "intrinsics": K.copy(), "pose": _pose(centre)}
    if color:
        frame["color"] = rgb
    return frame


def known_axis_frames(color: bool = True) -> list:
    return [_frame(centre, color=color) for centre in CAMERA_CENTRES]


def sparse_frames() -> list:
    return [_frame(centre, keep=0.03, seed=index) for index, centre in enumerate(CAMERA_CENTRES)]


def missing_color() -> list:
    return known_axis_frames(color=False)


def frame_bytes(frame: dict) -> bytes:
    buffer = io.BytesIO()
    np.savez(buffer, **frame)
    return buffer.getvalue()


# --------------------------------------------------------------------------- mesh export

def _quad(vertices: list, faces: list, corners, color) -> None:
    base = len(vertices)
    vertices.extend((*corner, *color) for corner in corners)
    faces.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])


def room_mesh() -> tuple:
    """(vertices (N,6) map metres + RGB, triangles (M,3)) for the floor and the box tops."""
    vertices: list = []
    faces: list = []
    (x0, x1), (y0, y1) = ROOM["floor"]["x"], ROOM["floor"]["y"]
    _quad(vertices, faces, [(x0, y0, FLOOR_Z), (x1, y0, FLOOR_Z), (x1, y1, FLOOR_Z),
                            (x0, y1, FLOOR_Z)], FLOOR_COLOR)
    for box in ROOM["boxes"]:
        (cx, cy), (dx, dy) = box["centre"], box["size"]
        z = FLOOR_Z + box["height"]
        _quad(vertices, faces, [(cx - dx / 2, cy - dy / 2, z), (cx + dx / 2, cy - dy / 2, z),
                                (cx + dx / 2, cy + dy / 2, z), (cx - dx / 2, cy + dy / 2, z)],
              box["color"])
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def map_to_y_up_cm(points: np.ndarray) -> np.ndarray:
    """Map (X, Y, Z) metres -> a Y-up export in centimetres: file (x, y, z) = (X, Z, -Y)."""
    points = np.asarray(points, dtype=np.float64)
    return np.stack([points[:, 0], points[:, 2], -points[:, 1]], axis=1) * 100.0


BAD_FACE = (0, 1, 999)  # one index past the end: the viewer drops it and keeps the rest


def write_mesh_ply(path: str, binary: bool = True) -> str:
    vertices, faces = room_mesh()
    xyz = map_to_y_up_cm(vertices[:, :3])
    rgb = vertices[:, 3:6].astype(np.uint8)
    rows = [tuple(face) for face in faces] + [BAD_FACE]
    header = ("ply\nformat {} 1.0\ncomment scene fixture: Y up, centimetres\n"
              f"element vertex {len(xyz)}\nproperty float x\nproperty float y\n"
              "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\n"
              f"element face {len(rows)}\nproperty list uchar int vertex_indices\nend_header\n")
    with open(path, "wb") as f:
        if binary:
            f.write(header.format("binary_little_endian").encode())
            for p, c in zip(xyz, rgb):
                f.write(struct.pack("<3f3B", *p, *c))
            for row in rows:
                f.write(struct.pack("<B3i", 3, *row))
        else:
            f.write(header.format("ascii").encode())
            for p, c in zip(xyz, rgb):
                f.write(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {c[0]} {c[1]} {c[2]}\n".encode())
            for row in rows:
                f.write(f"3 {row[0]} {row[1]} {row[2]}\n".encode())
    return path


def write_mesh_obj(path: str) -> str:
    """The same mesh as OBJ (1-based faces), vertex colours as 0..1 floats."""
    vertices, faces = room_mesh()
    xyz = map_to_y_up_cm(vertices[:, :3])
    with open(path, "w", encoding="ascii") as f:
        f.write("# scene fixture: Y up, centimetres\n")
        for p, c in zip(xyz, vertices[:, 3:6]):
            f.write(f"v {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} "
                    f"{c[0] / 255:.6f} {c[1] / 255:.6f} {c[2] / 255:.6f}\n")
        for face in [tuple(face) for face in faces] + [BAD_FACE]:
            f.write("f " + " ".join(str(i + 1) for i in face) + "\n")
    return path


# --------------------------------------------------------------------------- large cloud

def large_cloud(count: int = 600_000, seed: int = 11) -> tuple:
    """(points, colors): a floor and two box tops, sampled uniformly; more than 500k points."""
    rng = np.random.default_rng(seed)
    (x0, x1), (y0, y1) = ROOM["floor"]["x"], ROOM["floor"]["y"]
    points = np.column_stack([rng.uniform(x0, x1, count), rng.uniform(y0, y1, count),
                              np.full(count, FLOOR_Z)])
    colors = np.tile(np.asarray(FLOOR_COLOR, dtype=np.uint8), (count, 1))
    for box in ROOM["boxes"]:
        inside = _box_at(points[:, 0], points[:, 1], box)
        points[inside, 2] = FLOOR_Z + box["height"]
        colors[inside] = box["color"]
    return points, colors


def write_binary_cloud(path: str, points: np.ndarray, colors: np.ndarray | None) -> str:
    """A binary PLY point cloud; colors=None writes no colour properties at all."""
    record = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(points)}",
              "property float x", "property float y", "property float z"]
    if colors is not None:
        record += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
        header += ["property uchar red", "property uchar green", "property uchar blue"]
    columns = [points[:, axis] for axis in range(3)]
    if colors is not None:
        columns += [colors[:, channel] for channel in range(3)]
    table = np.rec.fromarrays(columns, dtype=record)
    with open(path, "wb") as f:
        f.write(("\n".join(header + ["end_header"]) + "\n").encode())
        f.write(table.tobytes())
    return path
