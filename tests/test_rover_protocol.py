"""The fake rover against docs/robot/rover-protocol.md, over real sockets."""
import json
import math
import socket
import threading
import time

import httpx
import numpy as np
import pytest

from policy.controller import K_V, K_W
from policy.finetune.train_bc import LinearPolicy
from policy.planning import densify, plan_mission
from robot.adapters.fake_rover import FakeRover
from robot.protocol import mission_payload
from robot.spec import load_spec

SPEC = load_spec("rover")
OPEN = {"origin": [0.0, 0.0], "cell": 0.1, "width": 40, "height": 40,
        "grid": [[0] * 40 for _ in range(40)], "profile": {"robot_radius": 0.2}}
MISSION = {**plan_mission(OPEN, (0.6, 0.6, 0.0), (2.6, 1.4)), "mission_id": "m1"}  # ~2.2 m


def bundle() -> dict:
    quantized = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    return {"W": quantized["W"]["data"].tolist(), "W_scale": quantized["W"]["scale"],
            "b": quantized["b"]["data"].tolist(), "b_scale": quantized["b"]["scale"]}


class Telemetry:
    """A UDP socket standing in for the orchestrator's telemetry listener."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.frames: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.sock.getsockname()[1])

    def _read(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            self.frames.append(json.loads(data))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.sock.close()


class Heartbeat:
    """The orchestrator's heartbeat: a datagram every 200 ms until stopped."""

    def __init__(self, rover: FakeRover, session: str):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stop = threading.Event()
        self._target = ("127.0.0.1", rover.heartbeat_port)
        self._session = session
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def _beat(self):
        seq = 0
        while not self._stop.is_set():
            self.sock.sendto(json.dumps({"session": self._session, "seq": seq}).encode(),
                             self._target)
            seq += 1
            self._stop.wait(0.2)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.sock.close()


def wait_for(client: httpx.Client, states: tuple, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/status").json()
        if status["state"] in states:
            return status
        time.sleep(0.02)
    raise AssertionError(f"state never reached {states}: {status}")


@pytest.fixture
def fast():
    """A fake rover whose model runs ten times faster than real time."""
    with FakeRover(SPEC, navmesh=OPEN, seed=1, time_scale=10.0) as rover:
        with httpx.Client(base_url=rover.address, timeout=5.0) as client:
            yield rover, client


@pytest.fixture
def telemetry():
    listener = Telemetry()
    yield listener
    listener.close()


def load(client, telemetry, session="s1") -> dict:
    body = mission_payload(MISSION, bundle(), SPEC, session=session, telemetry_port=telemetry.port)
    response = client.post("/mission", json=body)
    assert response.status_code == 200, response.text
    return body


def test_a_new_rover_is_idle_and_says_it_is_fake(fast):
    rover, client = fast
    status = client.get("/status").json()
    assert status["state"] == "idle" and status["fake"] is True and status["protocol"] == 1
    assert status["heartbeat_port"] == rover.heartbeat_port
    assert status["battery_v"] is None  # the fake rover has no battery to report
    assert client.get("/nowhere").status_code == 404


def test_a_bad_mission_is_refused_naming_the_field(fast, telemetry):
    _, client = fast
    body = mission_payload(MISSION, bundle(), SPEC, session="s1", telemetry_port=telemetry.port)
    body["limits"]["v_max"] = 0.9
    refused = client.post("/mission", json=body)
    assert refused.status_code == 400
    assert any(e.startswith("limits.v_max") for e in refused.json()["errors"])
    assert client.post("/mission", content=b"{not json").status_code == 400
    assert client.get("/status").json()["state"] == "idle"


def test_a_mission_runs_to_done_with_telemetry(fast, telemetry):
    rover, client = fast
    load(client, telemetry)
    beat = Heartbeat(rover, "s1")
    try:
        assert client.post("/start").json()["state"] == "running"
        status = wait_for(client, ("done", "estop", "error"))
    finally:
        beat.close()
    assert status["state"] == "done", status
    assert math.dist(status["pose"][:2], MISSION["goal"]) <= 0.02 + 1e-6

    time.sleep(0.15)  # the rover keeps reporting its final state
    frames = [f for f in telemetry.frames if f["session"] == "s1"]
    assert len(frames) >= 5
    assert {"running", "done"} <= {f["state"] for f in frames}
    assert all(len(json.dumps(f)) < 256 for f in frames)
    assert "truth" in frames[-1]  # the fake rover's own position, never used on the real path


def test_stop_keeps_the_mission_so_it_can_start_again(fast, telemetry):
    rover, client = fast
    load(client, telemetry)
    beat = Heartbeat(rover, "s1")
    try:
        client.post("/start")
        assert client.post("/stop").json()["state"] == "ready"
        assert client.post("/start").json()["state"] == "running"
    finally:
        beat.close()


def test_estop_latches_until_reset(fast, telemetry):
    rover, client = fast
    load(client, telemetry)
    beat = Heartbeat(rover, "s1")
    try:
        client.post("/start")
        assert client.post("/estop", json={"reason": "dashboard"}).json()["state"] == "estop"
        refused = client.post("/start")
        assert refused.status_code == 409 and refused.json()["state"] == "estop"
        assert client.post("/mission", json={}).status_code == 409
        assert client.post("/reset").json()["state"] == "idle"
        load(client, telemetry)
        assert client.get("/status").json()["state"] == "ready"
    finally:
        beat.close()


def test_requests_in_the_wrong_state_are_refused(fast, telemetry):
    _, client = fast
    assert client.post("/start").status_code == 409  # idle: nothing to start
    assert client.post("/stop").status_code == 409
    load(client, telemetry)
    client.post("/start")
    assert client.post("/reset").status_code == 409  # never reset a moving rover
    assert client.post("/calibrate", json={"mode": "straight"}).status_code == 410


def test_losing_the_heartbeat_latches_an_estop_within_a_second(telemetry):
    with FakeRover(SPEC, navmesh=OPEN, seed=1, time_scale=1.0) as rover, \
            httpx.Client(base_url=rover.address, timeout=5.0) as client:
        load(client, telemetry)
        wrong = Heartbeat(rover, "another-session")  # heartbeats that are not for this drive
        try:
            started = time.monotonic()
            client.post("/start")
            status = wait_for(client, ("estop", "error", "done"), timeout=3.0)
            elapsed = time.monotonic() - started
        finally:
            wrong.close()
        assert status["state"] == "estop" and status["reason"] == "heartbeat_lost"
        assert 0.9 <= elapsed <= 1.4, elapsed

        right = Heartbeat(rover, "s1")  # heartbeats returning do not restart the drive
        try:
            time.sleep(0.5)
            assert client.get("/status").json()["state"] == "estop"
        finally:
            right.close()


def test_a_collision_in_the_fake_world_ends_in_error(fast, telemetry):
    rover, client = fast
    walled = {**OPEN, "grid": [[1 if 18 <= c <= 22 else 0 for c in range(40)] for _ in range(40)]}
    assert client.post("/sim/world", json={"navmesh": walled}).status_code == 200
    assert client.post("/sim/world", json={"navmesh": {"cell": 1}}).status_code == 400

    body = mission_payload(MISSION, bundle(), SPEC, session="s1", telemetry_port=telemetry.port)
    body["path"] = [[round(x, 4), round(y, 4)] for x, y in densify([(0.6, 2.0), (3.4, 2.0)], 0.1)]
    body["start"], body["goal"] = [0.6, 2.0, 0.0], [3.4, 2.0]
    assert client.post("/mission", json=body).status_code == 200
    beat = Heartbeat(rover, "s1")
    try:
        client.post("/start")
        status = wait_for(client, ("error", "done", "estop"))
    finally:
        beat.close()
    assert status["state"] == "error" and status["reason"] == "collision"
    assert status["pose"][0] < 1.8


def test_obsolete_blocking_calibration_is_refused(fast):
    _, client = fast
    response = client.post("/calibrate", json={"mode": "straight"})
    assert response.status_code == 410
    assert "/calibrations" in response.json()["error"]
