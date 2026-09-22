"""Capture and import inputs are bounded and validated before files exist (execution plan E2).

Covers the upload size bound (declared and streamed), server-side export paths, declared
units/up axis, PLY headers that declare more than they hold, and the registration gate that
keeps a physical rover off a scan with no home-marker frame.
"""
import asyncio
import json
import os
from contextlib import closing

import pytest

from orchestrator import body_limits, db
from reconstruction.reconstruct import read_ply
from robot.adapters.fake_rover import FakeRover
from tests.fixtures import scenes
from tests.test_missions import GOAL, START, mission
from tests.test_orchestrator import frame_bytes, scan_frame
from tests.test_rover_deploy import client, wait_until_ended  # noqa: F401

HOME = json.dumps({"home_pose": [0.0, 0.0, 1.5708]})


def scan_rows() -> int:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]


def scans_on_disk(tmp_path) -> list:
    root = tmp_path / "data" / "scans"
    return sorted(os.listdir(root)) if root.exists() else []


# --------------------------------------------------------------------------- body size

@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(body_limits, "LIMITS", (
        (body_limits.LIMITS[0][0], 4096), (body_limits.LIMITS[1][0], 4096)))


@pytest.mark.parametrize("route", ["/capture", "/capture/import", "/capture/named/import"])
def test_a_declared_body_over_the_limit_is_refused_before_it_is_read(
        client, tmp_path, small_limits, route):  # noqa: F811
    response = client.post(route, params={"index": 0},
                           files={"file": ("big.ply", b"x" * 10_000)})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "too_large"
    assert scans_on_disk(tmp_path) == [] and scan_rows() == 0


def test_a_streamed_body_without_a_length_is_refused(client, tmp_path, small_limits):  # noqa: F811
    def body():
        for _ in range(64):
            yield b"y" * 1024

    response = client.post("/capture/import", content=body(),
                           headers={"content-type": "multipart/form-data; boundary=x"})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "too_large"
    assert scans_on_disk(tmp_path) == [] and scan_rows() == 0


def test_the_limit_stops_pulling_a_streamed_body(small_limits):
    """At the ASGI level (the test client buffers a body before the app sees it): the route
    reads until the limit, the rest is never pulled, and the answer is 413."""
    pulled, sent = [], []

    async def receive():
        pulled.append(1024)
        return {"type": "http.request", "body": b"z" * 1024, "more_body": len(pulled) < 64}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):  # a route that reads its whole body
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"read it all"})

    scope = {"type": "http", "method": "POST", "path": "/capture", "headers": []}
    asyncio.run(body_limits.BodyLimit(app)(scope, receive, send))
    assert sum(pulled) == 5 * 1024  # 4096 bytes allowed: the fifth chunk ran over
    assert sent[0]["status"] == 413 and len(sent) == 2


def test_bodies_within_the_limit_and_other_routes_are_untouched(client):  # noqa: F811
    assert (body_limits.limit_for("POST", "/capture") or 0) > 16 * 1024 * 1024
    assert (body_limits.limit_for("POST", "/capture/abc/import") or 0) > 256 * 1024 * 1024
    assert body_limits.limit_for("GET", "/capture/abc") is None
    assert body_limits.limit_for("POST", "/plan") is None
    ok = client.post("/capture", params={"index": 0},
                     files={"file": ("f.npz", frame_bytes(scan_frame()))})
    assert ok.status_code == 200, ok.text


# ------------------------------------------------------------------ export path and units

def test_a_server_side_export_outside_the_exports_folder_is_refused(client, tmp_path):  # noqa: F811
    outside = scenes.write_mesh_ply(str(tmp_path / "room.ply"))
    exports = tmp_path / "data" / "exports"
    exports.mkdir(parents=True)
    for name in (outside, "../../room.ply", str(exports), "missing.ply"):
        response = client.post("/capture/import", params={"export_path": name})
        assert response.status_code == 400, name
        assert "exports/" in response.text
    assert scans_on_disk(tmp_path) == [] and scan_rows() == 0


@pytest.mark.parametrize("params", [{"units": "furlongs"}, {"up_axis": "x"},
                                    {"units": "CM"}, {"up_axis": ""}])
def test_unknown_units_or_up_axis_are_refused_before_any_file(client, tmp_path,  # noqa: F811
                                                              params):
    path = scenes.write_mesh_ply(str(tmp_path / "room.ply"))
    with open(path, "rb") as stream:
        response = client.post("/capture/import", params=params,
                               files={"file": ("room.ply", stream.read())})
    assert response.status_code == 400
    assert scans_on_disk(tmp_path) == [] and scan_rows() == 0


# ------------------------------------------------------------------------ PLY headers

def ply(header: str, body: bytes = b"") -> bytes:
    return header.encode() + body


@pytest.mark.parametrize("content, message", [
    (ply("ply\nformat binary_little_endian 1.0\nelement vertex 4000000000\n"
         "property float x\nproperty float y\nproperty float z\nend_header\n"),
     "at most"),
    (ply("ply\nformat binary_little_endian 1.0\nelement vertex 1000\n"
         "property float x\nproperty float y\nproperty float z\nend_header\n", b"\0" * 12),
     "body holds 1"),
    (ply("ply\nformat ascii 1.0\nelement vertex -3\nend_header\n"), "negative"),
    (ply("ply\nformat ascii 1.0\n" + "comment padding\n" * 2000 + "end_header\n"), "longer"),
    (ply("ply\nformat ascii 1.0\nelement face 1\nproperty list uchar int vertex_indices\n"
         "element vertex 1\nproperty float x\nproperty float y\nproperty float z\n"
         "end_header\n3 0 0 0\n0 0 0\n"), "must come before"),
    (ply("ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nproperty float y\n"
         "property float z\nend_header\n1 2 3\n4 5\n"), "declared properties"),
], ids=["huge-count", "short-body", "negative", "long-header", "vertex-not-first", "short-row"])
def test_a_header_that_declares_more_than_the_file_holds_is_refused(
        client, tmp_path, content, message):  # noqa: F811
    path = tmp_path / "bad.ply"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        read_ply(str(path))
    response = client.post("/capture/import", files={"file": ("bad.ply", content)})
    assert response.status_code == 400 and message in response.text
    assert scans_on_disk(tmp_path) == [] and scan_rows() == 0


# ----------------------------------------------------------------------- registration

def phone_scan(client, meta=HOME) -> str:  # noqa: F811
    params = {"index": 0, **({"meta": meta} if meta else {})}
    scan_id = client.post("/capture", params=params,
                          files={"file": ("f.npz", frame_bytes(scan_frame()))}).json()["scan_id"]
    assert client.post(f"/capture/{scan_id}/complete").status_code == 200
    return scan_id


def test_scan_status_says_whether_the_scan_shares_the_marker_frame(client, tmp_path):  # noqa: F811
    marked = client.get(f"/capture/{phone_scan(client)}").json()["registration"]
    assert marked["state"] == "home_marker" and marked["physically_registered"]
    unmarked = client.get(f"/capture/{phone_scan(client, None)}").json()["registration"]
    assert unmarked["state"] == "unknown" and not unmarked["physically_registered"]
    imported = client.post("/capture/import", files={"file": (
        "room.ply", open(scenes.write_mesh_ply(str(tmp_path / "r.ply")), "rb").read())}).json()
    status = client.get(f"/capture/{imported['scan_id']}").json()
    assert status["registration"]["state"] == "unknown"
    assert "Imported" in status["registration"]["reason"]


def artifact_from(client, scan_id: str) -> dict:  # noqa: F811
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    client.post("/segment", json={"mesh_id": mesh["mesh_id"]})
    twin = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"],
                                               "environment": "home"}).json()
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    policy = client.post("/train", json={"twin_id": twin["twin_id"],
                                         "mission_id": planned["mission_id"]})
    assert policy.status_code == 200, policy.text
    return client.post("/optimize", json={"policy_id": policy.json()["policy_id"]}).json()


@pytest.fixture
def physical_rover(client):  # noqa: F811
    """A rover registered by address, as an ESP32 would be (the process behind it is the fake
    rover, but the orchestrator treats it as physical)."""
    rover = FakeRover(time_scale=10.0).start()
    registered = client.post("/robots", json={"kind": "rover", "address": rover.address})
    assert registered.status_code == 200 and registered.json()["fake"] is False
    yield registered.json()["robot_id"]
    rover.close()


def test_a_physical_rover_refuses_a_scan_without_a_marker_frame(
        client, physical_rover):  # noqa: F811
    artifact = artifact_from(client, phone_scan(client, None))
    refused = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                           "robot_id": physical_rover})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "scan_not_registered"
    assert detail["registration"]["state"] == "unknown"
    assert client.get("/deployments").json()["total"] == 0  # nothing was sent to the rover
    # The same artifact still runs in simulation: inspection and simulation stay open.
    simulated = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                             "kind": "sim"})
    assert simulated.status_code == 200, simulated.text


def test_a_physical_rover_drives_a_scan_started_on_the_marker(
        client, physical_rover):  # noqa: F811
    artifact = artifact_from(client, phone_scan(client))
    started = client.post("/deploy", json={"artifact_id": artifact["artifact_id"],
                                           "robot_id": physical_rover})
    assert started.status_code == 200, started.text
    assert started.json()["fake"] is False
    assert wait_until_ended(client, started.json()["deployment_id"])["result"] is not None
