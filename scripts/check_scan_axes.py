"""Axis check for a phone scan: does the scan's +Y point where the phone pointed at Start?

The operator starts the scan standing over the home marker, pointing along its arrow, then walks
straight ahead a tape-measured distance. In a correctly registered scan the phone's path runs along
+Y by that distance, with little sideways drift, and the first frame faces +Y
(docs/superpowers/plans/2026-09-15-phase-5-capture.md, decision 15).

    python scripts/check_scan_axes.py SCAN_ID [--walk-m 2.0] [--tolerance-m 0.15]
    python scripts/check_scan_axes.py --poses tests/fixtures/scan_axis_s24fe.json
    python scripts/check_scan_axes.py SCAN_ID --save-poses tests/fixtures/scan_axis_s24fe.json

Frame poses are camera -> map, with the OpenCV camera looking down its +Z. Exit code 0 on a pass.
"""
import argparse
import glob
import json
import math
import os
import sys

import numpy as np

HEADING_TOLERANCE_DEG = 10.0


def poses_from_scan(scan_dir: str) -> list:
    """The 4x4 camera->map pose of every frame in a scan directory, in frame order."""
    poses = []
    for path in sorted(glob.glob(os.path.join(scan_dir, "frame_*.npz"))):
        with np.load(path) as npz:
            poses.append(np.asarray(npz["pose"], dtype=np.float64).reshape(4, 4).tolist())
    return poses


def heading(pose) -> float | None:
    """The camera's viewing direction projected onto the floor, as an angle from +X; None when the
    camera looks straight up or down."""
    forward = np.asarray(pose, dtype=np.float64)[:3, :3] @ np.array([0.0, 0.0, 1.0])
    if math.hypot(forward[0], forward[1]) < 1e-6:
        return None
    return math.atan2(forward[1], forward[0])


def check(poses: list, walk_m: float, tolerance_m: float = 0.15) -> dict:
    """Judge a straight walk along +Y from the first pose to the last."""
    if len(poses) < 2:
        return {"passed": False, "reason": "need at least two frames", "frames": len(poses)}
    start = np.asarray(poses[0], dtype=np.float64)[:3, 3]
    end = np.asarray(poses[-1], dtype=np.float64)[:3, 3]
    dx, dy = float(end[0] - start[0]), float(end[1] - start[1])
    first = heading(poses[0])
    heading_error = (None if first is None else
                     math.degrees(abs(math.atan2(math.sin(first - math.pi / 2),
                                                 math.cos(first - math.pi / 2)))))
    along_ok = abs(dy - walk_m) <= tolerance_m
    sideways_ok = abs(dx) <= tolerance_m
    facing_ok = heading_error is not None and heading_error <= HEADING_TOLERANCE_DEG
    return {
        "passed": along_ok and sideways_ok and facing_ok,
        "frames": len(poses), "walk_m": walk_m, "tolerance_m": tolerance_m,
        "along_y_m": round(dy, 3), "sideways_x_m": round(dx, 3),
        "start_xy": [round(float(start[0]), 3), round(float(start[1]), 3)],
        "first_heading_error_deg": None if heading_error is None else round(heading_error, 1),
        "along_ok": along_ok, "sideways_ok": sideways_ok, "facing_ok": facing_ok,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Axis check for a phone scan.")
    parser.add_argument("scan_id", nargs="?")
    parser.add_argument("--data", default=os.environ.get("SCANBORN_DATA", "data"))
    parser.add_argument("--poses", help="a JSON file of poses instead of a scan")
    parser.add_argument("--walk-m", type=float, default=2.0)
    parser.add_argument("--tolerance-m", type=float, default=0.15)
    parser.add_argument("--save-poses", help="write the scan's poses to this JSON file")
    args = parser.parse_args()

    if args.poses:
        with open(args.poses, encoding="utf-8") as f:
            document = json.load(f)
        poses = document["poses"] if isinstance(document, dict) else document
    elif args.scan_id:
        poses = poses_from_scan(os.path.join(args.data, "scans", args.scan_id))
    else:
        parser.error("give a scan id or --poses")

    result = check(poses, args.walk_m, args.tolerance_m)
    if args.save_poses and args.scan_id:
        with open(args.save_poses, "w", encoding="utf-8") as f:
            json.dump({"scan_id": args.scan_id, "walk_m": args.walk_m, "poses": poses,
                       "result": result}, f, indent=1)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
