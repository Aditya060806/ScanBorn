"""Rover deployments through the orchestrator, end to end, against in-process fake rovers."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, service
from orchestrator.service import app
from tests.test_missions import GOAL, START, mission, twin_for


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    monkeypatch.setenv("SCANBORN_TELEMETRY_PORT", "0")
    for key in ("SARVAM_API_KEY", "AI_HUB_API_TOKEN", "SCANBORN_PLANNER", "SCANBORN_PROFILER"):
        monkeypatch.delenv(key, raising=False)
    yield TestClient(app)
    for rover in list(service.FAKE_ROVERS.values()):
        rover.close()
    service.FAKE_ROVERS.clear()


def trained_artifact(client) -> tuple:
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    trained = client.post("/train", json={"twin_id": twin["twin_id"],
                                          "mission_id": planned["mission_id"]})
    assert trained.status_code == 200, trained.text
    artifact = client.post("/optimize", json={"policy_id": trained.json()["policy_id"]}).json()
    return twin, planned, trained.json(), artifact


def fake_rover(client, time_scale: float = 10.0) -> dict:
    registered = client.post("/robots", json={"kind": "fake_rover", "time_scale": time_scale})
    assert registered.status_code == 200, registered.text
    return registered.json()


def wait_until_ended(client, deployment_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/deployments/{deployment_id}").json()
        if not state["live"] and state["result"] is not None:
            return state
        time.sleep(0.05)
    raise AssertionError(f"deployment never ended: {state}")


def test_a_fake_rover_drives_a_trained_mission_live_and_the_result_is_recorded(client):
    _, planned, _, artifact = trained_artifact(client)
    robot = fake_rover(client)
    assert robot["fake"] is True and robot["status"]["state"] == "idle"

    deployed = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "robot_id": robot["robot_id"],
                                            "request_id": "recover-lost-response"})
    assert deployed.status_code == 200, deployed.text
    body = deployed.json()
    recovered = client.get("/deploy/requests/recover-lost-response")
    assert recovered.status_code == 200
    assert recovered.json() == {"request_id": "recover-lost-response", "status": "done",
                                "response": body}
    assert client.get("/deploy/requests/never-submitted").status_code == 404
    assert client.get("/deployments").json()["total"] == 1  # lookup never starts another drive
    assert body["status"] == "running" and body["fake"] is True
    assert body["live"] == f"/ws/deployments/{body['deployment_id']}"

    messages: list = []

    def listen():
        with client.websocket_connect(body["live"]) as ws:
            while True:
                message = ws.receive_json()
                messages.append(message)
                if message["type"] == "finished" or (message["type"] == "snapshot"
                                                     and message["finished"]):
                    return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    listener.join(20.0)
    assert not listener.is_alive(), messages[-3:]
    assert messages[0]["type"] == "snapshot"

    ended = wait_until_ended(client, body["deployment_id"])
    assert ended["robot_id"] == robot["robot_id"]
    assert ended["artifact_id"] == artifact["artifact_id"]
    assert ended["status"] == "done"
    result = ended["result"]
    assert result["kind"] == "fake_rover" and result["reached"] is True and result["fake"] is True
    assert result["frames"] >= 5
    assert result["reported_miss_m"] <= 0.05          # it stopped where it believed the goal was
    assert result["deviation_from_sim_m"] < 0.15      # and drove close to the simulated route
    assert "true_miss_m" in result                    # known only because the rover is fake
    assert len(ended["trace"]) >= 5

    conn = db.connect()
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM runs WHERE mission_id = ?",
                                             (planned["mission_id"],))]
    conn.close()
    assert "fake_rover" in kinds and "sim" in kinds


def test_a_rover_deploy_needs_a_registered_robot_and_a_mission(client):
    _, twin = twin_for(client)
    graph = client.post("/plan", json={"twin_id": twin["twin_id"],
                                       "text": "go to the table"}).json()
    trained = client.post("/train", json={"twin_id": twin["twin_id"],
                                          "task_graph_id": graph["task_graph_id"]}).json()
    artifact = client.post("/optimize", json={"policy_id": trained["policy_id"]}).json()

    no_robot = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "kind": "rover"})
    assert no_robot.status_code == 400 and no_robot.json()["detail"]["code"] == "robot_required"

    robot = fake_rover(client)
    no_mission = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                              "robot_id": robot["robot_id"]})
    assert no_mission.status_code == 409
    assert no_mission.json()["detail"]["code"] == "mission_required"


def test_robot_kinds_without_an_adapter_are_refused(client):
    _, _, _, artifact = trained_artifact(client)
    by_kind = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                           "kind": "unoq"})
    assert by_kind.status_code == 400 and "unknown robot kind" in by_kind.json()["detail"]

    # A robot registered before the UNO Q adapter was removed is still in an older database.
    db.insert(db.connect(), "robots", id="old-unoq", kind="unoq", config_json="{}")
    registered = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                              "robot_id": "old-unoq"})
    assert registered.status_code == 400 and "no adapter" in registered.json()["detail"]


def test_an_estop_from_the_orchestrator_latches_mid_drive(client):
    _, _, _, artifact = trained_artifact(client)
    robot = fake_rover(client, time_scale=1.0)
    deployment = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                              "robot_id": robot["robot_id"]}).json()
    time.sleep(0.5)
    stopped = client.post(f"/robots/{robot['robot_id']}/estop").json()
    assert stopped["state"] == "estop" and stopped["deployment_id"] == deployment["deployment_id"]

    ended = wait_until_ended(client, deployment["deployment_id"])
    assert ended["status"] == "estop" and ended["result"]["reached"] is False
    status = client.get(f"/robots/{robot['robot_id']}/status").json()
    assert status["state"] == "estop" and status["reason"] == "dashboard"

    again = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                         "robot_id": robot["robot_id"]})
    # The latched rover refuses a new mission until reset, and that failed start records no run.
    assert again.status_code == 409 and again.json()["detail"]["code"] == "rover_refused"
    assert client.post(f"/robots/{robot['robot_id']}/reset").json()["state"] == "idle"
    conn = db.connect()
    rover_runs = conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'fake_rover'").fetchone()[0]
    conn.close()
    assert rover_runs == 1


def test_an_operator_stop_ends_the_drive_and_keeps_the_mission_on_the_rover(client):
    _, _, _, artifact = trained_artifact(client)
    robot = fake_rover(client, time_scale=1.0)
    deployment = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                              "robot_id": robot["robot_id"]}).json()
    time.sleep(0.5)
    stop = client.post(f"/deployments/{deployment['deployment_id']}/stop")
    assert stop.status_code == 200 and stop.json()["status"] == "stopped"
    ended = wait_until_ended(client, deployment["deployment_id"])
    assert ended["status"] == "stopped"
    assert client.get(f"/robots/{robot['robot_id']}/status").json()["state"] == "ready"
    again = client.post(f"/deployments/{deployment['deployment_id']}/stop")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_live"


def test_robots_that_cannot_be_reached_or_do_not_fit_are_refused(client, monkeypatch):
    unreachable = client.post("/robots", json={"kind": "rover", "address": "http://127.0.0.1:9"})
    assert unreachable.status_code == 502
    assert unreachable.json()["detail"]["code"] == "rover_unreachable"
    assert client.post("/robots", json={"kind": "rover"}).status_code == 422
    assert client.post("/robots", json={"kind": "tank", "address": "x"}).status_code == 422

    import dataclasses

    from robot.spec import load_spec

    _, _, _, artifact = trained_artifact(client)
    robot = fake_rover(client)
    wide = dataclasses.replace(load_spec("rover"), footprint_radius_m=0.3)
    monkeypatch.setattr(service.specifications, "load_spec", lambda name="rover": wide)
    refused = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                           "robot_id": robot["robot_id"]})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "footprint_too_large"


def test_calibration_goes_through_to_the_rover(client):
    robot = fake_rover(client)
    result = client.post(f"/robots/{robot['robot_id']}/calibrate", json={"mode": "spin"}).json()
    assert "detail" in result  # obsolete blocking endpoint is refused with migration guidance
    refused = client.post(f"/robots/{robot['robot_id']}/calibrate", json={"mode": "hop"})
    assert refused.status_code == 410 and refused.json()["detail"]["code"] == "rover_refused"
    assert client.get("/deployments/nope").status_code == 404


def test_changed_rover_geometry_is_refused_before_driving(client):
    _, _, _, artifact = trained_artifact(client)
    robot = fake_rover(client)
    fake = service.FAKE_ROVERS[robot["robot_id"]]
    code, _ = fake.configure({"wheelbase_m": fake.spec.wheelbase_m * 1.1})
    assert code == 200
    response = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "robot_id": robot["robot_id"]})
    assert response.status_code == 409
    assert "geometry changed" in response.text
    assert fake.state != "running"


def test_recording_failure_is_visible_in_database_and_job(client, monkeypatch):
    _, _, _, artifact = trained_artifact(client)
    robot = fake_rover(client)

    def broken_record(*args, **kwargs):
        raise OSError("injected result recording failure")

    monkeypatch.setattr(service, "_record_run", broken_record)
    response = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "robot_id": robot["robot_id"]})
    assert response.status_code == 200
    body = response.json()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/deployments/{body['deployment_id']}").json()
        if result["recording_error"]:
            break
        time.sleep(0.05)
    assert result["status"] == "recording_failed", result
    assert "injected" in result["recording_error"]
    job = client.get(f"/status/{body['job_id']}").json()
    assert job["status"] == "failed" and "recording" in job["detail"]["error"]
