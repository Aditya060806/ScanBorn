"""Bound frame memory use and validate camera geometry before committing uploads."""
import io
import math
import zipfile

import numpy as np

MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_ARRAY_BYTES = 64 * 1024 * 1024
MAX_INDEX = 99999


def validate_frame(data: bytes) -> None:
    if not data or len(data) > MAX_CHUNK_BYTES:
        raise ValueError("frame must contain 1..16777216 bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 8 or sum(f.file_size for f in files) > MAX_ARRAY_BYTES:
                raise ValueError("frame expands beyond the array limit")
            names = [f.filename for f in files]
            if len(set(names)) != len(names):
                raise ValueError("duplicate frame arrays")
            for info in files:
                if not info.filename.endswith(".npy"):
                    raise ValueError("frame entries must be NPY arrays")
                with archive.open(info) as stream:
                    version = np.lib.format.read_magic(stream)
                    reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                              else np.lib.format.read_array_header_2_0 if version == (2, 0)
                              else None)
                    if reader is None:
                        raise ValueError("unsupported NPY version")
                    shape, _, dtype = reader(stream)
                    size = math.prod(shape) * dtype.itemsize
                    if (dtype.hasobject or size > MAX_ARRAY_BYTES
                            or size != info.file_size - stream.tell()):
                        raise ValueError("array declaration exceeds its stored payload")
        with np.load(io.BytesIO(data), allow_pickle=False) as frame:
            depth, k, pose = (frame[name] for name in ("depth", "intrinsics", "pose"))
            if depth.ndim != 2 or not all(0 < n <= 4096 for n in depth.shape):
                raise ValueError("depth must be a nonempty 2D array, at most 4096 per side")
            for name, array in (("depth", depth), ("intrinsics", k), ("pose", pose)):
                if array.dtype.kind not in "fiu" or not np.isfinite(array).all():
                    raise ValueError(f"{name} must contain finite numeric values")
            if np.any(depth < 0) or np.any(depth > 100):
                raise ValueError("depth must be in metres, between 0 and 100")
            if k.shape != (3, 3) or k[0, 0] <= 0 or k[1, 1] <= 0:
                raise ValueError("intrinsics must be 3x3 with positive focal lengths")
            if not np.allclose(k[2], [0, 0, 1], atol=1e-5):
                raise ValueError("invalid homogeneous camera intrinsics")
            if pose.shape != (4, 4) or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5):
                raise ValueError("pose must be a homogeneous 4x4 transform")
            rotation = pose[:3, :3]
            if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3) or not np.isclose(
                np.linalg.det(rotation), 1, atol=1e-3
            ):
                raise ValueError("pose rotation must be right-handed and orthonormal")
            if "color" in frame:
                color = frame["color"]
                if color.shape != (*depth.shape, 3) or color.dtype != np.uint8:
                    raise ValueError("color must be depth-sized RGB uint8")
    except (KeyError, OSError, zipfile.BadZipFile, EOFError) as exc:
        raise ValueError("invalid NPZ frame; depth, intrinsics and pose are required") from exc
