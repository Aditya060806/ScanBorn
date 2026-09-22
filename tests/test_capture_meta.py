"""A phone scan's meta through the orchestrator: validated, merged, and carried to the twin."""
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.service import app
from tests.test_orchestrator import frame_bytes, scan_frame

HOME = [0.0, 0.0, 1.5708]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    for key in ("SARVAM_API_KEY", "AI_HUB_API_TOKEN", "SCANBORN_PLANNER", "SCANBORN_PROFILER"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(app)


def upload(client, index: int, scan_id: str | None = None, meta=None):
    params: dict = {"index": index}
    if scan_id:
        params["scan_id"] = scan_id
    if meta is not None:
        params["meta"] = meta if isinstance(meta, str) else json.dumps(meta)
    return client.post("/capture", files={"file": ("f.npz", frame_bytes(scan_frame()))},
                       params=params)


def read_meta(tmp_path, scan_id: str) -> dict:
    return json.loads((tmp_path / "data" / "scans" / scan_id / "meta.json").read_text())


def test_the_home_pose_sent_first_survives_later_meta_and_reaches_the_twin(client, tmp_path):
    first = upload(client, 0, meta={"device": "Galaxy S24 FE", "depth_source": "raw",
                                    "depth_mode": "RAW_DEPTH_ONLY", "home_pose": HOME})
    assert first.status_code == 200, first.text
    scan_id = first.json()["scan_id"]
    assert upload(client, 1, scan_id, meta={"skipped": {"turning": 3}}).status_code == 200

    done = client.post(f"/capture/{scan_id}/complete",
                       json={"meta": {"frames": 2, "uploaded": 2, "failed": 0}})
    assert done.status_code == 200 and done.json()["frame_count"] == 2
    meta = read_meta(tmp_path, scan_id)
    assert meta["home_pose"] == HOME and meta["skipped"] == {"turning": 3}
    assert meta["uploaded"] == 2 and meta["device"] == "Galaxy S24 FE"

    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    assert mesh["capture"]["home_pose"] == HOME and mesh["capture"]["depth_source"] == "raw"
    assert mesh["capture"]["frames"] == 2
    # Two identical frames: every voxel was seen twice, so the stricter rule keeps them all.
    assert mesh["fusion"] == {"frames": 2, "min_views": 2, "voxels": mesh["point_count"],
                              "dropped_voxels": 0}

    objects = client.post("/segment", json={"mesh_id": mesh["mesh_id"]}).json()
    twin = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"],
                                               "objects_id": objects["objects_id"],
                                               "environment": "home"}).json()
    assert client.get(f"/twins/{twin['twin_id']}/navmesh").json()["home_pose"] == HOME


@pytest.mark.parametrize("meta, field", [
    ("not json", None),
    ('["a", "list"]', None),
    ({"home_pose": [0, 0]}, "home_pose"),
    ({"home_pose": [0, "x", 1]}, "home_pose"),
    ({"home_pose": [0, True, 1]}, "home_pose"),
    ('{"home_pose": [0, NaN, 1]}', "home_pose"),
])
def test_bad_meta_is_refused_before_a_scan_is_stored(client, tmp_path, meta, field):
    response = upload(client, 0, meta=meta)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "bad_meta" and detail.get("field") == field
    scans = tmp_path / "data" / "scans"
    assert not scans.exists() or not any(scans.iterdir())


def test_a_bad_meta_body_on_complete_changes_nothing(client, tmp_path):
    scan_id = upload(client, 0, meta={"home_pose": HOME}).json()["scan_id"]
    refused = client.post(f"/capture/{scan_id}/complete", json={"meta": {"home_pose": "here"}})
    assert refused.status_code == 400 and refused.json()["detail"]["field"] == "home_pose"
    assert read_meta(tmp_path, scan_id)["home_pose"] == HOME
    assert client.post(f"/capture/{scan_id}/complete").status_code == 200  # no body still works


def test_a_scan_sent_without_meta_reports_fusion_but_no_capture(client, tmp_path):
    scan_id = upload(client, 0).json()["scan_id"]
    client.post(f"/capture/{scan_id}/complete")
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    assert mesh["capture"] is None  # no meta was sent
    assert mesh["fusion"]["frames"] == 1 and mesh["fusion"]["min_views"] == 1
