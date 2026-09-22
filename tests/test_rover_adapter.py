"""The orchestrator's rover adapter against the fake rover."""
import socket
import time

import numpy as np
import pytest

from policy.controller import K_V, K_W
from policy.finetune.train_bc import LinearPolicy
from policy.planning import plan_mission
from robot.adapters.fake_rover import FakeRover
from robot.adapters.rover import RoverError, RoverRobot
from robot.protocol import mission_payload
from robot.spec import load_spec

SPEC = load_spec("rover")
OPEN = {"origin": [0.0, 0.0], "cell": 0.1, "width": 40, "height": 40,
        "grid": [[0] * 40 for _ in range(40)], "profile": {"robot_radius": 0.2}}
MISSION = {**plan_mission(OPEN, (0.6, 0.6, 0.0), (2.6, 1.4)), "mission_id": "m1"}


def payload(session: str = "s1") -> dict:
    quantized = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    policy = {"W": quantized["W"]["data"].tolist(), "W_scale": quantized["W"]["scale"],
              "b": quantized["b"]["data"].tolist(), "b_scale": quantized["b"]["scale"]}
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # somewhere for telemetry to land
    sink.bind(("127.0.0.1", 0))
    port = sink.getsockname()[1]
    sink.close()
    return mission_payload(MISSION, policy, SPEC, session=session, telemetry_port=port)


def wait_for(robot: RoverRobot, states: tuple, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = robot.status()
        if status["state"] in states:
            return status
        time.sleep(0.02)
    raise AssertionError(f"state never reached {states}: {status}")


def test_every_command_round_trips():
    with FakeRover(SPEC, navmesh=OPEN, time_scale=10.0) as fake:
        robot = RoverRobot(fake.address)
        try:
            assert robot.connect() is True
            assert robot.supports_drive is False and robot.move(1.0, 1.0) is False
            with pytest.raises(RoverError) as obsolete:
                robot.calibrate("straight")
            assert obsolete.value.status_code == 410
            assert robot.configure({"wheelbase_m": 0.17})["source"] == "simulated"
            assert robot.set_world(OPEN)["ok"] is True
            assert robot.send_mission(payload())["state"] == "ready"
            robot.start_heartbeat("s1")
            assert robot.start()["state"] == "running"
            assert robot.stop()["state"] == "ready"
            assert robot.start()["state"] == "running"
            assert robot.estop("test")["state"] == "estop"
            with pytest.raises(RoverError) as refused:
                robot.start()
            assert refused.value.status_code == 409 and refused.value.detail["state"] == "estop"
            assert robot.reset()["state"] == "idle"
            assert robot.heartbeats_sent > 0
        finally:
            robot.close()


def test_a_refused_mission_carries_the_rovers_reasons():
    with FakeRover(SPEC, navmesh=OPEN) as fake:
        robot = RoverRobot(fake.address)
        try:
            body = payload()
            body["path"] = [[0.0, 0.0]]
            with pytest.raises(RoverError) as refused:
                robot.send_mission(body)
            assert refused.value.status_code == 400
            assert any(e.startswith("path") for e in refused.value.detail["errors"])
            assert "path" in str(refused.value)
        finally:
            robot.close()


def test_an_unreachable_rover_is_offline_not_an_exception():
    robot = RoverRobot("http://127.0.0.1:9", timeout=0.5)
    try:
        assert robot.connect() is False
    finally:
        robot.close()


def test_the_heartbeat_keeps_a_drive_alive_and_losing_it_stops_the_rover():
    with FakeRover(SPEC, navmesh=OPEN, time_scale=1.0) as fake:
        robot = RoverRobot(fake.address)
        try:
            robot.send_mission(payload())
            robot.start_heartbeat("s1")
            robot.start()
            time.sleep(1.5)  # longer than the deadman: still driving because of the heartbeat
            assert robot.status()["state"] == "running"
            assert robot.get_pose() != (0.0, 0.0, 0.0)

            robot.stop_heartbeat()
            stopped_at = time.monotonic()
            status = wait_for(robot, ("estop", "done", "error"), timeout=3.0)
            assert status["state"] == "estop" and status["reason"] == "heartbeat_lost"
            assert time.monotonic() - stopped_at <= 1.5
        finally:
            robot.close()
