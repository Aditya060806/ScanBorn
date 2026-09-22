"""SDK driven against the real orchestrator app over an in-process ASGI transport."""
import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.service import app
from tests.test_orchestrator import frame_bytes, scan_frame
from scanborn import ScanBorn, ScanBornError
from scanborn.client import decode_cells


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("AI_HUB_API_TOKEN", raising=False)
    with ScanBorn(http=TestClient(app)) as client:
        yield client


def test_run_pipeline_end_to_end(sdk):
    result = sdk.run_pipeline([frame_bytes(scan_frame())], text="go to the table",
                              meta={"device": "test"})
    assert result["deployment_id"]
    assert result["status"] == "running"
    assert result["sim_success_rate"] >= 0.6
    assert {o["label"] for o in result["objects"]} >= {"floor", "table"}


def test_each_verb_is_callable_on_its_own(sdk):
    scan = sdk.capture([frame_bytes(scan_frame())], meta={"device": "test"})
    assert scan["frame_count"] == 1

    mesh = sdk.reconstruct(scan["scan_id"])
    objects = sdk.segment(mesh["mesh_id"])
    twin = sdk.generate_twin(mesh["mesh_id"], objects["objects_id"])
    graph = sdk.plan(twin["twin_id"], "go to the table then wait")
    policy = sdk.train(twin["twin_id"], graph["task_graph_id"])
    artifact = sdk.optimize(policy["policy_id"])
    deployment = sdk.deploy(artifact["artifact_id"])

    assert sdk.status(deployment["job_id"])["stage"] == "deploy"
    assert sdk.sync(twin["twin_id"], scan["scan_id"], same_marker_confirmed=True)["diff_summary"]


def test_provider_settings_are_readable_and_local_by_default(sdk):
    report = sdk.providers()
    assert {w: e["selected"] for w, e in report["workloads"].items()} == {
        "planner": "local", "profiler": "local", "export": "local"}
    assert all(entry["ready"] for entry in report["workloads"].values())


def test_a_plan_needing_clarification_is_a_scanborn_error(sdk):
    scan = sdk.capture([frame_bytes(scan_frame())], meta={"device": "test"})
    mesh = sdk.reconstruct(scan["scan_id"])
    twin = sdk.generate_twin(mesh["mesh_id"], sdk.segment(mesh["mesh_id"])["objects_id"])
    with pytest.raises(ScanBornError) as excinfo:
        sdk.plan(twin["twin_id"], "go to the kitchen")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "clarification_needed"
    first = sdk.plan(twin["twin_id"], "go to the table", request_id="sdk-plan-1")
    assert sdk.plan(twin["twin_id"], "go to the table", request_id="sdk-plan-1") == first
    with pytest.raises(ScanBornError) as missing:
        sdk.cancel_provider_request("nothing-running")
    assert missing.value.status_code == 404


def test_capture_without_frames_is_rejected_client_side(sdk):
    with pytest.raises(ValueError, match="at least one frame"):
        sdk.capture([])


def test_an_import_declares_units_and_reports_its_registration(sdk, tmp_path):
    from tests.fixtures import scenes
    path = scenes.write_mesh_ply(str(tmp_path / "room.ply"))
    imported = sdk.import_scan(path, units="cm", up_axis="y")
    assert imported["import"]["units_source"] == "declared"
    assert imported["registration"]["physically_registered"] is False
    assert sdk.scan_status(imported["scan_id"])["registration"]["state"] == "unknown"
    with pytest.raises(ScanBornError) as excinfo:
        sdk.import_scan(path, units="parsecs")
    assert excinfo.value.status_code == 400


def test_server_errors_surface_as_scanborn_errors(sdk):
    with pytest.raises(ScanBornError) as excinfo:
        sdk.reconstruct("no-such-scan")
    assert excinfo.value.status_code == 404


def test_missions_through_the_sdk(sdk):
    scan = sdk.capture([frame_bytes(scan_frame())], meta={"device": "test"})
    mesh = sdk.reconstruct(scan["scan_id"])
    objects = sdk.segment(mesh["mesh_id"])
    twin = sdk.generate_twin(mesh["mesh_id"], objects["objects_id"], environment="home")
    assert twin["profile"]["environment"] == "home"

    navmesh = sdk.navmesh(twin["twin_id"])
    assert decode_cells(navmesh).shape == (navmesh["height"], navmesh["width"])
    assert decode_cells(navmesh, "band_b64").any()

    planned = sdk.mission(twin["twin_id"], goal=(0.6, 0.6), start=(-1.2, -1.2))
    assert sdk.get_mission(planned["mission_id"])["waypoints"] == planned["waypoints"]
    policy = sdk.train(twin["twin_id"], mission_id=planned["mission_id"])
    assert policy["mission_metrics"]["passed"] is True

    with pytest.raises(ScanBornError) as excinfo:
        sdk.mission(twin["twin_id"], goal=(0.85, -0.85), start=(-1.2, -1.2))
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "goal_blocked"

    spec = sdk.robot_spec()
    assert spec["source"] == "placeholder" and spec["noise"]["source"] == "assumed"


def test_a_fake_rover_through_the_sdk(sdk):
    from orchestrator import service

    robot = sdk.register_robot("fake_rover", time_scale=10.0)
    try:
        assert robot["fake"] is True and robot["status"]["fake"] is True
        assert sdk.robot_status(robot["robot_id"])["state"] == "idle"
        with pytest.raises(ScanBornError):
            sdk.calibrate(robot["robot_id"], "spin")
        assert sdk.estop(robot["robot_id"])["state"] == "estop"
        assert sdk.robot_status(robot["robot_id"])["reason"] == "dashboard"
        assert sdk.reset_robot(robot["robot_id"])["state"] == "idle"
        for call in (sdk.deployment, sdk.stop_deployment):
            with pytest.raises(ScanBornError) as excinfo:
                call("no-such-deployment")
            assert excinfo.value.status_code == 404
    finally:
        service.FAKE_ROVERS.pop(robot["robot_id"]).close()


def test_stage_request_recovers_original_sdk_response(sdk):
    scan = sdk.capture([frame_bytes(scan_frame())], meta={"device": "test"})
    result = sdk._http.post("/reconstruct", json={
        "scan_id": scan["scan_id"], "request_id": "sdk-lost"}).json()
    assert sdk.stage_request("reconstruct", "sdk-lost")["response"] == result
    with pytest.raises(ScanBornError) as exc:
        sdk.stage_request("deploy", "sdk-lost")
    assert exc.value.status_code == 404
