"""Deployment sessions against the fake rover, without the web layer."""
import threading
import time

import numpy as np
import pytest

from orchestrator.deployments import DeploymentRegistry
from policy.controller import K_V, K_W
from policy.finetune.train_bc import LinearPolicy
from policy.planning import plan_mission
from robot.adapters.fake_rover import FakeRover
from robot.adapters.rover import RoverRobot
from robot.protocol import mission_payload
from robot.spec import load_spec

SPEC = load_spec("rover")
OPEN = {"origin": [0.0, 0.0], "cell": 0.1, "width": 40, "height": 40,
        "grid": [[0] * 40 for _ in range(40)], "profile": {"robot_radius": 0.2}}
MISSION = {**plan_mission(OPEN, (0.6, 0.6, 0.0), (2.6, 1.4)), "mission_id": "m1"}


def policy() -> dict:
    quantized = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    return {"W": quantized["W"]["data"].tolist(), "W_scale": quantized["W"]["scale"],
            "b": quantized["b"]["data"].tolist(), "b_scale": quantized["b"]["scale"]}


@pytest.fixture
def registry():
    reg = DeploymentRegistry(port=0, silence_s=0.8)
    yield reg
    reg.close()


def launch(registry, fake, deployment_id="d1", finished=None):
    """Open a session and start a drive on `fake`, the way /deploy will."""
    robot = RoverRobot(fake.address)
    port = registry.ensure_listening()
    done = finished if finished is not None else threading.Event()
    results: list = []

    def on_finish(session):
        results.append(session.snapshot())
        done.set()

    session = registry.open(deployment_id, MISSION["mission_id"], robot,
                            sim_trace=MISSION["path"], on_finish=on_finish)
    body = mission_payload(MISSION, policy(), SPEC, session=session.token, telemetry_port=port)
    assert robot.send_mission(body)["state"] == "ready"
    robot.start_heartbeat(session.token)
    assert robot.start()["state"] == "running"
    return session, robot, done, results


def test_a_drive_streams_telemetry_and_finishes_once(registry):
    frames: list = []
    with FakeRover(SPEC, navmesh=OPEN, time_scale=10.0) as fake:
        session, robot, done, results = launch(registry, fake)
        unsubscribe = registry.subscribe("d1", frames.append)
        try:
            assert done.wait(15.0), registry.get("d1").snapshot()
        finally:
            unsubscribe()
            robot.close()
    assert len(results) == 1
    result = results[0]
    assert result["state"] == "done" and result["finished"] is True and result["fake"] is True
    assert len(result["trace"]) >= 5
    assert any(m["type"] == "telemetry" for m in frames)
    assert frames[-1]["type"] == "finished"
    assert robot.heartbeats_sent > 0
    registry.finish("d1", state="estop")  # a second finish changes nothing
    assert len(results) == 1 and registry.get("d1").state == "done"


def test_an_operator_stop_halts_the_rover_and_ends_the_session(registry):
    with FakeRover(SPEC, navmesh=OPEN, time_scale=1.0) as fake:
        session, robot, done, results = launch(registry, fake)
        try:
            time.sleep(0.3)
            registry.stop("d1")
            assert done.is_set() and results[0]["state"] == "stopped"
            assert robot.status()["state"] == "ready"
        finally:
            robot.close()


def test_an_estop_through_the_registry_latches_on_the_rover(registry):
    with FakeRover(SPEC, navmesh=OPEN, time_scale=1.0) as fake:
        session, robot, done, results = launch(registry, fake)
        try:
            time.sleep(0.3)
            registry.estop("d1", reason="dashboard")
            status = robot.status()
            assert status["state"] == "estop" and status["reason"] == "dashboard"
            assert results[0]["state"] == "estop"
        finally:
            robot.close()


def test_a_drive_whose_telemetry_falls_silent_ends_as_lost(registry):
    fake = FakeRover(SPEC, navmesh=OPEN, time_scale=1.0).start()
    session, robot, done, results = launch(registry, fake)
    try:
        time.sleep(0.3)
        fake.close()  # the rover vanishes mid-drive
        assert done.wait(3.0)
        assert results[0]["state"] == "lost" and results[0]["reason"] == "telemetry_silent"
    finally:
        robot.close()


def test_stale_datagrams_from_another_session_are_ignored(registry):
    with FakeRover(SPEC, navmesh=OPEN, time_scale=10.0) as fake:
        first, robot, done, _ = launch(registry, fake, deployment_id="first")
        try:
            assert done.wait(15.0)
            frames_after_finish = first.frames
            time.sleep(0.3)  # the rover keeps reporting "done" under the finished token
            assert registry.get("first").frames == frames_after_finish
        finally:
            robot.close()
