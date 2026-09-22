"""A durable calibration request never replays motion after a lost response."""
import httpx
import pytest

from robot.adapters.rover import RoverRobot
from tests.test_rover_deploy import client, fake_rover  # noqa: F401
from tests.test_calibration_sessions import wait_result


def test_lost_rover_response_keeps_identity_without_replaying(client, monkeypatch):  # noqa: F811
    root = f"/robots/{fake_rover(client)['robot_id']}/calibrations"
    original = RoverRobot._call
    starts = []

    def lost(self, method, route, *args, **kwargs):
        if method == "POST" and route == "/calibrations":
            persisted = client.get(root).json()["items"]
            assert len(persisted) == 1
            assert persisted[0]["start_state"] == "pending"
            assert persisted[0]["id"] == args[0]["session"]
        answer = original(self, method, route, *args, **kwargs)
        if method == "POST" and route == "/calibrations":
            starts.append(answer["id"])
            # The motion command was accepted, but its response never reached orchestration.
            raise httpx.ReadTimeout("response lost")
        return answer

    monkeypatch.setattr(RoverRobot, "_call", lost)
    body = {"mode": "wheel_step", "duration_s": 0.1, "request_id": "lost"}
    assert client.post(root, json=body).status_code == 502
    retry = client.post(root, json=body)
    assert retry.status_code == 200
    assert retry.json()["start_state"] == "unknown"
    assert retry.json()["status"] == "interrupted"
    assert starts == [retry.json()["id"]]
    assert client.get(root).json()["total"] == 1
    assert client.get(root + "/" + starts[0]).json()["request_id"] == "lost"
    lookup = root.replace("/calibrations", "/calibration-requests/lost")
    assert client.get(lookup).json()["id"] == starts[0]
    assert client.get(lookup + "-missing").status_code == 404
    assert client.post(root, json={**body, "mode": "spin"}).status_code == 409


def test_completed_request_retry_returns_same_session(client):  # noqa: F811
    from scanborn import ScanBorn

    sdk = ScanBorn(http=client)
    root = f"/robots/{fake_rover(client)['robot_id']}/calibrations"
    body = {"mode": "wheel_step", "duration_s": 0.1, "request_id": "complete"}
    first = client.post(root, json=body)
    assert first.status_code == 200, first.text
    identifier = first.json()["id"]
    wait_result(lambda: client.get(root + "/" + identifier))
    repeated = client.post(root, json=body)
    assert repeated.json()["id"] == identifier
    assert repeated.json()["status"] != "running"
    assert sdk.calibration_request(root.split("/")[2], "complete")["id"] == identifier
    assert client.get(root).json()["total"] == 1


@pytest.mark.parametrize("value", ["", 3, "x" * 129])
def test_invalid_request_identity_is_rejected(client, value):  # noqa: F811
    root = f"/robots/{fake_rover(client)['robot_id']}/calibrations"
    assert client.post(root, json={"mode": "spin", "request_id": value}).status_code == 400
    assert client.get(root).json()["total"] == 0
