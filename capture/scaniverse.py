"""Ingest scan exports (Scaniverse and friends) into ScanBorn's PLY pipeline.

Scaniverse is an external phone-side capture app. Its exports arrive as:
- `.ply` point clouds — ScanBorn's native format, but usually *binary*, not ASCII.
- `.obj` meshes — geometry taken as a point cloud; MTL/textures are ignored.
- `.glb` / `.usdz` — not supported (see ingest_export).

Everything is normalised to one ASCII PLY at `scan_dir/scaniverse.ply`, in the map frame
(metres, right-handed, Z up), so that every downstream stage sees a single shape regardless of
what the user picked. An export's units and up axis are not in the file: the operator declares
them, and an undeclared one is recorded as assumed (metres, Z up — what earlier imports used).
The export itself is kept byte for byte as `surface.<ext>`, with the transform that maps it.
"""
import hashlib
import os
import numpy as np

from reconstruction.reconstruct import (IMPORTED_PLY, obj_face_count, ply_elements, read_obj,
                                        read_ply, write_ply)

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "ft": 0.3048, "in": 0.0254}
# Rows are map axes, columns file axes. A Y-up export (most mesh tools and ARKit-based apps)
# looks along -Z; the map looks along +Y, so file -Z becomes map +Y and file +Y map +Z.
UP_AXES = {
    "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
}
DEFAULT_UNITS, DEFAULT_UP_AXIS = "m", "z"


def to_map(units: str = DEFAULT_UNITS, up_axis: str = DEFAULT_UP_AXIS) -> np.ndarray:
    """The 4x4 transform from an export's coordinates to the map frame."""
    if units not in UNITS:
        raise ValueError(f"unknown units {units!r}; expected one of {', '.join(UNITS)}")
    if up_axis not in UP_AXES:
        raise ValueError(f"unknown up axis {up_axis!r}; expected one of {', '.join(UP_AXES)}")
    transform = np.eye(4)
    transform[:3, :3] = np.asarray(UP_AXES[up_axis]) * UNITS[units]
    return transform


def _copy_with_digest(source: str, target: str) -> tuple:
    digest, size = hashlib.sha256(), 0
    with open(source, "rb") as reader, open(target, "wb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


SUPPORTED_EXTS = (".ply", ".obj")
DEFERRED_EXTS = {
    ".glb": "glb import not yet supported; export PLY or OBJ from Scaniverse",
    ".gltf": "gltf import not yet supported; export PLY or OBJ from Scaniverse",
    ".usdz": "usdz import not yet supported; export PLY or OBJ from Scaniverse",
    ".fbx": "fbx import not yet supported; export PLY or OBJ from Scaniverse",
}


def ingest_export(path: str, scan_dir: str, units: str | None = None,
                  up_axis: str | None = None) -> dict:
    """Normalise a scan export into `scan_dir/scaniverse.ply`, in the map frame.

    Args:
        path: Path to the export file (.ply or .obj).
        scan_dir: Destination scan directory.
        units, up_axis: What the operator declared; None records the default as assumed.

    Returns:
        Dict with keys: ply_path, point_count, source, format, import (the record kept in the
        scan's meta.json: original file, its SHA-256 and size, units and up axis with whether
        each was declared or assumed, the to_map transform, point and face counts).

    Raises:
        NotImplementedError: For formats deferred to a later milestone (.glb, .usdz, ...).
        ValueError: For an unrecognised extension, or an export with no vertices.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in DEFERRED_EXTS:
        raise NotImplementedError(DEFERRED_EXTS[ext])
    if ext not in SUPPORTED_EXTS:
        raise ValueError(
            f"unsupported export type {ext!r}; expected one of {', '.join(SUPPORTED_EXTS)}")
    units_declared, up_declared = units is not None, up_axis is not None
    units = DEFAULT_UNITS if units is None else units
    up_axis = DEFAULT_UP_AXIS if up_axis is None else up_axis
    transform = to_map(units, up_axis)

    points, colors = (read_ply if ext == ".ply" else read_obj)(path)
    if not np.isfinite(points).all():
        raise ValueError("imported vertices must be finite")
    if len(points) == 0:
        raise ValueError(f"export {os.path.basename(path)} contains no vertices")
    faces = ply_elements(path).get("face", 0) if ext == ".ply" else obj_face_count(path)
    points = points @ transform[:3, :3].T

    os.makedirs(scan_dir, exist_ok=True)
    raw_path = os.path.join(scan_dir, "surface" + ext)
    if os.path.abspath(path) == os.path.abspath(raw_path):
        raise ValueError("the export must not already be the scan's kept original")
    sha256, size = _copy_with_digest(path, raw_path)
    # ponytail: re-encodes to ASCII PLY, which roughly triples a large scan on disk.
    # Swap write_ply for a binary writer if scan sizes start to hurt.
    dest_path = write_ply(os.path.join(scan_dir, IMPORTED_PLY), points, colors)
    return {
        "ply_path": dest_path,
        "point_count": len(points),
        "source": "scaniverse",
        "format": ext.lstrip("."),
        "import": {
            "format": ext.lstrip("."), "original": "surface" + ext,
            "original_sha256": sha256, "original_bytes": size,
            "units": units, "units_source": "declared" if units_declared else "assumed",
            "up_axis": up_axis, "up_axis_source": "declared" if up_declared else "assumed",
            "to_map": transform.tolist(), "point_count": int(len(points)),
            "face_count": int(faces),
        },
    }
