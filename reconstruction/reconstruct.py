"""Stage entry point: scan directory of .npz frames -> point-cloud artifacts.

mode='fast'     -> numpy fusion (Open3D TSDF when installed)
mode='fidelity' -> not implemented: raises NotImplementedError, which /reconstruct answers with 501
"""
import array
import glob
import shutil
import os
import typing
import warnings

import numpy as np

from reconstruction.fast_path.fusion import fuse_frames_with_stats

# Filename an imported (non-frame) capture lands under inside the scan directory.
IMPORTED_PLY = "scaniverse.ply"

MODES = ("fast", "fidelity")


def load_frames(scan_dir: str):
    frames = []
    for path in sorted(glob.glob(os.path.join(scan_dir, "frame_*.npz"))):
        with np.load(path) as npz:
            frames.append({k: npz[k] for k in npz.files})
    return frames


def write_ply(path: str, points, colors) -> str:
    colors = np.asarray(colors, dtype=np.uint8)
    with open(path, "w") as f:
        f.write(
            "ply\nformat ascii 1.0\n"
            f"element vertex {len(points)}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\n"
            "end_header\n"
        )
        for p, c in zip(points, colors):
            f.write(f"{p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {c[0]} {c[1]} {c[2]}\n")
    return path


# PLY scalar type names -> numpy. Both the spec names and the C names are in the wild.
_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}
DEFAULT_GREY = 200  # vertices with no colour channel render as light grey
# Bounds checked before anything is allocated: a header can declare any count, and a 256 MiB
# import can hold tens of millions of short lines. Ten million points is far beyond what the
# twin needs (previews stop at 500k) and keeps a parsed cloud under ~300 MB.
MAX_VERTICES = 10_000_000
MAX_HEADER_LINES = 1000


def _parse_ply_header(f) -> tuple:
    """Consume the header off a binary-mode file. -> (format, [(name, count, props)]).

    props is [(prop_name, numpy_type_or_None)]; None marks a `property list` (faces),
    which has no fixed width and so cannot be read as a flat record.
    """
    if f.readline(64).strip() != b"ply":
        raise ValueError("not a PLY file (missing 'ply' magic)")
    fmt: typing.Optional[str] = None
    elements: typing.List[typing.Any] = []
    for _ in range(MAX_HEADER_LINES):
        line = f.readline(4096)
        if not line:
            raise ValueError("PLY header ended without 'end_header'")
        parts = line.split()
        if not parts:
            continue
        if parts[0] == b"format" and len(parts) > 1:
            fmt = parts[1].decode("ascii", "replace")
        elif parts[0] == b"element" and len(parts) > 2:
            count = int(parts[2])
            if count < 0:
                raise ValueError("PLY element counts cannot be negative")
            elements.append((parts[1].decode("ascii", "replace"), count, []))
        elif parts[0] == b"property" and elements:
            if parts[1] == b"list":
                elements[-1][2].append((parts[-1].decode(), None))
            else:
                elements[-1][2].append(
                    (parts[2].decode(), _PLY_TYPES.get(parts[1].decode())))
        elif parts[0] == b"end_header":
            break
    else:
        raise ValueError(f"PLY header is longer than {MAX_HEADER_LINES} lines")
    if fmt is None:
        raise ValueError("PLY header declares no format")
    return fmt, elements


def ply_elements(path: str) -> dict:
    """Element name -> declared count, read from the header alone (faces in an import)."""
    with open(path, "rb") as f:
        _, elements = _parse_ply_header(f)
    return {name: count for name, count, _ in elements}


def _split_xyz_rgb(fields: dict, count: int) -> tuple:
    """Pull xyz + rgb out of a name->column mapping, defaulting colour when absent."""
    missing = [axis for axis in ("x", "y", "z") if axis not in fields]
    if missing:
        raise ValueError(f"PLY vertices missing coordinate(s): {', '.join(missing)}")
    points = np.stack([fields["x"], fields["y"], fields["z"]], axis=1).astype(float)

    names = ("red", "green", "blue") if "red" in fields else ("r", "g", "b")
    if all(channel in fields for channel in names):
        rgb = np.stack([fields[channel] for channel in names], axis=1)
        # Float colour channels are 0..1 by convention; integer ones are already 0..255.
        if np.issubdtype(rgb.dtype, np.floating):
            rgb = rgb * 255.0
        colors = np.clip(rgb, 0, 255).astype(np.uint8)
    else:
        colors = np.full((count, 3), DEFAULT_GREY, dtype=np.uint8)
    return points, colors


def read_ply(path: str) -> tuple:
    """Read vertices from an ASCII or binary PLY. -> (points (N,3) float, colors (N,3) u8).

    Scaniverse and most scanning apps export binary_little_endian, so ASCII-only
    parsing silently yields an empty cloud on real scans. The vertex element must come first,
    as every scanner writes it; the declared count is checked against MAX_VERTICES and, for a
    binary body, against the bytes actually present before anything is allocated.
    """
    with open(path, "rb") as f:
        fmt, elements = _parse_ply_header(f)
        vertex = next((el for el in elements if el[0] == "vertex"), None)
        if vertex is None:
            raise ValueError("PLY has no 'vertex' element")
        if elements[0][0] != "vertex":
            raise ValueError("PLY 'vertex' element must come before the others")
        _, count, props = vertex
        if count > MAX_VERTICES:
            raise ValueError(f"PLY declares {count} vertices; at most {MAX_VERTICES} are supported")
        if count == 0:
            return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.uint8)
        if any(ptype is None for _, ptype in props):
            raise ValueError("PLY with a list property on 'vertex' is unsupported")

        if fmt == "ascii":
            try:
                with warnings.catch_warnings():
                    # numpy notes that blank lines no longer count towards max_rows: intended.
                    warnings.simplefilter("ignore", UserWarning)
                    table = np.loadtxt(f, dtype=np.float64, max_rows=count, ndmin=2,
                                       usecols=range(len(props)), comments=None)
            except ValueError as exc:
                raise ValueError(f"PLY vertex rows do not hold the {len(props)} declared "
                                 f"properties: {exc}") from exc
            if len(table) != count:
                raise ValueError(f"PLY declares {count} vertices, found {len(table)}")
            fields = {
                name: (table[:, i] if (ptype or "f").startswith("f")
                       else table[:, i].astype(np.int64))
                for i, (name, ptype) in enumerate(props)
            }
        elif fmt.startswith("binary"):
            order = "<" if "little" in fmt else ">"
            dtype = np.dtype([(name, order + ptype) for name, ptype in props])
            present = os.fstat(f.fileno()).st_size - f.tell()
            if present < dtype.itemsize * count:
                raise ValueError(
                    f"PLY declares {count} vertices but the body holds "
                    f"{present // dtype.itemsize}")
            raw = f.read(dtype.itemsize * count)
            arr = np.frombuffer(raw, dtype=dtype, count=count)
            fields = {name: arr[name] for name, _ in props}
        else:
            raise ValueError(f"unsupported PLY format: {fmt!r}")

    return _split_xyz_rgb(fields, count)


def read_obj(path: str) -> tuple:
    """Read `v` lines from a Wavefront OBJ. -> (points (N,3) float, colors (N,3) u8).

    Geometry only: faces, normals, and MTL materials are ignored, because every
    downstream stage (segmentation, twin generation) consumes a point cloud; the original
    file keeps them for the viewer. Vertex colours are the common `v x y z r g b`
    extension, 0..1 floats. Values are kept in compact arrays and the vertex count is
    bounded by MAX_VERTICES.
    """
    xyz = array.array("d")
    rgb_values = array.array("B")
    with open(path, errors="replace") as f:
        for line in f:
            if not line.startswith("v "):
                continue
            vals = line.split()[1:]
            if len(vals) < 3:
                continue
            if len(xyz) >= 3 * MAX_VERTICES:
                raise ValueError(f"OBJ holds more than {MAX_VERTICES} vertices")
            xyz.extend(float(v) for v in vals[:3])
            if len(vals) >= 6:
                rgb = [float(v) for v in vals[3:6]]
                # 0..1 floats are the OBJ convention; tolerate 0..255 exporters too.
                scale = 1.0 if max(rgb, default=0.0) > 1.0 else 255.0
                rgb_values.extend(min(255, max(0, int(c * scale))) for c in rgb)
            else:
                rgb_values.extend((DEFAULT_GREY,) * 3)
    return np.frombuffer(xyz, dtype=float).reshape(-1, 3).copy(), \
        np.frombuffer(rgb_values, dtype=np.uint8).reshape(-1, 3).copy()


def obj_face_count(path: str) -> int:
    """How many `f` lines an OBJ holds (the viewer triangulates and checks them)."""
    with open(path, "rb") as f:
        return sum(1 for line in f if line.startswith(b"f "))


def reconstruct(scan_dir: str, mode: str = "fast", out_dir: str | None = None,
                voxel_size: float = 0.02) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown reconstruction mode {mode!r}; "
                         f"expected one of {', '.join(MODES)}")
    if mode == "fidelity":
        # A photogrammetry path (COLMAP, splats) was planned for the hackathon and never built.
        raise NotImplementedError("fidelity reconstruction is not implemented; use mode='fast'")
    out_dir = out_dir or scan_dir
    os.makedirs(out_dir, exist_ok=True)
    frames = load_frames(scan_dir)
    if not frames:
        # An imported scan (Scaniverse export) has no depth frames to fuse — the cloud
        # arrived finished. Pass it through so /reconstruct works for either path.
        imported = os.path.join(scan_dir, IMPORTED_PLY)
        if not os.path.exists(imported):
            raise ValueError(f"no frame_*.npz files or {IMPORTED_PLY} in {scan_dir}")
        points, colors = read_ply(imported)
        ply_path = imported
        if os.path.abspath(out_dir) != os.path.abspath(scan_dir):
            ply_path = write_ply(os.path.join(out_dir, "mesh.ply"), points, colors)
            for ext in ("ply", "obj"):
                source = os.path.join(scan_dir, "surface." + ext)
                if os.path.isfile(source):
                    shutil.copyfile(source, os.path.join(out_dir, "surface." + ext))
        return {"ply_path": ply_path, "glb_path": None, "point_count": len(points),
                "fusion": None}
    points, colors, fusion = fuse_frames_with_stats(frames, voxel_size=voxel_size)
    ply_path = write_ply(os.path.join(out_dir, "mesh.ply"), points, colors)
    return {"ply_path": ply_path, "glb_path": None, "point_count": len(points),
            "fusion": fusion}
