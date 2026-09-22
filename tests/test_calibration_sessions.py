"""Calibration remains stoppable and simulated measurements stay isolated."""
import time
from pathlib import Path

import pytest

from robot.spec import SPEC_DIR
from tests.test_rover_deploy import client as calibration_client, fake_rover  # noqa: F401
from tests.test_rover_protocol import Heartbeat, fast as fast_rover  # noqa: F401


def wait_result(get, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get().json()
        if result.get("status") != "running":
            return result
        time.sleep(0.05)
    raise AssertionError(f"calibration did not terminate: {result}")


@pytest.mark.parametrize("action, expected", [("stop", "stopped"), ("estop", "estop")])
def test_calibration_stop_controls_remain_responsive(fast_rover, action, expected):  # noqa: F811
    rover, http = fast_rover
    assert http.post("/calibrations", json={"mode": "spin", "session": "s"}).status_code == 202
    beat = Heartbeat(rover, "s")
    try:
        assert http.get("/status").json()["state"] == "calibrating"
        assert http.post(f"/{action}").status_code == 200
        result = wait_result(lambda: http.get("/calibrations/s"))
        assert result["status"] == expected
        assert result["source"] == "simulated"
    finally:
        beat.close()


def test_calibration_heartbeat_expiry_latches_estop(fast_rover):  # noqa: F811
    _, http = fast_rover
    http.post("/calibrations", json={"mode": "spin", "session": "s"})
    result = wait_result(lambda: http.get("/calibrations/s"))
    assert result["status"] == "estop"
    assert result["reason"] == "heartbeat_lost"


def test_simulated_apply_readback_and_subsequent_snapshot(calibration_client):  # noqa: F811
    client = calibration_client
    physical_path = Path(SPEC_DIR) / "rover.yaml"
    physical_before = physical_path.read_bytes()
    registered = fake_rover(client)
    root = f"/robots/{registered['robot_id']}/calibrations"
    response = client.post(root, json={"mode": "straight"})
    assert response.status_code == 200, response.text
    path = root + "/" + response.json()["id"]
    result = wait_result(lambda: client.get(path))
    assert result["status"] == "done", result
    assert client.post(path + "/measurement", json={"measured": -1}).status_code == 400
    measured = client.post(path + "/measurement", json={"measured": 1.01})
    assert measured.status_code == 200, measured.text
    applied = client.post(path + "/apply")
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["applied"] and result["apply_state"] == "verified"
    assert result["source"] == "simulated" and result["noise_unchanged"]
    assert physical_path.read_bytes() == physical_before
    assert "simulated-specs" in result["local_spec"]
    assert client.post(path + "/apply").json()["read_back"] == result["read_back"]
    next_run = client.post(root, json={"mode": "wheel_step", "duration_s": 0.1}).json()
    assert next_run["robot_spec"]["wheel_radius_m"] == result["read_back"]["wheel_radius_m"]
    wait_result(lambda: client.get(root + "/" + next_run["id"]))
    history = client.get(root + "?limit=1").json()
    assert history["total"] == 2 and history["next_offset"] == 1
    assert history["items"][0]["id"] == next_run["id"]
    previous = client.get(root + "?limit=1&offset=1").json()["items"][0]
    assert previous["applied"] and previous["source"] == "simulated"


def test_mismatched_readback_is_reported_and_previous_settings_restored(
        calibration_client, monkeypatch):  # noqa: F811
    from robot.adapters.rover import RoverRobot

    client = calibration_client
    registered = fake_rover(client)
    root = f"/robots/{registered['robot_id']}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    assert client.post(path + "/measurement", json={"measured": 1.02}).status_code == 200
    original = RoverRobot._call
    reads = 0

    def broken_readback(self, method, route, *args, **kwargs):
        nonlocal reads
        answer = original(self, method, route, *args, **kwargs)
        if method == "GET" and route == "/config":
            reads += 1
            if reads == 2:
                answer["settings"]["wheel_radius_m"] *= 1.1
        return answer

    monkeypatch.setattr(RoverRobot, "_call", broken_readback)
    response = client.post(path + "/apply")
    assert response.status_code == 409
    result = client.get(path).json()
    assert result["apply_state"] == "failed" and not result["applied"]
    assert result["rollback_verified"] is True
    assert "local_spec" not in result


def test_calibrated_fake_spec_reaches_training_export_and_drive(calibration_client):  # noqa: F811
    from tests.test_missions import twin_for, mission, START, GOAL
    from tests.test_rover_deploy import wait_until_ended

    client = calibration_client
    registered = fake_rover(client)
    robot_id = registered["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    assert started["spec_id"] == "rover"
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    assert client.post(path + "/measurement", json={"measured": 1.02}).status_code == 200
    applied = client.post(path + "/apply").json()
    assert applied["applied"], applied
    _, twin = twin_for(client)
    manifest = client.get(f"/twins/{twin['twin_id']}/visualization").json()
    bound = client.post("/generate-twin", json={"mesh_id": manifest["mesh_id"],
                        "environment": "home", "robot_id": robot_id}).json()
    view = client.get(f"/twins/{bound['twin_id']}/visualization").json()
    assert view["robot_spec_binding"]["revision"] == applied["applied_revision"]
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    trained = client.post("/train", json={"twin_id": twin["twin_id"],
                                          "mission_id": planned["mission_id"],
                                          "robot_id": robot_id})
    assert trained.status_code == 200, trained.text
    exported = client.post("/optimize", json={"policy_id": trained.json()["policy_id"]})
    assert exported.status_code == 200, exported.text
    artifact = exported.json()
    assert artifact["validation"]["spec_revision"] == applied["applied_revision"]
    assert artifact["validation"]["robot_spec"]["source"] == "simulated"
    other = fake_rover(client)["robot_id"]
    assert client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                        "robot_id": other}).status_code == 409
    deployed = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                            "robot_id": robot_id})
    assert deployed.status_code == 200, deployed.text
    assert wait_until_ended(client, deployed.json()["deployment_id"])["status"] == "done"
    client.post(f"/robots/{robot_id}/reset")
    second = client.post(root, json={"mode": "straight"}).json()
    second_path = root + "/" + second["id"]
    wait_result(lambda: client.get(second_path))
    client.post(second_path + "/measurement", json={"measured": 1.05})
    assert client.post(second_path + "/apply").status_code == 200
    assert client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                        "robot_id": robot_id}).status_code == 409
    assert client.get(f"/twins/{bound['twin_id']}/visualization").json() == view


def test_physical_spec_alias_is_not_its_display_name(calibration_client, monkeypatch):  # noqa: F811
    """Mock the physical transport classification; this does not measure physical hardware."""
    import json
    from orchestrator import db
    from robot.adapters.rover import RoverRobot

    client = calibration_client
    registered = fake_rover(client)
    robot_id = registered["robot_id"]
    with db.connect() as conn:
        row = db.get(conn, "robots", robot_id)
        config = json.loads(row["config_json"])
        config["fake"] = False
        db.update(conn, "robots", robot_id, config_json=json.dumps(config))
    original = RoverRobot._call

    def physical_status(self, method, route, *args, **kwargs):
        result = original(self, method, route, *args, **kwargs)
        if route == "/status":
            result["fake"] = False
        if route.startswith("/calibrations"):
            result["source"] = "measured"
        return result

    monkeypatch.setattr(RoverRobot, "_call", physical_status)
    default_path = Path(SPEC_DIR) / "rover.yaml"
    before = default_path.read_bytes()
    root = f"/robots/{robot_id}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    assert started["spec_id"] == "rover"
    assert started["robot_spec"]["name"] == "reference-rover"
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    assert client.post(path + "/measurement", json={"measured": 1.01}).status_code == 200
    response = client.post(path + "/apply")
    assert response.status_code == 200, response.text
    assert response.json()["applied"]
    assert default_path.read_bytes() == before
    # The new accepted spec must also be usable by the next session.
    second = client.post(root, json={"mode": "wheel_step", "duration_s": 0.1})
    assert second.status_code == 200, second.text
    assert second.json()["robot_spec"]["source"] == "calibrated_geometry"
    wait_result(lambda: client.get(root + "/" + second.json()["id"]))


def test_spec_write_failure_keeps_previous_accepted_revision(
        calibration_client, monkeypatch):  # noqa: F811
    import json
    from orchestrator import calibrations, db

    client = calibration_client
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    client.post(path + "/measurement", json={"measured": 1.02})

    def disk_full(*args, **kwargs):
        raise OSError("test disk full")

    monkeypatch.setattr(calibrations, "atomic_write", disk_full)
    response = client.post(path + "/apply")
    assert response.status_code == 409
    result = client.get(path).json()
    assert not result["applied"] and result["rollback_verified"]
    conn = db.connect()
    try:
        config = json.loads(db.get(conn, "robots", robot_id)["config_json"])
        assert "accepted_spec" not in config
    finally:
        conn.close()


@pytest.mark.parametrize("point", ["before_config", "before_publish", "after_file"])
def test_interrupted_apply_reconciles_without_resending_config(
        calibration_client, monkeypatch, point):  # noqa: F811
    from orchestrator import calibrations
    from robot.adapters.rover import RoverRobot

    class InterruptedProcess(BaseException):
        pass

    client = calibration_client
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    client.post(path + "/measurement", json={"measured": 1.03})
    write = calibrations.atomic_write

    def interrupt(*args, **kwargs):
        if point == "after_file":
            write(*args, **kwargs)
        raise InterruptedProcess()

    with monkeypatch.context() as patch:
        if point == "before_config":
            patch.setattr(RoverRobot, "configure", interrupt)
        elif point == "before_publish":
            patch.setattr(calibrations, "_publish", interrupt)
        else:
            patch.setattr(calibrations, "atomic_write", interrupt)
        with pytest.raises(InterruptedProcess):
            calibrations.apply(robot_id, started["id"])
    assert client.get(path).json()["apply_state"] == "verifying"
    assert client.post(root, json={"mode": "wheel_step"}).status_code == 409

    def forbidden(*args, **kwargs):
        raise AssertionError("reconciliation must not change rover configuration")

    monkeypatch.setattr(RoverRobot, "configure", forbidden)
    recovered = client.post(path + "/reconcile")
    assert recovered.status_code == 200, recovered.text
    result = recovered.json()
    assert result["applied"] == (point != "before_config")
    assert result["apply_state"] == ("not_applied" if point == "before_config" else "verified")
    assert client.post(path + "/reconcile").json()["apply_state"] == result["apply_state"]


def test_legacy_apply_is_adopted_only_after_live_readback(calibration_client):  # noqa: F811
    import json
    from orchestrator import db

    client = calibration_client
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    started = client.post(root, json={"mode": "straight"}).json()
    path = root + "/" + started["id"]
    wait_result(lambda: client.get(path))
    client.post(path + "/measurement", json={"measured": 1.03})
    applied = client.post(path + "/apply").json()
    conn = db.connect()
    try:
        config = json.loads(db.get(conn, "robots", robot_id)["config_json"])
        config.pop("accepted_spec")
        db.update(conn, "robots", robot_id, config_json=json.dumps(config))
        for key in ("spec_id", "spec_revision", "applied_revision"):
            applied.pop(key, None)
        db.update(conn, "calibration_sessions", started["id"], result_json=json.dumps(applied))
    finally:
        conn.close()
    reconciled = client.post(path + "/reconcile")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["applied_revision"]
    # Subsequent experiments now use the adopted snapshot, not the default YAML.
    next_session = client.post(root, json={"mode": "wheel_step", "duration_s": 0.1})
    assert next_session.json()["robot_spec"]["source"] == "simulated"
    wait_result(lambda: client.get(root + "/" + next_session.json()["id"]))
