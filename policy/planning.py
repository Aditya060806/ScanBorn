"""Global path planning on the navmesh: clearance, A*, shortcutting, missions.

Navigation is split the way Nav2 splits it (section 11.3): this module finds the route, and
the learned policy in policy/evaluate.py is the local controller that tracks it.

Clearance is one distance field — metres from each cell centre to the nearest cell that is
not free (blocked, unknown, or off the map) — computed with scipy's exact Euclidean distance
transform. Everything else reads it:

- traversable = clearance > robot radius, which is Nav2's costmap inflation;
- an A* step costs more as clearance falls below radius + PREFERRED_MARGIN, so a path keeps
  its distance where the room allows and squeezes through only where it must;
- shortcutting may straighten a path but never brings it closer to an obstacle than A* did.

Distances are centre to centre, like the inflation loop this replaces, so the true gap to an
obstacle's edge is up to half a cell less than the number reported.
"""
import heapq
import math
import time
from typing import cast

import numpy as np
from scipy import ndimage

from twin.navmesh import BLOCKED, UNKNOWN, as_array

ROBOT_RADIUS = 0.25      # m: for navmeshes written before profiles carried a radius
PREFERRED_MARGIN = 0.30  # m beyond the robot's radius where more clearance stops paying
CLEARANCE_WEIGHT = 4.0   # at the inflation edge a step costs 1 + 4 = 5 times its length
EPS = 1e-9
SQRT2 = math.sqrt(2.0)
_STEPS = ((-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
          (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2))


class MissionError(ValueError):
    """A mission that cannot be planned: a code a client can branch on, and a way out."""

    def __init__(self, code: str, message: str, nearest=None, index: int | None = None):
        super().__init__(message)
        self.code = code
        self.nearest = nearest
        self.index = index

    def as_detail(self) -> dict:
        detail: dict = {"error": str(self), "code": self.code,
                        "nearest": _rounded(self.nearest) if self.nearest else None}
        if self.index is not None:
            detail["index"] = self.index
        return detail


# ------------------------------------------------------------------------------ grid

def robot_radius(navmesh: dict) -> float:
    return float(navmesh.get("profile", {}).get("robot_radius", ROBOT_RADIUS))


def cell_of(navmesh: dict, x: float, y: float) -> tuple:
    """(row, col) holding (x, y). May be outside the grid."""
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    return math.floor((y - y0) / cell), math.floor((x - x0) / cell)


def centre_of(navmesh: dict, row: int, col: int) -> tuple:
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    return x0 + (col + 0.5) * cell, y0 + (row + 0.5) * cell


def clearance_field(navmesh: dict) -> np.ndarray:
    """Metres from every cell centre to the nearest non-free cell centre; 0 on non-free cells.

    The map is padded with one non-free ring first: beyond the scanned floor is not somewhere
    the robot can put its wheels, so the edge of the map repels a path like a wall does.
    """
    grid = as_array(navmesh)
    if grid.size == 0:
        return np.zeros(grid.shape)
    free = np.pad(grid == 0, 1, constant_values=False)
    return ndimage.distance_transform_edt(free)[1:-1, 1:-1] * navmesh["cell"]


def traversable(field: np.ndarray, radius: float) -> np.ndarray:
    """Cells where a robot of `radius` fits: strictly more than its radius from anything."""
    return field > radius + EPS


def inflate(navmesh: dict, radius: float | None = None) -> dict:
    """Grow every obstacle by the robot's radius — Nav2's costmap inflation.

    Planning on the raw grid produces paths that graze obstacles, and a controller aiming at a
    lookahead point then cuts the corner straight through them. Inflating first is what buys
    the clearance that makes corner-cutting safe.

    The radius comes from the navmesh's own profile when it is not passed, so a twin generated
    for a forklift plans like a forklift. Unknown cells stay 2, so the result still says why a
    cell is off-limits.
    """
    radius = robot_radius(navmesh) if radius is None else radius
    grid = as_array(navmesh)
    inflated = np.where(traversable(clearance_field(navmesh), radius), 0, BLOCKED)
    inflated = inflated.astype(np.uint8)
    inflated[grid == UNKNOWN] = UNKNOWN
    return {**navmesh, "grid": inflated.tolist()}


# ----------------------------------------------------------------------------- search

def _octile(drow: int, dcol: int) -> float:
    a, b = abs(drow), abs(dcol)
    return max(a, b) + (SQRT2 - 1.0) * min(a, b)


def _cell_cost(field: np.ndarray, radius: float) -> np.ndarray:
    shortfall = np.clip((radius + PREFERRED_MARGIN - field) / PREFERRED_MARGIN, 0.0, 1.0)
    return 1.0 + CLEARANCE_WEIGHT * shortfall


def astar(navmesh: dict, start, goal, field: np.ndarray | None = None,
          radius: float | None = None) -> list:
    """Cheapest cell route from start to goal as [(row, col), ...]; [] if there is none.

    Diagonals cost √2 and may not cut past a corner — the robot has width. A step costs its
    length times the mean clearance cost of the two cells, so the octile heuristic, which
    assumes cost 1 per unit length, never overestimates and the route is optimal.
    """
    radius = robot_radius(navmesh) if radius is None else radius
    field = clearance_field(navmesh) if field is None else field
    height, width = field.shape
    ok = traversable(field, radius)
    (sr, sc), (gr, gc) = cell_of(navmesh, start[0], start[1]), cell_of(navmesh, goal[0], goal[1])
    if not (_inside(ok, sr, sc) and _inside(ok, gr, gc)):
        return []

    okf = ok.ravel().tolist()
    cost = _cell_cost(field, radius).ravel().tolist()
    start_i, goal_i = sr * width + sc, gr * width + gc
    best = {start_i: 0.0}
    came = {start_i: -1}
    closed: set = set()
    heap = [(_octile(sr - gr, sc - gc), 0.0, start_i)]
    while heap:
        _, g, i = heapq.heappop(heap)
        if i in closed:
            continue
        if i == goal_i:
            break
        closed.add(i)
        r, c = divmod(i, width)
        for dr, dc, step in _STEPS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < height and 0 <= nc < width):
                continue
            j = nr * width + nc
            if not okf[j] or j in closed:
                continue
            if dr and dc and not (okf[r * width + nc] and okf[nr * width + c]):
                continue
            ng = g + step * 0.5 * (cost[i] + cost[j])
            if ng < best.get(j, math.inf):
                best[j] = ng
                came[j] = i
                heapq.heappush(heap, (ng + _octile(nr - gr, nc - gc), ng, j))

    if goal_i not in came:
        return []
    cells = []
    node = goal_i
    while node != -1:
        cells.append(divmod(node, width))
        node = came[node]
    cells.reverse()
    return cells


def _inside(ok: np.ndarray, row: int, col: int) -> bool:
    return 0 <= row < ok.shape[0] and 0 <= col < ok.shape[1] and bool(ok[row, col])


def supercover(r0: int, c0: int, r1: int, c1: int) -> list:
    """Every cell a straight line between two cell centres touches, corners included.

    Integer arithmetic, so a line passing exactly through a grid corner adds both cells beside
    that corner instead of slipping between them.
    """
    nr, nc = abs(r1 - r0), abs(c1 - c0)
    sr, sc = (1 if r1 > r0 else -1), (1 if c1 > c0 else -1)
    r, c, ir, ic = r0, c0, 0, 0
    cells = [(r, c)]
    while ir < nr or ic < nc:
        decision = (1 + 2 * ir) * nc - (1 + 2 * ic) * nr
        if decision == 0:
            cells.append((r + sr, c))
            cells.append((r, c + sc))
            r, c, ir, ic = r + sr, c + sc, ir + 1, ic + 1
        elif decision < 0:
            r, ir = r + sr, ir + 1
        else:
            c, ic = c + sc, ic + 1
        cells.append((r, c))
    return cells


def shortcut(cells: list, field: np.ndarray, radius: float) -> list:
    """Drop every cell a straight line can skip, without giving up any clearance.

    A line from cell i to cell j replaces the route between them only if every cell it touches
    is traversable and at least as far from obstacles as the route's own closest approach over
    that stretch. The result's minimum clearance therefore equals the A* route's, and the
    straightening happens only where it costs nothing.
    """
    if len(cells) <= 2:
        return list(cells)
    ok = cast("list[list[bool]]", traversable(field, radius).tolist())
    clear = cast("list[list[float]]", field.tolist())
    # Closest approach of each original step, corners included.
    step_floor = [min(clear[r][c] for r, c in supercover(*a, *b))
                  for a, b in zip(cells, cells[1:])]

    out, i, n = [cells[0]], 0, len(cells)
    while i < n - 1:
        j, floor = i + 1, step_floor[i]
        while j + 1 < n:
            candidate = min(floor, step_floor[j])
            line = supercover(*cells[i], *cells[j + 1])
            if not all(ok[r][c] and clear[r][c] >= candidate - EPS for r, c in line):
                break
            j, floor = j + 1, candidate
        out.append(cells[j])
        i = j
    return out


# ---------------------------------------------------------------------------- geometry

def densify(points, step: float) -> list:
    """The polyline resampled at no more than `step` apart, keeping every vertex."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2:
        return pts
    out: list[tuple[float, float]] = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        n = max(1, math.ceil(math.hypot(bx - ax, by - ay) / step - EPS))
        out.extend((ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(n))
    out.append(pts[-1])
    return out


def path_clearance(navmesh: dict, field: np.ndarray, polyline) -> float:
    """Closest approach of a polyline to anything not free, sampled every quarter cell."""
    if not len(polyline):
        return 0.0
    height, width = field.shape
    closest = math.inf
    for x, y in densify(polyline, navmesh["cell"] / 4):
        r, c = cell_of(navmesh, x, y)
        closest = min(closest, float(field[r, c]) if 0 <= r < height and 0 <= c < width else 0.0)
    return closest


def _segment_ok(navmesh: dict, ok: np.ndarray, a, b) -> bool:
    samples = densify([a, b], navmesh["cell"] / 4)
    return all(_inside(ok, *cell_of(navmesh, x, y)) for x, y in samples)


def nearest_valid(navmesh: dict, allowed: np.ndarray, x: float, y: float):
    """Centre of the allowed cell closest to (x, y), or None if none is allowed."""
    rows, cols = np.nonzero(allowed)
    if rows.size == 0:
        return None
    cell, (x0, y0) = navmesh["cell"], navmesh["origin"]
    cx, cy = x0 + (cols + 0.5) * cell, y0 + (rows + 0.5) * cell
    i = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))
    return float(cx[i]), float(cy[i])


def _why(navmesh: dict, radius: float, x: float, y: float) -> str:
    row, col = cell_of(navmesh, x, y)
    if not (0 <= row < navmesh["height"] and 0 <= col < navmesh["width"]):
        return "outside the scanned floor"
    state = navmesh["grid"][row][col]
    if state == BLOCKED:
        return "on an obstacle"
    if state == UNKNOWN:
        return "on floor the scan never saw"
    return f"within {radius:.2f} m of an obstacle or unscanned floor, closer than the robot fits"


def _rounded(point) -> list:
    return [round(float(v), 4) for v in point]


# ---------------------------------------------------------------------------- missions

def plan_mission(navmesh: dict, start, goal, via=(), field: np.ndarray | None = None) -> dict:
    """Route start -> via points (in order) -> goal, or raise MissionError.

    `start` is (x, y) or (x, y, heading); the heading defaults to the direction of the first
    segment. Returns sparse `waypoints` (corners, for display and for the rover), a dense
    `path` (one point per cell length, for the point-following controller), the length, and
    the closest approach to anything not free.

    Errors carry a code — start_blocked, via_blocked, goal_blocked or unreachable — plus the
    nearest point that would work: for the start, anywhere the robot fits; for anything else,
    somewhere reachable from the start.
    """
    began = time.perf_counter()
    if not navmesh.get("width") or not navmesh.get("height"):
        raise MissionError("unreachable", "The navmesh is empty: there is no floor to plan on.")
    radius = robot_radius(navmesh)
    field = clearance_field(navmesh) if field is None else field
    ok = traversable(field, radius)
    # 4-connected on purpose: with no corner cutting, that is exactly what A* can reach.
    labels, _ = ndimage.label(ok)

    stops = [("start", start), *(("via", v) for v in via), ("goal", goal)]
    cells: list = []
    for index, (role, point) in enumerate(stops):
        x, y = float(point[0]), float(point[1])
        row, col = cell_of(navmesh, x, y)
        via_index = index - 1 if role == "via" else None
        reachable = (labels == labels[cells[0]]) if cells else ok
        if not _inside(ok, row, col):
            raise MissionError(f"{role}_blocked",
                               f"The {role} is {_why(navmesh, radius, x, y)}.",
                               nearest_valid(navmesh, reachable, x, y), via_index)
        if cells and labels[row, col] != labels[cells[0]]:
            raise MissionError("unreachable",
                               f"No route reaches the {role}: obstacles or unscanned floor "
                               f"cut it off from the start.",
                               nearest_valid(navmesh, reachable, x, y), via_index)
        cells.append((row, col))

    vertices, stop_at = [cells[0]], [0]
    for a, b in zip(cells, cells[1:]):
        leg = astar(navmesh, centre_of(navmesh, *a), centre_of(navmesh, *b), field, radius)
        if not leg:  # cannot happen when the labels agree; kept so a bug fails loudly
            raise MissionError("unreachable", "No route found between two stops.")
        vertices.extend(shortcut(leg, field, radius)[1:])
        stop_at.append(len(vertices) - 1)

    points = [(float(p[0]), float(p[1])) for _, p in stops]
    waypoints = _pin_stops(navmesh, ok, vertices, stop_at, points)
    heading = (float(start[2]) if len(start) > 2 and start[2] is not None
               else _first_heading(waypoints))
    path = densify(waypoints, navmesh["cell"])
    return {
        "start": [*_rounded(points[0]), round(heading, 4)],
        "goal": _rounded(points[-1]),
        "via": [_rounded(p) for p in points[1:-1]],
        "waypoints": [_rounded(p) for p in waypoints],
        "path": [_rounded(p) for p in path],
        "length_m": round(sum(math.dist(a, b) for a, b in zip(waypoints, waypoints[1:])), 3),
        "min_clearance_m": round(path_clearance(navmesh, field, waypoints), 3),
        "plan_ms": round((time.perf_counter() - began) * 1000, 1),
    }


def _pin_stops(navmesh: dict, ok: np.ndarray, vertices: list, stop_at: list,
               points: list) -> list:
    """Cell-centre vertices -> metres, with each stop at the exact point that was asked for.

    A stop replaces its cell centre when the straight segments to its neighbours stay
    traversable. When they do not, the start and goal keep the centre beside them (a segment
    inside one traversable cell is always clear), and a via point stays at its centre.
    """
    if len(vertices) == 1:
        return _dedupe([points[0], points[-1]])
    xy = [centre_of(navmesh, r, c) for r, c in vertices]
    exact: dict = {}
    for k, at in enumerate(stop_at):
        exact.setdefault(at, points[k])
    exact[stop_at[-1]] = points[-1]

    last, out = len(xy) - 1, []
    for i, centre in enumerate(xy):
        if i not in exact:
            out.append(centre)
            continue
        point = exact[i]
        fits = ((i == 0 or _segment_ok(navmesh, ok, point, xy[i - 1]))
                and (i == last or _segment_ok(navmesh, ok, point, xy[i + 1])))
        if fits:
            out.append(point)
        elif i == 0:
            out.extend([point, centre])
        elif i == last:
            out.extend([centre, point])
        else:
            out.append(centre)
    return _dedupe(out)


def _dedupe(points: list) -> list:
    out: list = []
    for p in points:
        if not out or math.dist(out[-1], p) > EPS:
            out.append(p)
    return out


def _first_heading(waypoints: list) -> float:
    for a, b in zip(waypoints, waypoints[1:]):
        if math.dist(a, b) > EPS:
            return math.atan2(b[1] - a[1], b[0] - a[0])
    return 0.0


def plan_path(navmesh: dict, start, goal) -> list:
    """Route on an already-inflated navmesh, a point every cell length; [] if there is none.

    For the rollout and policy-runner callers, which inflate first and then plan. The mesh is
    treated as inflated (radius 0), so A* still keeps its distance from the inflated edge
    without inflating a second time. The route ends exactly at the goal.
    """
    field = clearance_field(navmesh)
    cells = astar(navmesh, start, goal, field, radius=0.0)
    if not cells:
        return []
    if len(cells) == 1:
        return [(float(goal[0]), float(goal[1]))]
    path = densify([centre_of(navmesh, r, c) for r, c in shortcut(cells, field, 0.0)],
                   navmesh["cell"])
    path[-1] = (float(goal[0]), float(goal[1]))
    return path
