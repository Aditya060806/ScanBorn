"""Chunked scan storage: data/scans/{scan_id}/frame_{index:05d}.npz + meta.json."""
import json
import os
import re
import tempfile
import threading
from pathlib import Path

_SCAN_LOCKS = [threading.RLock() for _ in range(64)]


def scan_lock(scan_id: str):
    """Bounded lock pool serializes commits and Finish within the single server process."""
    scan_path(scan_id)
    return _SCAN_LOCKS[hash(scan_id) % len(_SCAN_LOCKS)]


def data_root() -> str:
    return os.environ.get("SCANBORN_DATA", "data")


def scan_path(scan_id: str) -> Path:
    if (not isinstance(scan_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", scan_id)):
        raise ValueError("invalid scan identifier")
    root = (Path(data_root()) / "scans").resolve()
    path = (root / scan_id).resolve()
    if path.parent != root:
        raise ValueError("scan path escapes the configured data directory")
    return path


def scan_dir(scan_id: str) -> str:
    path = scan_path(scan_id)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def atomic_write(path: str, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_chunk(scan_id: str, index: int, data: bytes) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 99999:
        raise ValueError("frame index must be between 0 and 99999")
    path = os.path.join(scan_dir(scan_id), f"frame_{index:05d}.npz")
    atomic_write(path, data)
    return path


def save_meta(scan_id: str, meta: dict) -> str:
    path = os.path.join(scan_dir(scan_id), "meta.json")
    atomic_write(path, json.dumps(meta, allow_nan=False).encode("utf-8"))
    return path


def read_meta(scan_id: str) -> dict:
    """The scan's meta.json, or {} when there is none (or it is not a JSON object)."""
    path = str(scan_path(scan_id) / "meta.json")
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def frame_paths(scan_id: str):
    d = scan_dir(scan_id)
    return sorted(
        os.path.join(d, f) for f in os.listdir(d)
        if f.startswith("frame_") and f.endswith(".npz")
    )


def complete(scan_id: str) -> int:
    n = len(frame_paths(scan_id))
    atomic_write(os.path.join(scan_dir(scan_id), "COMPLETE"), str(n).encode("ascii"))
    return n


def status(scan_id: str) -> dict:
    d = str(scan_path(scan_id))
    if not os.path.isdir(d):
        return {"status": "unknown", "frame_count": 0}
    n = len(frame_paths(scan_id))
    done = os.path.exists(os.path.join(d, "COMPLETE"))
    return {"status": "complete" if done else "uploading", "frame_count": n}
