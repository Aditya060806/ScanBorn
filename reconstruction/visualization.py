"""Versioned, bounded RGB previews; source reconstruction is never modified."""
import hashlib
import json
import threading
from pathlib import Path

import numpy as np

from capture.service.store import atomic_write, data_root
from reconstruction.floor import estimate_floor_z
from reconstruction.reconstruct import read_ply

LEVELS = (100_000, 500_000)
_LOCK = threading.Lock()


def artifact_path(value: str) -> Path:
    root = Path(data_root()).resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("geometry artifact is missing or outside the configured data directory")
    return path


def previews(ply_path: str) -> dict:
    source = artifact_path(ply_path)
    folder = source.parent / "visualization-v1"
    manifest = folder / "geometry.json"
    with _LOCK:
        if manifest.exists():
            return json.loads(manifest.read_text(encoding="utf-8"))
        points, colors = read_ply(str(source))
        if len(points) == 0 or not np.isfinite(points).all():
            raise ValueError("reconstruction has no finite geometry")
        folder.mkdir(exist_ok=True)
        levels = []
        # Packed 16-byte records, little endian xyz float32, RGB sRGB uint8, padding.
        for cap in LEVELS:
            indices = np.linspace(0, len(points) - 1, min(len(points), cap), dtype=np.int64)
            records = np.zeros((len(indices), 16), dtype=np.uint8)
            records[:, :12].view("<f4")[:] = points[indices]
            records[:, 12:15] = np.asarray(colors, dtype=np.uint8)[indices]
            content = records.tobytes()
            atomic_write(str(folder / f"points-{cap}.bin"), content)
            levels.append({"level": cap, "point_count": len(indices), "bytes": len(content),
                           "sha256": hashlib.sha256(content).hexdigest()})
        result = {"point_count": len(points), "bounds": [points.min(axis=0).tolist(),
                                                         points.max(axis=0).tolist()],
                  "floor_z": float(estimate_floor_z(points)), "levels": levels}
        atomic_write(str(manifest), json.dumps(result, allow_nan=False).encode())
        return result


def geometry_asset(ply_path: str, level: int) -> Path:
    if level not in LEVELS:
        raise ValueError("unknown geometry level")
    previews(ply_path)
    return artifact_path(str(Path(ply_path).parent / "visualization-v1" / f"points-{level}.bin"))
