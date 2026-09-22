import io
import zipfile

import numpy as np
import pytest

from capture.service import store
from capture.service.validation import validate_frame
from tests.test_orchestrator import frame_bytes, scan_frame


@pytest.mark.parametrize("field,value", [
    ("depth", np.array([[float("nan")]])),
    ("depth", np.array([[-1.0]])),
    ("intrinsics", np.zeros((3, 3))),
    ("pose", np.zeros((4, 4))),
    ("color", np.zeros((2, 2, 3))),
])
def test_bad_camera_arrays(field, value):
    frame = scan_frame()
    frame[field] = value
    with pytest.raises(ValueError):
        validate_frame(frame_bytes(frame))


def test_valid_frame_and_pickle_refusal():
    validate_frame(frame_bytes(scan_frame()))
    stream = io.BytesIO()
    np.savez(stream, depth=np.array([{}], dtype=object))
    with pytest.raises(ValueError):
        validate_frame(stream.getvalue())


def test_array_header_cannot_allocate_more_than_uploaded_payload():
    declared = io.BytesIO()
    np.lib.format.write_array_header_1_0(declared, {
        "descr": "<f8", "fortran_order": False, "shape": (2**30, 2**30)})
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w") as archive:
        archive.writestr("depth.npy", declared.getvalue())
    with pytest.raises(ValueError, match="declaration"):
        validate_frame(compressed.getvalue())


@pytest.mark.parametrize("identifier", ["../escape", "a/b", "C:\\escape", ".", ""])
def test_scan_paths_are_confined(tmp_path, monkeypatch, identifier):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path))
    with pytest.raises(ValueError):
        store.save_chunk(identifier, 0, b"frame")
    assert not list(tmp_path.iterdir())
