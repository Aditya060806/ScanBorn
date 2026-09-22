import importlib

from orchestrator import jobs
from orchestrator.deployments import Session
from tests.test_capture_meta import client  # noqa: F401
from tests.test_orchestrator import upload_scan


def test_stage_retry_is_deduplicated_and_jobs_survive_reload(client):  # noqa: F811
    scan = upload_scan(client)
    body = {"scan_id": scan, "request_id": "retry-first-response"}
    first = client.post("/reconstruct", json=body)
    assert first.status_code == 200
    second = client.post("/reconstruct", json=body)
    assert first.json() == second.json()
    assert client.post("/reconstruct", json={**body, "mode": "fidelity"}).status_code == 409
    identifier = first.json()["job_id"]
    importlib.reload(jobs)
    assert jobs.get_job(identifier)["status"] == "done"
    running = jobs.create_job("test-interruption")
    jobs.interrupt_running()
    assert jobs.get_job(running)["status"] == "interrupted"


def test_cross_pipeline_objects_are_refused(client):  # noqa: F811
    scan = upload_scan(client)
    mesh = client.post("/reconstruct", json={"scan_id": scan}).json()["mesh_id"]
    response = client.post("/generate-twin", json={"mesh_id": mesh, "objects_id": "another-mesh"})
    assert response.status_code == 400


def test_repeated_segmentation_keeps_one_immutable_object_set(client):  # noqa: F811
    scan = upload_scan(client)
    mesh = client.post("/reconstruct", json={"scan_id": scan}).json()["mesh_id"]
    first = client.post("/segment", json={"mesh_id": mesh}).json()
    second = client.post("/segment", json={"mesh_id": mesh}).json()
    assert first["objects"] == second["objects"]
    manifest = client.get(f"/meshes/{mesh}/visualization").json()
    assert len(manifest["objects"]) == len(first["objects"])


def test_telemetry_rejects_nonfinite_and_out_of_order_without_extending_deadman():
    session = Session("d", "m", None, [], None)
    frame = {"t": 1.0, "x": 0.0, "y": 0.0, "th": 0.0, "v": 0.1, "w": 0.0,
             "state": "running"}
    assert session.absorb(frame)
    heard = session.last_heard
    assert not session.absorb(frame)
    assert not session.absorb({**frame, "t": 2.0, "x": float("nan")})
    assert not session.absorb({**frame, "t": 0.5})
    assert session.frames == 1 and session.last_heard == heard
