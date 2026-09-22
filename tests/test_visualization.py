import hashlib

from tests.test_capture_meta import client  # noqa: F401
from tests.test_orchestrator import upload_scan


def test_geometry_is_measured_bounded_and_id_scoped(client):  # noqa: F811
    scan_id = upload_scan(client)
    first = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    second = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    assert first["glb_url"] != second["glb_url"]
    response = client.get(f"/meshes/{first['mesh_id']}/visualization")
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["version"] == 1 and manifest["scan_id"] == scan_id
    assert manifest["point_count"] == first["point_count"]
    assert manifest["coordinates"]["up"] == "Z"
    for asset in manifest["geometry"]:
        binary = client.get(asset["url"])
        assert binary.status_code == 200
        assert len(binary.content) == asset["point_count"] * 16
        assert asset["point_count"] <= asset["level"]
        assert hashlib.sha256(binary.content).hexdigest() == asset["sha256"]
        assert "immutable" in binary.headers["cache-control"]
    assert client.get(f"/meshes/{first['mesh_id']}/geometry/123").status_code == 404
    assert client.get("/scans?limit=1").json()["items"][0]["id"] == scan_id
    assert client.get("/scans?limit=1000").status_code == 422
