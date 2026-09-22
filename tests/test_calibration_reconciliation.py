"""Calibration reconciliation edge cases (execution plan E0): a deterministic way forward.

Every case here ends with a clear answer from live read-back, and none resends configuration or
motion during reconciliation. An older proposal is never applied over a newer state, and
uncertainty is resolved explicitly (recorded as superseded, adopted or not applied), never cleared
silently. These run against the in-process fake rover: software evidence, not physical.
"""
import json
import sqlite3
from contextlib import closing

import pytest

from orchestrator import db
from tests.test_calibration_sessions import wait_result
from tests.test_rover_deploy import client, fake_rover  # noqa: F401


def experiment(client, root, measured):  # noqa: F811
    started = client.post(root, json={"mode": "straight"})
    assert started.status_code == 200, started.text
    path = root + "/" + started.json()["id"]
    assert wait_result(lambda: client.get(path))["status"] == "done"
    assert client.post(path + "/measurement", json={"measured": measured}).status_code == 200
    return started.json()["id"], path


def make_legacy(robot_id, *session_ids):
    """Strip what current code records, as a database from before accepted specs would look."""
    with closing(db.connect()) as conn:
        db.init_db(conn)
        config = json.loads(db.get(conn, "robots", robot_id)["config_json"])
        config.pop("accepted_spec", None)
        db.update(conn, "robots", robot_id, config_json=json.dumps(config))
        for identifier in session_ids:
            result = json.loads(db.get(conn, "calibration_sessions", identifier)["result_json"])
            for key in ("spec_id", "spec_revision", "applied_revision"):
                result.pop(key, None)
            db.update(conn, "calibration_sessions", identifier, result_json=json.dumps(result))


def accepted(robot_id):
    """The robot's accepted specification record, or None."""
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return json.loads(db.get(conn, "robots", robot_id)["config_json"]).get("accepted_spec")


def forbid_configuration(monkeypatch):
    from robot.adapters.rover import RoverRobot

    def forbidden(*args, **kwargs):
        raise AssertionError("reconciliation must not change rover configuration")

    monkeypatch.setattr(RoverRobot, "configure", forbidden)


def test_two_legacy_applies_reconcile_newest_first(client, monkeypatch):  # noqa: F811
    """Each blocked the other: the older by 'a newer calibration was applied', the newer by the
    older's uncertainty. The newest is verified from read-back and the older is superseded."""
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    first, first_path = experiment(client, root, 1.02)
    assert client.post(first_path + "/apply").status_code == 200
    second, second_path = experiment(client, root, 1.04)
    assert client.post(second_path + "/apply").status_code == 200
    make_legacy(robot_id, first, second)

    blocked = client.post(root, json={"mode": "straight"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["calibration_id"] == second  # newest first
    assert client.post(first_path + "/reconcile").status_code == 409  # a newer one exists

    with monkeypatch.context() as scoped:
        forbid_configuration(scoped)
        reconciled = client.post(second_path + "/reconcile")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["apply_state"] == "verified"
    assert accepted(robot_id)["calibration_id"] == second
    older = client.get(first_path).json()
    assert older["superseded_by"] == second and older["apply_state"] == "superseded"
    unblocked = client.post(root, json={"mode": "wheel_step", "duration_s": 0.1})
    assert unblocked.status_code == 200, unblocked.text
    wait_result(lambda: client.get(root + "/" + unblocked.json()["id"]))


def test_an_older_proposal_is_never_applied_over_a_newer_one(client):  # noqa: F811
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    _, old_path = experiment(client, root, 1.02)
    _, new_path = experiment(client, root, 1.05)
    newer = client.post(new_path + "/apply").json()
    refused = client.post(old_path + "/apply")
    assert refused.status_code == 409
    live = client.get(f"/robots/{robot_id}/status")
    assert live.status_code == 200
    assert accepted(robot_id)["revision"] == newer["applied_revision"]
    assert client.post(old_path + "/reconcile").status_code == 409


def test_a_newer_session_that_never_reached_the_rover_adopts_the_live_older_one(
        client, monkeypatch):  # noqa: F811
    from orchestrator import calibrations
    from robot.adapters.rover import RoverRobot

    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    first, first_path = experiment(client, root, 1.03)
    assert client.post(first_path + "/apply").status_code == 200
    second, second_path = experiment(client, root, 1.06)

    class Interrupted(BaseException):
        pass

    def interrupt(*args, **kwargs):
        raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(RoverRobot, "configure", interrupt)
        with pytest.raises(Interrupted):
            calibrations.apply(robot_id, second)
    make_legacy(robot_id, first, second)

    forbid_configuration(monkeypatch)
    reconciled = client.post(second_path + "/reconcile").json()
    assert reconciled["apply_state"] == "not_applied"
    assert reconciled["adopted_calibration_id"] == first
    assert accepted(robot_id)["calibration_id"] == first
    assert client.get(first_path).json()["apply_state"] == "verified"


def test_retrying_apply_after_publication_changes_nothing(client, monkeypatch):  # noqa: F811
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    _, path = experiment(client, root, 1.02)
    first = client.post(path + "/apply").json()
    forbid_configuration(monkeypatch)
    again = client.post(path + "/apply")
    assert again.status_code == 200 and again.json() == first


def test_a_failed_recording_leaves_an_uncertain_session_that_reconciles(
        client, monkeypatch):  # noqa: F811
    """Publication and the failure record both fail: the rover was rolled back, the database says
    'verifying', and reconciliation settles it from read-back without sending configuration."""
    from orchestrator import calibrations

    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    identifier, path = experiment(client, root, 1.04)
    save = calibrations._save
    calls = {"n": 0}

    def flaky_save(session, result):
        calls["n"] += 1
        if calls["n"] > 1:  # the "verifying" record succeeds; the failure record does not
            raise sqlite3.OperationalError("disk I/O error")
        save(session, result)

    def failing_publish(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(calibrations, "_save", flaky_save)
        patch.setattr(calibrations, "_publish", failing_publish)
        with pytest.raises(sqlite3.OperationalError):
            calibrations.apply(robot_id, identifier)
    assert client.get(path).json()["apply_state"] == "verifying"
    assert client.post(root, json={"mode": "straight"}).status_code == 409

    forbid_configuration(monkeypatch)
    reconciled = client.post(path + "/reconcile").json()
    assert reconciled["apply_state"] == "not_applied" and reconciled["applied"] is False
    assert accepted(robot_id) is None


def test_a_running_session_without_a_monitor_does_not_block_forever(client):  # noqa: F811
    robot_id = fake_rover(client)["robot_id"]
    with closing(db.connect()) as conn:
        db.init_db(conn)
        db.insert(conn, "calibration_sessions", id="orphan", robot_id=robot_id,
                  status="running", provenance="simulated",
                  result_json=json.dumps({"status": "running", "mode": "spin"}))
    started = client.post(f"/robots/{robot_id}/calibrations",
                          json={"mode": "wheel_step", "duration_s": 0.1})
    assert started.status_code == 200, started.text
    orphan = client.get(f"/robots/{robot_id}/calibrations/orphan").json()
    assert orphan["status"] == "interrupted"
    wait_result(lambda: client.get(f"/robots/{robot_id}/calibrations/{started.json()['id']}"))


def test_an_uncertain_session_under_a_newer_applied_one_is_settled_as_superseded(
        client):  # noqa: F811
    robot_id = fake_rover(client)["robot_id"]
    root = f"/robots/{robot_id}/calibrations"
    first, first_path = experiment(client, root, 1.02)
    assert client.post(first_path + "/apply").status_code == 200
    second, second_path = experiment(client, root, 1.05)
    assert client.post(second_path + "/apply").status_code == 200
    with closing(db.connect()) as conn:  # a crash left the older record mid-apply
        db.init_db(conn)
        result = json.loads(db.get(conn, "calibration_sessions", first)["result_json"])
        result["apply_state"] = "verifying"
        db.update(conn, "calibration_sessions", first, result_json=json.dumps(result))
    assert client.post(root, json={"mode": "straight"}).status_code == 409
    settled = client.post(first_path + "/reconcile")
    assert settled.status_code == 200, settled.text
    assert settled.json()["superseded_by"] == second
    assert accepted(robot_id)["calibration_id"] == second  # the newer state is untouched
    unblocked = client.post(root, json={"mode": "wheel_step", "duration_s": 0.1})
    assert unblocked.status_code == 200, unblocked.text
    wait_result(lambda: client.get(root + "/" + unblocked.json()["id"]))
