"""Missions through the REST surface: the navmesh, planning, training, deploying, staleness."""
import json
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.service import app
from policy.planning import cell_of
from tests.test_orchestrator import FOCAL, HEIGHT, SIZE, frame_bytes, scan_frame
from twin.navmesh import BLOCKED, FREE, decode_cells

# The synthetic room (tests/test_orchestrator.py) spans about ±2.5 m. Its table top covers
# x 0.44..1.26, y -1.26..-0.44; its chair-height box x -1.09..-0.45, y 0.45..1.09.
# A ~2.5 m route through the middle of the room: the length dead reckoning drives within the
# 10 cm bar (Phase 3 plan, decision 14), and across the spot the stale test puts a box on.
START, GOAL = [-1.2, -1.2], [0.6, 0.6]
TABLE = (0.85, -0.85)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    for key in ("SARVAM_API_KEY", "AI_HUB_API_TOKEN", "SCANBORN_PLANNER", "SCANBORN_PROFILER"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(app)


def upload(client, frame) -> str:
    scan_id = client.post("/capture", files={"file": ("f.npz", frame_bytes(frame))},
                          params={"index": 0}).json()["scan_id"]
    assert client.post(f"/capture/{scan_id}/complete").status_code == 200
    return scan_id


def twin_for(client, frame=None) -> tuple:
    """Scan -> reconstruct -> segment -> twin for the home profile (radius 0.20 m)."""
    scan_id = upload(client, frame or scan_frame())
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    objects = client.post("/segment", json={"mesh_id": mesh["mesh_id"]}).json()
    twin = client.post("/generate-twin", json={
        "mesh_id": mesh["mesh_id"], "objects_id": objects["objects_id"],
        "environment": "home"}).json()
    return scan_id, twin


def with_box(x0, x1, y0, y1, top):
    """The synthetic room plus a box standing on its floor, seen by the same camera."""
    frame = scan_frame()
    depth = HEIGHT - top
    u0, u1 = sorted(round(x * FOCAL / depth + SIZE / 2) for x in (x0, x1))
    v0, v1 = sorted(round(-y * FOCAL / depth + SIZE / 2) for y in (y0, y1))
    frame["depth"][v0:v1, u0:u1] = depth
    return frame


def mission(client, twin_id, **body):
    return client.post("/missions", json={"twin_id": twin_id, **body})


def test_the_navmesh_endpoint_serves_the_twin_grid(client):
    _, twin = twin_for(client)
    nav = client.get(f"/twins/{twin['twin_id']}/navmesh").json()
    cells = decode_cells(nav["cells_b64"], nav["width"], nav["height"])
    band = decode_cells(nav["band_b64"], nav["width"], nav["height"]).astype(bool)

    navmesh_path = os.path.join(os.path.dirname(twin["scene_url"]), "navmesh.json")
    with open(navmesh_path) as f:
        assert np.array_equal(cells, np.asarray(json.load(f)["grid"], dtype=np.uint8))
    assert sum(nav["counts"].values()) == nav["width"] * nav["height"]
    assert nav["counts"] == twin["counts"]
    assert cells[cell_of(nav, *TABLE)] == BLOCKED
    assert band.any() and not (band & (cells != FREE)).any()
    assert nav["robot_radius"] == 0.2 and nav["profile"]["environment"] == "home"
    assert nav["home_pose"] is None and nav["version"] == 2
    assert client.get("/twins/nope/navmesh").status_code == 404


def test_a_mission_is_a_clear_route_from_start_to_goal(client):
    _, twin = twin_for(client)
    response = mission(client, twin["twin_id"], start=START, goal=GOAL)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "ready"
    assert body["waypoints"][0] == START and body["waypoints"][-1] == GOAL
    assert body["path"][0] == START and body["path"][-1] == GOAL
    assert body["min_clearance_m"] > 0.2
    assert body["length_m"] >= math.dist(START, GOAL) - 1e-3
    assert body["plan_ms"] < 1000
    assert client.get(f"/missions/{body['mission_id']}").json()["path"] == body["path"]


def test_a_goal_on_the_table_is_refused_with_a_point_that_works(client):
    _, twin = twin_for(client)
    refused = mission(client, twin["twin_id"], start=START, goal=list(TABLE))
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["code"] == "goal_blocked" and "obstacle" in detail["error"]
    assert mission(client, twin["twin_id"], start=START, goal=detail["nearest"]).status_code == 200


def test_the_home_pose_is_the_default_start_and_without_it_a_start_is_required(client, tmp_path):
    scan_id, twin = twin_for(client)
    refused = mission(client, twin["twin_id"], goal=GOAL)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "no_start"
    assert len(refused.json()["detail"]["nearest"]) == 2

    meta = tmp_path / "data" / "scans" / scan_id / "meta.json"
    meta.write_text(json.dumps({"device": "test", "home_pose": [-1.2, -1.2, 0.5]}))
    planned = mission(client, twin["twin_id"], goal=GOAL).json()
    assert planned["start"] == [-1.2, -1.2, 0.5]
    assert client.get(f"/twins/{twin['twin_id']}/navmesh").json()["home_pose"] == [-1.2, -1.2, 0.5]


def test_malformed_mission_requests_are_refused(client):
    _, twin = twin_for(client)
    both = mission(client, twin["twin_id"], start=START, goal=GOAL, task_graph_id="x")
    neither = mission(client, twin["twin_id"], start=START)
    assert both.status_code == neither.status_code == 422
    assert both.json()["detail"]["code"] == "bad_request"
    assert mission(client, twin["twin_id"], start=START, goal=[1.0]).status_code == 422
    assert mission(client, "no-such-twin", start=START, goal=GOAL).status_code == 404
    assert client.get("/missions/nope").status_code == 404


def test_a_plan_can_supply_the_goal(client):
    _, twin = twin_for(client)
    graph = client.post("/plan", json={"twin_id": twin["twin_id"],
                                       "text": "go to the table"}).json()
    response = mission(client, twin["twin_id"], start=START,
                       task_graph_id=graph["task_graph_id"])
    assert response.status_code == 200, response.text
    assert math.dist(response.json()["goal"], TABLE) < 1.2
    assert response.json()["task_graph_id"] == graph["task_graph_id"]


def test_training_and_deploying_follow_the_mission_until_the_room_changes(client):
    _, twin = twin_for(client)
    twin_id = twin["twin_id"]
    crossing = mission(client, twin_id, start=[*START, 0.0], goal=GOAL).json()
    aside = mission(client, twin_id, start=[1.9, 1.9], goal=[1.9, 0.6]).json()

    trained = client.post("/train", json={"twin_id": twin_id, "mission_id": crossing["mission_id"]})
    assert trained.status_code == 200, trained.text
    body = trained.json()
    metrics = body["mission_metrics"]
    assert body["mission_id"] == crossing["mission_id"]
    assert metrics["passed"] is True and metrics["reached"] >= 27 and metrics["runs"] == 30
    assert metrics["noise"]["source"] == "assumed"
    assert body["robot_spec"]["source"] == "placeholder"
    # The median run starts where the rover was placed: the start, within the placement error.
    assert body["mission_trace"][0] == pytest.approx(START, abs=0.03)

    conn = db.connect()
    assert db.get(conn, "policies", body["policy_id"])["mission_id"] == crossing["mission_id"]
    runs = conn.execute("SELECT kind, reached FROM runs WHERE mission_id = ?",
                        (crossing["mission_id"],)).fetchall()
    assert [(r["kind"], r["reached"]) for r in runs] == [("sim", 1)]
    conn.close()

    artifact = client.post("/optimize", json={"policy_id": body["policy_id"]}).json()
    assert client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                        "kind": "rover"}).status_code == 400
    deployed = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "kind": "sim"}).json()
    assert deployed["status"] == "running" and deployed["mission_id"] == crossing["mission_id"]
    assert deployed["pose_trace"][0] == pytest.approx(START, abs=0.03)
    assert math.dist(deployed["pose_trace"][-1], GOAL) <= 0.10

    # Someone leaves a box in the middle of the room, on the first mission's route.
    rescan = upload(client, with_box(-0.3, 0.3, -0.3, 0.3, 0.5))
    synced = client.post("/sync", json={"twin_id": twin_id,
                                        "new_scan_id": rescan,
                                        "same_marker_confirmed": True}).json()
    report = {m["mission_id"]: m for m in synced["stale_missions"]}
    assert report[crossing["mission_id"]]["stale"] is True
    assert report[aside["mission_id"]]["stale"] is False
    assert client.get(f"/missions/{crossing['mission_id']}").json()["status"] == "stale"

    refused = client.post("/deploy", json={"artifact_id": artifact["artifact_id"], "kind": "sim"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "mission_stale"
    retrain = client.post("/train", json={"twin_id": twin_id, "mission_id": crossing["mission_id"]})
    assert retrain.status_code == 409


def test_a_rescan_that_only_removes_the_table_marks_nothing_stale(client):
    _, twin = twin_for(client)
    beside_table = mission(client, twin["twin_id"], start=[0.3, -1.9], goal=[1.9, -0.2])
    assert beside_table.status_code == 200, beside_table.text

    rescan = upload(client, scan_frame(table=False))
    unregistered = client.post("/sync", json={"twin_id": twin["twin_id"], "new_scan_id": rescan})
    assert unregistered.status_code == 409
    assert client.get(f"/missions/{beside_table.json()['mission_id']}").json()["status"] == "ready"
    synced = client.post("/sync", json={"twin_id": twin["twin_id"],
                                        "new_scan_id": rescan,
                                        "same_marker_confirmed": True}).json()
    assert [m["stale"] for m in synced["stale_missions"]] == [False]
    assert client.get(f"/missions/{beside_table.json()['mission_id']}").json()["status"] == "ready"


def test_the_twin_grid_follows_the_scan_or_the_request(client):
    scan_id = upload(client, scan_frame())
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    objects = client.post("/segment", json={"mesh_id": mesh["mesh_id"]}).json()
    body = {"mesh_id": mesh["mesh_id"], "objects_id": objects["objects_id"], "environment": "home"}

    # The synthetic room is sampled every ~4 cm: too sparse for 5 cm cells.
    auto = client.post("/generate-twin", json=body).json()
    assert auto["resolution"]["chosen"] == "auto" and auto["resolution"]["cell_m"] == 0.1

    fine = client.post("/generate-twin", json={**body, "cell_m": 0.05}).json()
    assert fine["resolution"] == {"cell_m": 0.05, "chosen": "requested"}
    navmesh = client.get(f"/twins/{fine['twin_id']}/navmesh").json()
    assert navmesh["cell"] == 0.05 and navmesh["resolution"] == fine["resolution"]

    assert client.post("/generate-twin", json={**body, "cell_m": 0.2}).status_code == 400


def test_the_rover_spec_is_served_with_its_source(client):
    spec = client.get("/robot-specs/rover").json()
    assert spec["source"] == "placeholder" and spec["noise"]["source"] == "assumed"
    assert spec["footprint_radius_m"] > 0 and spec["v_max"] <= 0.5
    assert client.get("/robot-specs/no-such-robot").status_code == 404
    assert client.get("/robot-specs/Bad Name").status_code == 400


def test_a_rover_wider_than_the_twin_was_built_for_is_refused(client, monkeypatch):
    import dataclasses

    from orchestrator import service
    from robot.spec import load_spec

    _, twin = twin_for(client)  # home profile: planned for a 0.20 m radius
    planned = mission(client, twin["twin_id"], start=START, goal=GOAL).json()
    wide = dataclasses.replace(load_spec("rover"), footprint_radius_m=0.3)
    monkeypatch.setattr(service.specifications, "load_spec", lambda name="rover": wide)

    refused = client.post("/train", json={"twin_id": twin["twin_id"],
                                          "mission_id": planned["mission_id"]})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "footprint_too_large"


def test_a_policy_from_before_the_rover_model_is_not_deployed(client):
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=START, goal=GOAL).json()
    trained = client.post("/train", json={"twin_id": twin["twin_id"],
                                          "mission_id": planned["mission_id"]}).json()
    artifact = client.post("/optimize", json={"policy_id": trained["policy_id"]}).json()

    conn = db.connect()
    bundle = db.get(conn, "artifacts", artifact["artifact_id"])["artifact_url"]
    conn.close()
    manifest_path = os.path.join(os.path.dirname(bundle), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    del manifest["observation"]  # what every artifact exported before Phase 3 looks like
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

    refused = client.post("/deploy", json={"artifact_id": artifact["artifact_id"], "kind": "sim"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "policy_outdated"


def test_training_twice_on_one_twin_keeps_both_checkpoints(client):
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=START, goal=GOAL).json()
    body = {"twin_id": twin["twin_id"], "mission_id": planned["mission_id"]}
    first = client.post("/train", json=body).json()["policy_id"]
    second = client.post("/train", json=body).json()["policy_id"]

    conn = db.connect()
    paths = {db.get(conn, "policies", pid)["finetuned_ckpt_url"] for pid in (first, second)}
    conn.close()
    assert len(paths) == 2 and all(os.path.exists(p) for p in paths)
