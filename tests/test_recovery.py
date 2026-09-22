"""Failure and restart recovery (execution plan E0): every stage ends its job, closes what it
opened, and a restart marks unfinished work interrupted without resuming anything.

Failures are injected into each stage's own work (reconstruction, segmentation, twin generation,
planning, training, export, the simulated drive) with an exception no handler expects, so the
generic request-scope cleanup is what is under test.
"""
import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, jobs, service
from orchestrator.service import app
from tests.test_missions import GOAL, START, mission, twin_for
from tests.test_orchestrator import upload_scan


class Injected(ZeroDivisionError):
    """An exception no stage handles: only the request scope can clean up after it."""


class Tracked(sqlite3.Connection):
    opened: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        Tracked.opened.append(self)

    def close(self):
        self.closed = True
        super().close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def tracked(monkeypatch):
    """Every connection opened through orchestrator.db.connect, so a test can check they closed."""
    original = db.connect

    def connect(path=None):
        path = path or db.DB_PATH
        conn = sqlite3.connect(path, factory=Tracked)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    Tracked.opened = []
    monkeypatch.setattr(db, "connect", connect)
    yield Tracked.opened
    monkeypatch.setattr(db, "connect", original)


def trained(client):
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    policy = client.post("/train", json={"twin_id": twin["twin_id"],
                                         "mission_id": planned["mission_id"]}).json()
    return twin, planned, policy


def prepare(client, stage):
    """Everything the stage needs, and the request that exercises it."""
    if stage == "reconstruct":
        return "/reconstruct", {"scan_id": upload_scan(client)}
    if stage == "segment":
        mesh = client.post("/reconstruct", json={"scan_id": upload_scan(client)}).json()
        return "/segment", {"mesh_id": mesh["mesh_id"]}
    if stage == "twin_generate":
        mesh = client.post("/reconstruct", json={"scan_id": upload_scan(client)}).json()
        client.post("/segment", json={"mesh_id": mesh["mesh_id"]})
        return "/generate-twin", {"mesh_id": mesh["mesh_id"], "environment": "home"}
    if stage == "plan":
        _, twin = twin_for(client)
        return "/plan", {"twin_id": twin["twin_id"], "text": "go to the table"}
    if stage == "train":
        _, twin = twin_for(client)
        planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
        return "/train", {"twin_id": twin["twin_id"], "mission_id": planned["mission_id"]}
    if stage == "optimize":
        _, _, policy = trained(client)
        return "/optimize", {"policy_id": policy["policy_id"]}
    if stage == "deploy":
        _, _, policy = trained(client)
        artifact = client.post("/optimize", json={"policy_id": policy["policy_id"]}).json()
        return "/deploy", {"artifact_id": artifact["artifact_id"], "kind": "sim"}
    raise AssertionError(stage)


# What each stage calls to do its work: replacing it injects the failure.
WORK = {"reconstruct": "reconstruct", "segment": "segment_points",
        "twin_generate": "generate_twin", "plan": "plan_instruction", "train": "finetune_bc",
        "optimize": "export_model", "deploy": "run_policy"}


@pytest.mark.parametrize("stage", list(WORK))
def test_an_unexpected_failure_ends_the_job_and_closes_every_connection(
        client, tracked, monkeypatch, stage):
    route, body = prepare(client, stage)
    jobs_before = {job["job_id"] for job in jobs.all_jobs()}
    Tracked.opened.clear()

    def explode(*args, **kwargs):
        raise Injected(f"injected into {stage}")

    monkeypatch.setattr(service, WORK[stage], explode)
    response = client.post(route, json=body)
    assert response.status_code == 500

    new = [job for job in jobs.all_jobs() if job["job_id"] not in jobs_before]
    assert new, "the stage should have recorded a job before failing"
    assert all(job["status"] == "failed" for job in new), new
    assert any(f"injected into {stage}" in str(job["detail"]) for job in new)
    assert [conn for conn in Tracked.opened if not conn.closed] == []


def test_a_successful_request_also_closes_its_connections(client, tracked):
    upload_scan(client)
    assert Tracked.opened and all(conn.closed for conn in Tracked.opened)


def test_a_restart_interrupts_unfinished_work_and_resumes_none_of_it(client, tmp_path):
    scan = upload_scan(client)
    with closing(db.connect()) as conn:
        db.init_db(conn)
        running_job = jobs.create_job("train")
        db.insert(conn, "request_results", id="deploy:lost-in-restart", route="deploy",
                  payload_hash="x", status="running")
        robot = db.insert(conn, "robots", kind="sim", config_json="{}")
        mesh = db.insert(conn, "meshes", scan_id=scan, mode="fast", ply_url="none.ply")
        twin = db.insert(conn, "twins", mesh_id=mesh, scene_url="s", navmesh_url="n")
        policy = db.insert(conn, "policies", twin_id=twin)
        artifact = db.insert(conn, "artifacts", policy_id=policy)
        deployment = db.insert(conn, "deployments", artifact_id=artifact, robot_id=robot,
                               status="running")
        db.insert(conn, "calibration_sessions", id="cal-running", robot_id=robot,
                  status="running", provenance="simulated", result_json="{}")

    with TestClient(app):  # startup runs the orchestrator's restart path
        pass

    with closing(db.connect()) as conn:
        db.init_db(conn)
        assert jobs.get_job(running_job)["status"] == "interrupted"
        assert db.get(conn, "request_results", "deploy:lost-in-restart")["status"] == \
            "interrupted"
        assert db.get(conn, "deployments", deployment)["status"] == "interrupted"
        assert db.get(conn, "calibration_sessions", "cal-running")["status"] == "interrupted"
    assert service.DEPLOYMENTS.active_for_robot(robot) is None  # nothing restarted a drive

    lookup = client.get("/deploy/requests/lost-in-restart")
    assert lookup.status_code == 200 and lookup.json()["status"] == "interrupted"


@pytest.mark.parametrize("stage", ["plan", "train", "optimize", "deploy"])
def test_a_request_id_reused_with_different_input_is_refused(client, stage):
    route, body = prepare(client, stage)
    first = client.post(route, json={**body, "request_id": f"reuse-{stage}"})
    assert first.status_code == 200, first.text
    changed = {**body, "request_id": f"reuse-{stage}", "note": "a different request"}
    refused = client.post(route, json=changed)
    assert refused.status_code == 409
    assert "different inputs" in json.dumps(refused.json())


@pytest.mark.parametrize("stage", ["reconstruct", "segment", "twin_generate", "train", "optimize"])
def test_stage_result_lookup_recovers_exact_response_without_replay(client, stage):
    path, body = prepare(client, stage)
    body["request_id"] = "lost-response"
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    with closing(db.connect()) as conn:
        before = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    for _ in range(2):
        recovered = client.get(f"/stages{path}/requests/lost-response")
        assert recovered.status_code == 200
        assert recovered.json() == {"stage": path[1:], "request_id": "lost-response",
                                    "status": "done", "response": response.json()}
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == before
    assert client.get(f"/stages{path}/requests/unknown").status_code == 404
    assert client.get("/stages/deploy/requests/lost-response").status_code == 404


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
def test_stage_lookup_reports_unfinished_requests_without_mutation(client, status):
    with closing(db.connect()) as conn:
        db.init_db(conn)
        conn.execute("INSERT INTO request_results(id,route,payload_hash,status) VALUES(?,?,?,?)",
                     ("segment:pending", "segment", "hash", status))
        conn.commit()
    response = client.get("/stages/segment/requests/pending")
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["response"] is None
    assert client.get("/stages/train/requests/pending").status_code == 404
