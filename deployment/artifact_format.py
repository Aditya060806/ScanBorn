"""What an exported file actually is, read from its bytes — never from the path that wrote it.

ScanBorn has exactly one deployable runtime: the int8 linear controller in an `.npz` bundle
(`policy_int8.npz`: int8 `W` and `b` with float scales). The simulator, the fake rover and the
ESP32 firmware (via `robot/protocol.py:load_bundle`) all execute that format and nothing else.
Anything an optional exporter returns (an AI Hub TFLite, a QAIRT DLC) is an experiment until a
runtime in this repository executes those exact bytes and passes the same mission gate.
"""
import zipfile

import numpy as np

NPZ_INT8 = "npz-int8"
NPZ_FLOAT = "npz-float"
NPZ_OTHER = "npz-other"
TFLITE = "tflite"
UNKNOWN = "unknown"

# The one runtime that executes ScanBorn policies, and what runs it.
RUNTIME = "scanborn-int8-linear-v1"
RUNTIME_FORMATS = frozenset({NPZ_INT8})
RUNTIME_EXECUTORS = ("simulator", "fake_rover", "esp32_rover")
INT8_KEYS = frozenset({"W", "W_scale", "b", "b_scale"})


def detect(path: str) -> str:
    """Identify an artifact from its content. A missing or unreadable file is `unknown`."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(8)
    except OSError:
        return UNKNOWN
    if head[:4] == b"PK\x03\x04":
        try:
            with np.load(path, allow_pickle=False) as npz:
                keys = set(npz.files)
                if INT8_KEYS <= keys and npz["W"].dtype == np.int8 and npz["b"].dtype == np.int8:
                    return NPZ_INT8
                if {"W", "b"} <= keys:
                    return NPZ_FLOAT
                return NPZ_OTHER
        except (OSError, ValueError, zipfile.BadZipFile, KeyError):
            return UNKNOWN
    # FlatBuffers put the file identifier at bytes 4..8; TensorFlow Lite's is "TFL3".
    if head[4:8] == b"TFL3":
        return TFLITE
    return UNKNOWN


def runtime_refusal(path: str) -> str | None:
    """Why these bytes cannot run on ScanBorn's runtime, or None when they can."""
    found = detect(path)
    if found in RUNTIME_FORMATS:
        return None
    return (f"This artifact is {found!r}; ScanBorn's rover, fake rover and simulator execute "
            f"only {NPZ_INT8!r} bundles ({RUNTIME}). Optimize the policy with the local exporter.")
