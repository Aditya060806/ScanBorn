"""Occupancy grid over the floor: where a ground robot can and cannot be.

Every cell is in one of three states:

- FREE (0): the scan saw floor there, and nothing in the robot's height band.
- BLOCKED (1): at least MIN_POINTS points in the band the robot sweeps, or at least
  MIN_POINTS points more than DROP below the floor (a step down, a stairwell).
- UNKNOWN (2): the scan saw neither. A ground robot must treat it as blocked. The state
  exists so the operator can see *why* a cell is off-limits, and so a rescan can tell
  "changed" apart from "never looked".

Anything that asks "can the robot be here?" tests for non-zero, so navmeshes written before
the unknown state existed (0/1 only) are still read correctly.

The thresholds come from the sampling rather than guesswork: reconstruction fuses at a 2 cm
voxel, so a real surface puts ~25 points in a 10 cm cell, and three points is a noise floor,
not a density requirement.

Grid convention: row index grows with +y and column index with +x; row 0 is the minimum y.
"""
import base64
import math

import numpy as np
from scipy import ndimage

from reconstruction.floor import estimate_floor_z
from twin.profile import profile_for

FREE, BLOCKED, UNKNOWN = 0, 1, 2
VERSION = 2

# Structure, not furniture: these never block the ground plane the robot drives on.
WALKABLE_LABELS = {"floor", "ceiling"}

# Height band a ground robot actually sweeps. Points below FLOOR_CLEARANCE are the floor
# itself (and scan noise on it); points above the robot's height pass over its head, so a
# ceiling or a high shelf must not block the ground plane. 5 cm is 2.5–5x fused depth noise, and
# three points still filter an isolated spike. The rover climbs only ~2 cm, so objects 2–5 cm tall
# (cables, thresholds) remain invisible to the map: a known gap, on the demo checklist.
FLOOR_CLEARANCE = 0.05
ROBOT_HEIGHT = 1.0

MIN_POINTS = 3   # points in a cell before it counts as observed (see module docstring)
# m below the floor that counts as a drop. A stair riser is 15-18 cm; fused ARCore depth is
# within 1-2 cm, so 10 cm separates the two with room to spare.
DROP = 0.10

# Grid sizes a twin can use. 5 cm resolves a doorway or a chair leg twice as finely and still
# plans in milliseconds (benchmarks/planning.json), but a 5 cm cell holds a quarter of a 10 cm
# cell's points, so it is only used where the scan is dense enough (choose_cell).
CELL_FINE = 0.05
CELL_COARSE = 0.10
GRID_CELLS = (CELL_FINE, CELL_COARSE)
# Floor points per 10 cm cell, at the 25th percentile of the floor that was seen, before the
# fine grid is used: four 5 cm cells' worth of MIN_POINTS, so three quarters of the floor
# still counts as seen at 5 cm.
FINE_FLOOR_POINTS = 4 * MIN_POINTS

_EIGHT = np.ones((3, 3), dtype=bool)


def grid_frame(objects: list, cell: float, points=None):
    """(origin, width, height) of the grid, or None when there is nothing to map.

    The floor's footprint when segmentation found one, else every object's, else the cloud's
    own xy bounds.
    """
    floors = [o for o in objects if o["label"] in WALKABLE_LABELS]
    extent = floors or objects
    if extent:
        xmin = min(o["bbox3d"][0] for o in extent)
        ymin = min(o["bbox3d"][1] for o in extent)
        xmax = max(o["bbox3d"][3] for o in extent)
        ymax = max(o["bbox3d"][4] for o in extent)
    elif points is not None and len(points) > 0:
        xy = np.asarray(points, dtype=np.float64)[:, :2]
        xy = xy[np.isfinite(xy).all(axis=1)]
        if not len(xy):
            return None
        (xmin, ymin), (xmax, ymax) = xy.min(axis=0), xy.max(axis=0)
    else:
        return None
    width = max(1, round((xmax - xmin) / cell))
    height = max(1, round((ymax - ymin) / cell))
    return (float(xmin), float(ymin)), width, height


def fill_gaps(grid: np.ndarray) -> np.ndarray:
    """Mark unknown cells free where they close a gap one cell wide in the free floor.

    A morphological close of the free mask, applied to unknown cells only: it never
    overwrites a blocked cell, and it does not creep into the unseen shadow beside a piece of
    furniture, because a blocked neighbour keeps the close from reaching across.
    """
    closed = ndimage.binary_closing(grid == FREE, structure=_EIGHT)
    out = grid.copy()
    out[closed & (grid == UNKNOWN)] = FREE
    return out


def _band_counts(points, origin, cell: float, width: int, height: int, floor_z: float,
                 sweep_height: float) -> tuple:
    """Per-cell point counts: (floor band, the robot's sweep band, below the floor)."""
    raw = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    pts = raw[np.isfinite(raw).all(axis=1)]
    col = np.floor((pts[:, 0] - origin[0]) / cell).astype(np.int64)
    row = np.floor((pts[:, 1] - origin[1]) / cell).astype(np.int64)
    inside = (col >= 0) & (col < width) & (row >= 0) & (row < height)
    index = row[inside] * width + col[inside]
    dz = pts[inside, 2] - floor_z

    def count(mask):
        return np.bincount(index[mask], minlength=width * height).reshape(height, width)

    return (count((dz >= -DROP) & (dz < FLOOR_CLEARANCE)),
            count((dz >= FLOOR_CLEARANCE) & (dz <= sweep_height)),
            count(dz < -DROP))


def rasterise(points, origin, cell: float, width: int, height: int, floor_z: float,
              sweep_height: float, min_points: int = MIN_POINTS) -> np.ndarray:
    """Point cloud -> (height, width) uint8 grid of FREE / BLOCKED / UNKNOWN.

    Vectorised: three bincounts over the cells, whatever the cloud's size. Shared by twin
    generation and rescan comparison, so both read a cloud the same way.
    """
    grid = np.full((height, width), UNKNOWN, dtype=np.uint8)
    if width == 0 or height == 0:
        return grid
    floor, sweep, drop = _band_counts(points, origin, cell, width, height, floor_z,
                                      sweep_height)
    grid[floor >= min_points] = FREE
    grid[(sweep >= min_points) | (drop >= min_points)] = BLOCKED
    return fill_gaps(grid)


def choose_cell(objects: list, points=None) -> tuple:
    """(cell size, evidence): 5 cm where the scan is dense enough for it, 10 cm otherwise.

    The test is the floor's own sampling: over the 10 cm cells where any floor was seen, the
    25th percentile of floor points must reach FINE_FLOOR_POINTS. A phone scan fused at the
    2 cm voxel puts about 25 points in such a cell; a cloud sampled every 4 cm puts about 6,
    and on a 5 cm grid most of its floor would read as never seen.
    """
    evidence: dict = {"floor_points_per_10cm_cell_p25": None, "threshold": FINE_FLOOR_POINTS}
    frame = grid_frame(objects, CELL_COARSE, points) if points is not None and len(points) else None
    try:
        floor_z = estimate_floor_z(points) if frame is not None else None
    except ValueError:
        floor_z = None
    if frame is None or floor_z is None:
        return CELL_COARSE, {**evidence, "reason": "no point cloud to measure"}

    origin, width, height = frame
    floor, _, _ = _band_counts(points, origin, CELL_COARSE, width, height, floor_z, ROBOT_HEIGHT)
    seen = floor[floor > 0]
    if not seen.size:
        return CELL_COARSE, {**evidence, "reason": "no floor seen"}
    p25 = float(np.percentile(seen, 25))
    fine = p25 >= FINE_FLOOR_POINTS
    return (CELL_FINE if fine else CELL_COARSE), {
        **evidence, "floor_points_per_10cm_cell_p25": round(p25, 1),
        "reason": "dense enough for 5 cm cells" if fine else "too sparse for 5 cm cells"}


def _from_boxes(objects: list, origin, cell: float, width: int, height: int) -> np.ndarray:
    """Two-state grid from bounding boxes, for when there is no cloud.

    Nothing was observed, so nothing can be unknown: open floor is assumed free. Any cell a
    footprint touches is blocked — rounding outwards, since a robot clipping the corner of a
    table is a real collision.
    """
    grid = np.zeros((height, width), dtype=np.uint8)
    for obj in objects:
        if obj["label"] in WALKABLE_LABELS:
            continue
        ox0, oy0, _, ox1, oy1, _ = obj["bbox3d"]
        r0 = max(0, int((oy0 - origin[1]) / cell))
        r1 = min(height, int((oy1 - origin[1]) / cell) + 1)
        c0 = max(0, int((ox0 - origin[0]) / cell))
        c1 = min(width, int((ox1 - origin[0]) / cell) + 1)
        if r0 < r1 and c0 < c1:
            grid[r0:r1, c0:c1] = BLOCKED
    return grid


def counts(grid: np.ndarray) -> dict:
    return {"free": int((grid == FREE).sum()), "blocked": int((grid == BLOCKED).sum()),
            "unknown": int((grid == UNKNOWN).sum())}


def build_navmesh(objects: list, cell: float, points=None, profile=None) -> dict:
    """Occupancy grid for one robot profile, as the dict written to navmesh.json.

    Occupancy comes from the point cloud when it is available, and only from bounding boxes
    when it is not. A bounding box is a solid block, so anything concave — a room shell, a
    U-shaped desk — fills its whole interior when rasterised that way; points carry the
    concavity a box throws away.

    `profile` decides how tall a band counts as an obstacle, because that is a fact about the
    robot rather than the room. It is written into the dict so the planner inflates by the
    same robot's radius without being told separately.
    """
    resolved = profile if profile is not None else profile_for(None)
    base = {"version": VERSION, "cell": cell, "profile": resolved.as_dict()}
    frame = grid_frame(objects, cell, points)
    if frame is None:
        empty = np.zeros((0, 0), dtype=np.uint8)
        return {**base, "origin": [0, 0], "width": 0, "height": 0, "grid": [],
                "floor_z": None, "counts": counts(empty)}

    origin, width, height = frame
    floor_z = None
    if points is not None and len(points) > 0:
        try:
            floor_z = estimate_floor_z(points)
        except ValueError:
            floor_z = None  # no finite point: fall back to the boxes
    if floor_z is not None:
        grid = rasterise(points, origin, cell, width, height, floor_z, resolved.robot_height)
    else:
        grid = _from_boxes(objects, origin, cell, width, height)

    return {**base, "origin": [origin[0], origin[1]], "width": width, "height": height,
            "grid": grid.tolist(), "floor_z": floor_z, "counts": counts(grid)}


def as_array(navmesh: dict) -> np.ndarray:
    """The navmesh's grid as a (height, width) uint8 array."""
    shape = (navmesh["height"], navmesh["width"])
    return np.asarray(navmesh["grid"], dtype=np.uint8).reshape(shape)


def is_blocked(navmesh: dict, x: float, y: float) -> bool:
    """True if (x, y) is untraversable — blocked, unknown, or outside the mapped floor."""
    col = math.floor((x - navmesh["origin"][0]) / navmesh["cell"])
    row = math.floor((y - navmesh["origin"][1]) / navmesh["cell"])
    if not (0 <= col < navmesh["width"] and 0 <= row < navmesh["height"]):
        return True
    return bool(navmesh["grid"][row][col])


def nearest_free(navmesh: dict, x: float, y: float):
    """Centre of the free cell closest to (x, y), or None if no cell is free.

    Navigating "to the table" cannot mean the table's centre — that cell is occupied by the
    table. It means the nearest spot a robot can actually stand. Ties go to the first cell in
    row-major order.
    """
    rows, cols = np.nonzero(as_array(navmesh) == FREE)
    if rows.size == 0:
        return None
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    cx, cy = x0 + (cols + 0.5) * cell, y0 + (rows + 0.5) * cell
    i = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))
    return float(cx[i]), float(cy[i])


def encode_cells(grid) -> str:
    """Grid -> base64 of its uint8 cells, row-major, row 0 at minimum y."""
    return base64.b64encode(np.ascontiguousarray(grid, dtype=np.uint8).tobytes()).decode("ascii")


def decode_cells(text: str, width: int, height: int) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.uint8).reshape(height, width)
