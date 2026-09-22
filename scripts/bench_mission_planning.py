"""Time mission planning on a large synthetic furnished room, on this computer's CPU.

Writes benchmarks/planning.json. No real scan exists yet, so the room is synthetic, built the
way a fused phone scan looks: floor sampled at the 2 cm reconstruction voxel with 5 mm of
noise, walls, an inner wall with a doorway, furniture with no floor visible beneath it, an
unscanned hole, and 1 % outliers. It measures whether a 5 cm navmesh plans within the budget
as a 10 cm one does. Compute is only half of that choice: a cell counts as seen with three
points, so a 5 cm cell needs a surface sampled about every 3 cm, and the samples per cell are
recorded alongside.

    python scripts/bench_mission_planning.py
"""
import datetime
import json
import math
import os
import platform
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "benchmarks", "planning.json")

ROOM = (12.0, 10.0)   # m
STEP = 0.02           # m, the reconstruction voxel
MISSIONS = 20
MIN_DISTANCE = 4.0    # m between start and goal
BUDGET_P95_MS = 250   # a cold plan (clearance field + A*) must fit this to feel immediate
PROFILE = "home"      # the demo profile: robot radius 0.20 m, sweep height 0.80 m

# (x0, x1, y0, y1, height) — tops and sides are scanned, the floor beneath is not.
FURNITURE = [
    (1.0, 3.0, 7.5, 8.5, 0.45),    # sofa
    (2.0, 3.2, 3.0, 4.0, 0.75),    # table
    (8.0, 10.0, 6.5, 9.5, 0.55),   # bed
    (9.0, 11.5, 1.0, 1.8, 0.75),   # desk
    (4.4, 5.6, 9.3, 9.9, 1.80),    # shelf
    (0.5, 1.1, 0.5, 1.1, 0.45),    # stool
    (7.0, 7.6, 3.0, 3.6, 1.10),    # cabinet
    (10.6, 11.6, 4.0, 5.2, 0.90),  # dresser
]
INNER_WALL_X, DOOR = 6.0, (4.0, 5.0)
HOLE = (6.8, 7.2, 1.8, 2.2)


def cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def surface(np, u0, u1, v0, v1, place):
    """Points on a rectangle sampled every STEP; `place(u, v)` maps them to (x, y, z)."""
    u, v = np.meshgrid(np.arange(u0 + STEP / 2, u1, STEP), np.arange(v0 + STEP / 2, v1, STEP))
    return np.column_stack(place(u.ravel(), v.ravel()))


def furnished_room(np, rng):
    width, depth = ROOM
    floor = surface(np, 0, width, 0, depth, lambda u, v: (u, v, rng.normal(0, 0.005, u.size)))
    hidden = np.zeros(len(floor), dtype=bool)
    for x0, x1, y0, y1, _ in FURNITURE:
        hidden |= (floor[:, 0] > x0) & (floor[:, 0] < x1) & (floor[:, 1] > y0) & (floor[:, 1] < y1)
    hx0, hx1, hy0, hy1 = HOLE
    hidden |= (floor[:, 0] > hx0) & (floor[:, 0] < hx1) & (floor[:, 1] > hy0) & (floor[:, 1] < hy1)
    parts = [floor[~hidden]]

    wall_h = 2.4
    for x in (0.0, width):
        parts.append(surface(np, 0, depth, 0, wall_h, lambda v, z, x=x: (np.full(v.size, x), v, z)))
    for y in (0.0, depth):
        parts.append(surface(np, 0, width, 0, wall_h, lambda u, z, y=y: (u, np.full(u.size, y), z)))
    for y0, y1 in ((0.0, DOOR[0]), (DOOR[1], depth)):
        parts.append(surface(np, y0, y1, 0, wall_h,
                             lambda v, z: (np.full(v.size, INNER_WALL_X), v, z)))

    for x0, x1, y0, y1, h in FURNITURE:
        parts.append(surface(np, x0, x1, y0, y1, lambda u, v, h=h: (u, v, np.full(u.size, h))))
        for x in (x0, x1):
            parts.append(surface(np, y0, y1, 0.02, h, lambda v, z, x=x: (np.full(v.size, x), v, z)))
        for y in (y0, y1):
            parts.append(surface(np, x0, x1, 0.02, h, lambda u, z, y=y: (u, np.full(u.size, y), z)))

    cloud = np.vstack(parts)
    n_out = int(0.01 * len(cloud))
    outliers = np.column_stack([rng.uniform(0, width, n_out), rng.uniform(0, depth, n_out),
                                rng.uniform(-1.0, 2.5, n_out)])
    return np.vstack([cloud, outliers]), n_out


def percentiles(values) -> dict:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return {"p50": round(statistics.median(ordered), 1), "p95": round(p95, 1),
            "max": round(ordered[-1], 1)}


def main() -> None:
    sys.path.insert(0, ROOT)
    import numpy as np
    import scipy
    from scipy import ndimage

    from policy.planning import (
        centre_of,
        clearance_field,
        plan_mission,
        robot_radius,
        traversable,
    )
    from reconstruction.floor import estimate_floor_z
    from twin.navmesh import build_navmesh, choose_cell
    from twin.profile import profile_for

    rng = np.random.default_rng(2026)
    points, n_outliers = furnished_room(np, rng)
    floor_obj = {"id": "floor", "label": "floor", "confidence": 0.9,
                 "bbox3d": [0.0, 0.0, 0.0, ROOM[0], ROOM[1], 0.0]}
    profile = profile_for(PROFILE)

    began = time.perf_counter()
    floor_z = estimate_floor_z(points)
    floor_ms = (time.perf_counter() - began) * 1000
    auto_cell, auto_evidence = choose_cell([floor_obj], points)

    resolutions = []
    for cell in (0.1, 0.05):
        began = time.perf_counter()
        navmesh = build_navmesh([floor_obj], cell, points=points, profile=profile)
        build_ms = (time.perf_counter() - began) * 1000

        text = json.dumps(navmesh)
        began = time.perf_counter()
        navmesh = json.loads(text)
        load_ms = (time.perf_counter() - began) * 1000

        began = time.perf_counter()
        field = clearance_field(navmesh)
        field_ms = (time.perf_counter() - began) * 1000

        ok = traversable(field, robot_radius(navmesh))
        labels, _ = ndimage.label(ok)
        largest = int(np.bincount(labels.ravel())[1:].argmax()) + 1
        rows, cols = np.nonzero(labels == largest)
        pairs: list = []
        while len(pairs) < MISSIONS:
            i, j = rng.integers(len(rows), size=2)
            start, goal = centre_of(navmesh, rows[i], cols[i]), centre_of(navmesh, rows[j], cols[j])
            if math.dist(start, goal) >= MIN_DISTANCE:
                pairs.append((start, goal))

        warm, cold, lengths = [], [], []
        for start, goal in pairs:
            began = time.perf_counter()
            mission = plan_mission(navmesh, start, goal, field=field)
            warm.append((time.perf_counter() - began) * 1000)
            lengths.append(mission["length_m"])
            began = time.perf_counter()
            plan_mission(navmesh, start, goal)
            cold.append((time.perf_counter() - began) * 1000)

        resolutions.append({
            "cell_m": cell, "width": navmesh["width"], "height": navmesh["height"],
            "counts": navmesh["counts"],
            "build_navmesh_ms": round(build_ms, 1),
            "navmesh_json_load_ms": round(load_ms, 1),
            "clearance_field_ms": round(field_ms, 1),
            "plan_with_cached_field_ms": percentiles(warm),
            "plan_cold_ms": percentiles(cold),
            "fits_budget": percentiles(cold)["p95"] <= BUDGET_P95_MS,
            "surface_points_per_cell": round((cell / STEP) ** 2, 1),
            "missions": len(pairs),
            "median_path_length_m": round(statistics.median(lengths), 2),
        })

    document = {
        "source": ("Synthetic furnished room from scripts/bench_mission_planning.py — not a "
                   "scan. No real scan exists yet; re-run on one when it does."),
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": "python scripts/bench_mission_planning.py",
        "machine": {"cpu": cpu_name(), "os": platform.platform(),
                    "python": platform.python_version(), "numpy": np.__version__,
                    "scipy": scipy.__version__},
        "room": {"size_m": list(ROOM), "sample_step_m": STEP, "points": int(len(points)),
                 "outliers": n_outliers, "furniture": len(FURNITURE), "profile": PROFILE,
                 "estimated_floor_z_m": round(floor_z, 4),
                 # What twin/navmesh.py:choose_cell picks for this cloud, and why.
                 "auto_cell_m": auto_cell, "auto_cell_evidence": auto_evidence},
        "floor_estimate_ms": round(floor_ms, 1),
        "resolutions": resolutions,
        "budget_p95_ms": BUDGET_P95_MS,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(document, f, indent=2)
        f.write("\n")

    print(f"{len(points):,} points, floor at {floor_z:+.4f} m ({floor_ms:.0f} ms)")
    for r in resolutions:
        cached, cold = r["plan_with_cached_field_ms"]["p95"], r["plan_cold_ms"]["p95"]
        print(f"cell {r['cell_m']} m: {r['width']}x{r['height']}, "
              f"build {r['build_navmesh_ms']} ms, field {r['clearance_field_ms']} ms, "
              f"plan p95 {cached} ms cached / {cold} ms cold, "
              f"{'within' if r['fits_budget'] else 'over'} the {BUDGET_P95_MS} ms budget")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
