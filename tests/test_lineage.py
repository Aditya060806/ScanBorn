"""Crossed lineage is refused at every stage, and one rover runs at most one drive (E0).

Each stage takes identifiers from the one before. A request that mixes records from two pipelines,
or presents a policy whose bytes, room, mission, robot or specification changed after it passed
its gate, is refused before anything runs. Two simultaneous deployments to one rover start exactly
one drive. The rover here is the in-process fake: software evidence, not physical.
"""
import json
import threading
from contextlib import closing

import numpy as np
import pytest

from orchestrator import db
from tests.test_calibration_reconciliation import experiment
from tests.test_calibration_sessions import wait_result
from tests.test_missions import GOAL, START, mission, twin_for, upload
from tests.test_orchestrator import scan_frame
from tests.test_rover_deploy import (client, fake_rover, trained_artifact,  # noqa: F401
                                     wait_until_ended)


def segmented_mesh(client) -> str:  # noqa: F811
    scan_id = upload(client, scan_frame())
    mesh_id = client.post("/reconstruct", json={"scan_id": scan_id}).json()["mesh_id"]
    assert client.post("/segment", json={"mesh_id": mesh_id}).status_code == 200
    return mesh_id


def row(table: str, identifier: str) -> dict:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return db.get(conn, table, identifier)


def refusal_code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def deploy(client, artifact_id, **body):  # noqa: F811
    return client.post("/deploy", json={"artifact_id": artifact_id, **body})


# ----------------------------------------------------------------------- crossed identifiers

def test_a_twin_takes_only_its_own_meshs_objects(client):  # noqa: F811
    first, second = segmented_mesh(client), segmented_mesh(client)
    crossed = client.post("/generate-twin", json={"mesh_id": first, "objects_id": second,
                                                  "environment": "home"})
    assert crossed.status_code == 400 and "another mesh" in crossed.text
    assert client.get("/twins").json()["total"] == 0  # refused before anything was written


def test_a_mission_takes_only_its_own_twins_plan(client):  # noqa: F811
    _, first = twin_for(client)
    _, second = twin_for(client)
    plan = client.post("/plan", json={"twin_id": second["twin_id"], "text": "go to the table"})
    assert plan.status_code == 200, plan.text
    crossed = mission(client, first["twin_id"], start=[*START, 0.0],
                      task_graph_id=plan.json()["task_graph_id"])
    assert crossed.status_code == 422 and refusal_code(crossed) == "bad_request"
    assert "different twin" in crossed.text


def test_training_takes_only_its_own_twins_mission_and_plan(client):  # noqa: F811
    _, first = twin_for(client)
    _, second = twin_for(client)
    other_mission = mission(client, second["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    crossed = client.post("/train", json={"twin_id": first["twin_id"],
                                          "mission_id": other_mission["mission_id"]})
    assert crossed.status_code == 400 and "another twin" in crossed.text
    other_plan = client.post("/plan", json={"twin_id": second["twin_id"],
                                            "text": "go to the table"}).json()
    crossed = client.post("/train", json={"twin_id": first["twin_id"],
                                          "task_graph_id": other_plan["task_graph_id"]})
    assert crossed.status_code == 400 and "another twin" in crossed.text


def test_a_calibration_session_is_read_only_through_its_own_robot(client):  # noqa: F811
    first, second = fake_rover(client)["robot_id"], fake_rover(client)["robot_id"]
    started = client.post(f"/robots/{first}/calibrations",
                          json={"mode": "wheel_step", "duration_s": 0.1})
    assert started.status_code == 200, started.text
    identifier = started.json()["id"]
    for suffix, method in (("", "get"), ("/apply", "post"), ("/reconcile", "post")):
        crossed = getattr(client, method)(f"/robots/{second}/calibrations/{identifier}{suffix}")
        assert crossed.status_code == 404, (suffix, crossed.text)
    wait_result(lambda: client.get(f"/robots/{first}/calibrations/{identifier}"))


# ------------------------------------------------- a gate result binds bytes, room, mission, robot

def rewrite_artifact(artifact_id: str) -> None:
    """Change the int8 weights on disk: the bytes still run, but they are not what passed."""
    path = row("artifacts", artifact_id)["artifact_url"]
    with np.load(path) as bundle:
        values = {key: bundle[key] for key in bundle.files}
    values["W"] = values["W"].copy()
    values["W"].flat[0] = np.int8(-1 if values["W"].flat[0] != -1 else 1)
    np.savez(path, **values)


def rewrite_room(twin_id: str) -> None:
    path = row("twins", twin_id)["navmesh_url"]
    with open(path, encoding="utf-8") as stream:
        navmesh = json.load(stream)
    navmesh["edited"] = "after validation"
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(navmesh, stream)


def point_policy_at_another_mission(client, twin_id: str, policy_id: str) -> None:  # noqa: F811
    other = mission(client, twin_id, start=[*START, 0.0], goal=GOAL).json()
    with closing(db.connect()) as conn:
        db.init_db(conn)
        db.update(conn, "policies", policy_id, mission_id=other["mission_id"])


@pytest.mark.parametrize("change, message", [
    ("artifact_bytes", "Artifact bytes changed"),
    ("twin_geometry", "Twin geometry changed"),
    ("mission", "different mission"),
])
def test_a_policy_changed_after_its_gate_cannot_drive(client, change, message):  # noqa: F811
    twin, _, policy, artifact = trained_artifact(client)
    if change == "artifact_bytes":
        rewrite_artifact(artifact["artifact_id"])
    elif change == "twin_geometry":
        rewrite_room(twin["twin_id"])
    else:
        point_policy_at_another_mission(client, twin["twin_id"], policy["policy_id"])
    before = client.get("/deployments").json()["total"]
    refused = deploy(client, artifact["artifact_id"], kind="sim")
    assert refused.status_code == 409 and refusal_code(refused) == "artifact_validation"
    assert message in refused.text
    assert client.get("/deployments").json()["total"] == before  # nothing drove


def test_a_policy_validated_for_one_robot_cannot_drive_another(client):  # noqa: F811
    first, second = fake_rover(client)["robot_id"], fake_rover(client)["robot_id"]
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    trained = client.post("/train", json={"twin_id": twin["twin_id"], "robot_id": first,
                                          "mission_id": planned["mission_id"]})
    assert trained.status_code == 200, trained.text
    artifact = client.post("/optimize", json={"policy_id": trained.json()["policy_id"]}).json()
    for target in ({"robot_id": second}, {"kind": "sim"}):
        refused = deploy(client, artifact["artifact_id"], **target)
        assert refused.status_code == 409 and refusal_code(refused) == "artifact_validation"
        assert "another robot" in refused.text
    assert client.get("/deployments").json()["total"] == 0


def test_a_policy_gated_under_one_specification_cannot_drive_a_recalibrated_rover(
        client):  # noqa: F811
    _, _, _, artifact = trained_artifact(client)  # gated under the stock rover specification
    robot_id = fake_rover(client)["robot_id"]
    _, path = experiment(client, f"/robots/{robot_id}/calibrations", 1.05)
    assert client.post(path + "/apply").status_code == 200  # the rover now has its own spec
    refused = deploy(client, artifact["artifact_id"], robot_id=robot_id)
    assert refused.status_code == 409 and refusal_code(refused) == "artifact_validation"
    assert "Robot specification changed" in refused.text


# ---------------------------------------------------------------- one rover, one drive at a time

def race(client, bodies):  # noqa: F811
    """POST every body to /deploy at the same moment, one thread each."""
    responses: list = [None] * len(bodies)
    gate = threading.Barrier(len(bodies))

    def send(index, body):
        gate.wait()
        responses[index] = client.post("/deploy", json=body)

    threads = [threading.Thread(target=send, args=(index, body))
               for index, body in enumerate(bodies)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert all(response is not None for response in responses)
    return responses


def test_two_simultaneous_deployments_to_one_rover_start_one_drive(client):  # noqa: F811
    _, _, _, artifact = trained_artifact(client)
    robot_id = fake_rover(client)["robot_id"]
    responses = race(client, [{"artifact_id": artifact["artifact_id"], "robot_id": robot_id,
                               "request_id": f"race-{n}"} for n in range(2)])
    started = [response for response in responses if response.status_code == 200]
    refused = [response for response in responses if response.status_code != 200]
    assert len(started) == 1 and len(refused) == 1, [r.text for r in responses]
    assert refused[0].status_code == 409 and refusal_code(refused[0]) == "robot_busy"
    assert refused[0].json()["detail"]["deployment_id"] == started[0].json()["deployment_id"]
    drive = wait_until_ended(client, started[0].json()["deployment_id"])
    assert drive["result"] is not None
    assert client.get("/deployments").json()["total"] == 1  # the refused start wrote no row


def test_the_same_request_sent_twice_at_once_starts_one_drive(client):  # noqa: F811
    _, _, _, artifact = trained_artifact(client)
    robot_id = fake_rover(client)["robot_id"]
    body = {"artifact_id": artifact["artifact_id"], "robot_id": robot_id, "request_id": "dup"}
    responses = race(client, [dict(body), dict(body)])
    started = {response.json()["deployment_id"] for response in responses
               if response.status_code == 200}
    assert len(started) == 1, [r.text for r in responses]
    for response in responses:
        # The duplicate either gets the first answer back or is told the first is running.
        assert response.status_code == 200 or refusal_code(response) == "request_running"
    wait_until_ended(client, started.pop())
    assert client.get("/deployments").json()["total"] == 1
