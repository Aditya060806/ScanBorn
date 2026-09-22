"""Chunked scan storage (capture/service/store.py), which the orchestrator's /capture routes use.

These tests once went through a standalone capture service with its own routes; nothing called that
service, and it was removed in Phase 7. The orchestrator's routes are covered in
tests/test_orchestrator.py and tests/test_capture_meta.py.
"""
import pytest

from capture.service import store


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path))


def test_chunked_upload_flow():
    for i in range(3):
        store.save_chunk("scan1", i, b"frame" + bytes([i]))
    assert store.status("scan1") == {"status": "uploading", "frame_count": 3}

    # Resumable: re-sending a chunk replaces it rather than adding a frame.
    path = store.save_chunk("scan1", 1, b"frame1x")
    with open(path, "rb") as f:
        assert f.read() == b"frame1x"

    store.save_meta("scan1", {"device": "test"})
    assert store.read_meta("scan1") == {"device": "test"}
    assert store.complete("scan1") == 3
    assert store.status("scan1") == {"status": "complete", "frame_count": 3}


def test_unknown_scan():
    assert store.status("nope") == {"status": "unknown", "frame_count": 0}
    assert store.read_meta("nope") == {}
