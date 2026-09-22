"""A1 on synthetic scenes (execution plan E2): axes, floor, units and previews round-trip.

The same room (tests/fixtures/scenes.py) arrives as phone-format frames in the home frame and
as a Y-up centimetre mesh export. Both must land on the room's map coordinates: heights within
1 mm, the east box on +X and the north box on +Y (no mirrored or swapped axis), the floor at its
nonzero height. Previews are deterministic and name only IDs.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from reconstruction.reconstruct import read_ply
from tests.fixtures import scenes
from tests.test_capture_meta import client  # noqa: F401

MM = 1e-3
PIXEL = scenes.CAMERA_HEIGHT / scenes.FOCAL  # floor sample spacing, metres
HOME = {"home_pose": [0.0, 0.0, 1.5708], "device": "fixture"}


def upload_frames(client, frames, meta=HOME) -> str:  # noqa: F811
    scan_id = None
    for index, frame in enumerate(frames):
        params: dict = {"index": index, **({"scan_id": scan_id} if scan_id else {})}
        if index == 0 and meta is not None:
            params["meta"] = json.dumps(meta)
        response = client.post("/capture", params=params,
                               files={"file": ("f.npz", scenes.frame_bytes(frame))})
        assert response.status_code == 200, response.text
        scan_id = response.json()["scan_id"]
    assert scan_id and client.post(f"/capture/{scan_id}/complete").status_code == 200
    return scan_id


def import_file(client, path, **params):  # noqa: F811
    with open(path, "rb") as stream:
        response = client.post("/capture/import", params=params,
                               files={"file": (os.path.basename(path), stream.read())})
    assert response.status_code == 200, response.text
    return response.json()


def reconstructed(client, scan_id):  # noqa: F811
    mesh = client.post("/reconstruct", json={"scan_id": scan_id})
    assert mesh.status_code == 200, mesh.text
    manifest = client.get(f"/meshes/{mesh.json()['mesh_id']}/visualization").json()
    points, colors = read_ply(mesh.json()["glb_url"])
    return mesh.json(), manifest, points, colors


def by_color(points, colors, rgb):
    return points[np.all(colors == np.asarray(rgb, dtype=np.uint8), axis=1)]


def assert_room(points, colors):
    """Heights to 1 mm, footprints to one sample, and each box on its own axis."""
    floor = by_color(points, colors, scenes.FLOOR_COLOR)
    assert len(floor) > 1000
    assert np.abs(floor[:, 2] - scenes.FLOOR_Z).max() < MM
    for box in scenes.ROOM["boxes"]:
        top = by_color(points, colors, box["color"])
        assert len(top) > 20, box["name"]
        assert np.abs(top[:, 2] - (scenes.FLOOR_Z + box["height"])).max() < MM, box["name"]
        (cx, cy), (dx, dy) = box["centre"], box["size"]
        assert np.all(np.abs(top[:, 0] - cx) <= dx / 2 + MM), box["name"]
        assert np.all(np.abs(top[:, 1] - cy) <= dy / 2 + MM), box["name"]
    east = by_color(points, colors, scenes.ROOM["boxes"][0]["color"]).mean(axis=0)
    north = by_color(points, colors, scenes.ROOM["boxes"][1]["color"]).mean(axis=0)
    assert east[0] > 1.0 and abs(east[1] - 0.4) < 0.05   # +X, not mirrored to -X
    assert north[1] > 1.5 and abs(north[0] - 0.3) < 0.05  # +Y, not swapped with X


def test_phone_frames_land_on_the_room_in_the_home_frame(client):  # noqa: F811
    scan_id = upload_frames(client, scenes.known_axis_frames())
    mesh, manifest, points, colors = reconstructed(client, scan_id)
    assert_room(points, colors)
    assert abs(manifest["floor_z"] - scenes.FLOOR_Z) < MM
    (lo, hi) = manifest["bounds"]
    (x0, x1), (y0, y1) = scenes.ROOM["floor"]["x"], scenes.ROOM["floor"]["y"]
    for got, want in zip((*lo[:2], *hi[:2]), (x0, y0, x1, y1)):
        # Edges are sampled, not exact: a voxel there survives fusion only if both frames
        # put a sample in it, so an edge can sit up to a sample plus a voxel inside the room.
        assert abs(got - want) <= 2 * PIXEL
    assert abs(lo[2] - scenes.FLOOR_Z) < MM
    assert abs(hi[2] - (scenes.FLOOR_Z + 0.8)) < MM
    assert manifest["registration"]["state"] == "home_marker"
    assert mesh["registration"]["physically_registered"] is True

    assert client.post("/segment", json={"mesh_id": mesh["mesh_id"]}).status_code == 200
    twin = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"],
                                               "environment": "home"})
    assert twin.status_code == 200, twin.text
    twin = twin.json()
    navmesh = client.get(f"/twins/{twin['twin_id']}/navmesh").json()
    assert abs(navmesh["floor_z"] - scenes.FLOOR_Z) < MM


@pytest.mark.parametrize("writer, name", [
    (lambda p: scenes.write_mesh_ply(p, binary=True), "room.ply"),
    (lambda p: scenes.write_mesh_ply(p, binary=False), "room-ascii.ply"),
    (scenes.write_mesh_obj, "room.obj"),
])
def test_a_y_up_centimetre_export_lands_on_the_same_room(client, tmp_path, writer,  # noqa: F811
                                                         name):
    path = writer(str(tmp_path / name))
    imported = import_file(client, path, units="cm", up_axis="y")
    record = imported["import"]
    assert (record["units"], record["units_source"]) == ("cm", "declared")
    assert (record["up_axis"], record["up_axis_source"]) == ("y", "declared")
    assert record["face_count"] == 7  # six room triangles and the out-of-range one
    assert record["original_sha256"] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert imported["registration"]["state"] == "unknown"

    mesh, manifest, points, colors = reconstructed(client, imported["scan_id"])
    vertices, _ = scenes.room_mesh()
    assert points.shape == (len(vertices), 3)
    order = np.lexsort(points.T), np.lexsort(vertices[:, :3].T)
    assert np.abs(points[order[0]] - vertices[order[1], :3]).max() < MM
    assert abs(manifest["floor_z"] - scenes.FLOOR_Z) < MM
    assert manifest["registration"]["physically_registered"] is False

    surface = manifest["surface"]
    assert surface["transform_source"] == "recorded" and surface["face_count"] == 7
    assert np.allclose(np.asarray(surface["to_map"])[:3, :3],
                       [[0.01, 0, 0], [0, 0, -0.01], [0, 0.01, 0]])
    served = client.get(surface["url"])
    assert served.status_code == 200 and served.content == Path(path).read_bytes()


def test_an_undeclared_export_is_recorded_as_assumed_metres_z_up(client, tmp_path):  # noqa: F811
    path = scenes.write_mesh_ply(str(tmp_path / "room.ply"))
    record = import_file(client, path)["import"]
    assert (record["units"], record["units_source"]) == ("m", "assumed")
    assert (record["up_axis"], record["up_axis_source"]) == ("z", "assumed")
    assert np.allclose(record["to_map"], np.eye(4))


def test_a_sparse_scan_keeps_exact_heights(client):  # noqa: F811
    scan_id = upload_frames(client, scenes.sparse_frames())
    _, manifest, points, colors = reconstructed(client, scan_id)
    dense = upload_frames(client, scenes.known_axis_frames())
    assert len(points) < 0.1 * len(reconstructed(client, dense)[2])
    floor = by_color(points, colors, scenes.FLOOR_COLOR)
    assert len(floor) and np.abs(floor[:, 2] - scenes.FLOOR_Z).max() < MM
    assert abs(manifest["floor_z"] - scenes.FLOOR_Z) < MM


def test_frames_without_colour_are_grey_everywhere(client):  # noqa: F811
    scan_id = upload_frames(client, scenes.missing_color())
    _, manifest, points, colors = reconstructed(client, scan_id)
    assert np.all(colors == np.asarray(scenes.GREY, dtype=np.uint8))
    preview = client.get(manifest["geometry"][0]["url"]).content
    records = np.frombuffer(preview, dtype=np.uint8).reshape(-1, 16)
    assert np.all(records[:, 12:15] == 200)


def test_an_imported_cloud_without_colour_properties_is_grey(client, tmp_path):  # noqa: F811
    points, _ = scenes.large_cloud(count=2000)
    path = scenes.write_binary_cloud(str(tmp_path / "plain.ply"), points, None)
    imported = import_file(client, path)
    _, _, _, colors = reconstructed(client, imported["scan_id"])
    assert np.all(colors == 200)


def decode(content: bytes) -> tuple:
    records = np.frombuffer(content, dtype=np.uint8).reshape(-1, 16)
    xyz = records[:, :12].copy().view("<f4").reshape(-1, 3)
    return xyz, records[:, 12:15]


def paths_in(value, root: str) -> list:
    """Every string in a JSON value that names a server file or the data directory."""
    if isinstance(value, dict):
        return [p for v in value.values() for p in paths_in(v, root)]
    if isinstance(value, list):
        return [p for v in value for p in paths_in(v, root)]
    if isinstance(value, str) and (root in value or "\\" in value or value.startswith("data/")
                                   or (len(value) > 2 and value[1] == ":")):
        return [value]
    return []


def test_previews_are_deterministic_levelled_and_name_only_ids(client, tmp_path):  # noqa: F811
    points, colors = scenes.large_cloud()
    path = scenes.write_binary_cloud(str(tmp_path / "large.ply"), points, colors)
    scan_id = import_file(client, path)["scan_id"]
    mesh, manifest, stored, stored_colors = reconstructed(client, scan_id)
    assert manifest["point_count"] == len(points)
    levels = {level["level"]: level for level in manifest["geometry"]}
    counts = {k: v["point_count"] for k, v in levels.items()}
    assert counts == {100_000: 100_000, 500_000: 500_000}
    first = {k: client.get(v["url"]).content for k, v in levels.items()}
    for level, content in first.items():
        assert hashlib.sha256(content).hexdigest() == levels[level]["sha256"]
        xyz, rgb = decode(content)
        chosen = np.linspace(0, len(stored) - 1, level, dtype=np.int64)
        assert np.abs(xyz - stored[chosen].astype(np.float32)).max() < MM
        assert np.array_equal(rgb, stored_colors[chosen])

    # Regenerated from scratch, the previews are the same bytes.
    shutil.rmtree(Path(mesh["glb_url"]).parent / "visualization-v1")
    again = client.get(f"/meshes/{mesh['mesh_id']}/visualization").json()
    assert [level["sha256"] for level in again["geometry"]] == \
        [level["sha256"] for level in manifest["geometry"]]

    root = str(Path(os.environ["SCANBORN_DATA"]).resolve())
    assert paths_in(manifest, root) == []
    assert all(asset["url"].startswith(f"/meshes/{mesh['mesh_id']}/")
               for asset in manifest["geometry"])
    assert mesh["visualization_url"] == f"/meshes/{mesh['mesh_id']}/visualization"
