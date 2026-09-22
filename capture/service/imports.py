"""Bound import bytes and publish the complete scan directory in one rename."""
import json
import os
import tempfile
from pathlib import Path

from capture.scaniverse import ingest_export
from capture.service import store

MAX_IMPORT_BYTES = 256 * 1024 * 1024
EXPORTS_DIR = "exports"


def prepare_import(source, filename: str, destination: Path, units: str | None = None,
                   up_axis: str | None = None) -> dict:
    """Write only inside an unpublished staging directory owned by the caller.

    The scan's meta.json records the import (see capture.scaniverse.ingest_export) and that its
    registration is unknown: an export carries no home-marker frame, so it can be inspected and
    simulated, but a physical rover cannot share its frame.
    """
    ext = Path(filename).suffix.lower()
    raw = destination / ("upload" + ext)
    total = 0
    with raw.open("wb") as output:
        while True:
            chunk = source.read(min(1024 * 1024, MAX_IMPORT_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMPORT_BYTES:
                raise ValueError("scan export exceeds the 256 MiB upload limit")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    result = ingest_export(str(raw), str(destination), units=units, up_axis=up_axis)
    raw.unlink()
    store.atomic_write(str(destination / "COMPLETE"), b"0")
    meta = {"source": "imported", "registration": "unknown", "import": result["import"]}
    store.atomic_write(str(destination / "meta.json"),
                       json.dumps(meta, allow_nan=False).encode("utf-8"))
    return result


def export_path(value: str) -> Path:
    """A server-side export named by path must be a file inside <data>/exports.

    The route is unauthenticated on the LAN; confining it keeps a request from reading any
    .ply/.obj elsewhere on this machine.
    """
    root = (Path(store.data_root()) / EXPORTS_DIR).resolve()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"export_path must name a file inside {EXPORTS_DIR}/ in the data "
                         "directory")
    return path


def staging_directory():
    root = (Path(store.data_root()) / "imports").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="pending-", dir=root)
