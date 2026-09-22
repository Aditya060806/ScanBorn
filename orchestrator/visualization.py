"""ID-scoped geometry and discovery routes shared by desktop and Android."""
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from capture.service import store
from capture.service.registration import registration
from orchestrator import db
from reconstruction.visualization import artifact_path, geometry_asset, previews
from robot.spec import load_spec

router = APIRouter()


@contextmanager
def connection():
    conn = db.connect()
    try:
        db.init_db(conn)
        yield conn
    finally:
        conn.close()


def need(conn, table, identifier):
    row = db.get(conn, table, identifier)
    if row is None:
        raise HTTPException(404, f"{table} entry not found")
    return row


IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def surface_entry(mesh_id: str, surface: str, capture: dict) -> dict:
    """The kept original export and the transform that puts it in the map frame.

    Imports since E2 record the operator's declared (or assumed) units and up axis; an older
    import recorded neither, so its transform is the identity it was always drawn with, and
    `transform_source` says it was assumed.
    """
    record = capture.get("import")
    if not isinstance(record, dict):
        record = {}
    return {"format": surface, "url": f"/meshes/{mesh_id}/surface/{surface}",
            "provenance": "imported", "to_map": record.get("to_map", IDENTITY),
            "units": record.get("units", "m"), "up_axis": record.get("up_axis", "z"),
            "transform_source": "recorded" if record else "assumed",
            "face_count": record.get("face_count")}


def mesh_manifest(conn, mesh_id):
    import json
    mesh = need(conn, "meshes", mesh_id)
    try:
        geometry = previews(mesh["ply_url"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    capture = store.read_meta(mesh["scan_id"])
    objects = [dict(row) for row in conn.execute(
        "SELECT id,label,bbox3d_json,confidence FROM semantic_objects WHERE mesh_id=?", (mesh_id,))]
    for obj in objects:
        obj["bbox3d"] = json.loads(obj.pop("bbox3d_json"))
        obj["provenance"] = "geometry-inferred"
    surface = next((ext for ext in ("ply", "obj")
                    if (Path(mesh["ply_url"]).parent / ("surface." + ext)).is_file()), None)
    return {"version": 1, "mesh_id": mesh_id,
            "surface": surface_entry(mesh_id, surface, capture) if surface else None,
            "scan_id": mesh["scan_id"],
            "registration": registration(capture),
            "coordinates": {"units": "metres", "up": "Z", "handedness": "right"},
            "bounds": geometry["bounds"], "floor_z": geometry["floor_z"],
            "point_count": geometry["point_count"],
            "geometry": [{**level, "format": "xyzrgb-le-v1", "stride": 16,
                          "color_space": "srgb", "provenance": "reconstructed",
                          "url": f"/meshes/{mesh_id}/geometry/{level['level']}"}
                         for level in geometry["levels"]],
            "capture": capture, "objects": objects,
            "robot_spec": load_spec("rover").as_dict(),
            "cosmetic_dimensions": "illustrative"}


@router.get("/meshes/{mesh_id}/visualization")
def mesh_visualization(mesh_id: str):
    with connection() as conn:
        return mesh_manifest(conn, mesh_id)


@router.get("/twins/{twin_id}/visualization")
def twin_visualization(twin_id: str):
    import json
    with connection() as conn:
        twin = need(conn, "twins", twin_id)
        result = mesh_manifest(conn, twin["mesh_id"])
        with open(twin["navmesh_url"], encoding="utf-8") as stream:
            navmesh = json.load(stream)
        spec_path = Path(twin["scene_url"]).parent / "robot-spec.json"
        if spec_path.is_file():
            binding = json.loads(spec_path.read_text(encoding="utf-8"))
            result.update(robot_spec=binding["snapshot"], robot_spec_binding=binding)
        else:
            result["robot_spec_provenance"] = "legacy-reference-not-pipeline-snapshot"
        result.update(twin_id=twin_id, floor_z=navmesh.get("floor_z", result["floor_z"]),
                      navmesh_url=f"/twins/{twin_id}/navmesh")
        return result


@router.get("/meshes/{mesh_id}/geometry/{level}")
def geometry(mesh_id: str, level: int):
    with connection() as conn:
        mesh = need(conn, "meshes", mesh_id)
        try:
            path = geometry_asset(mesh["ply_url"], level)
            info = next(x for x in previews(mesh["ply_url"])["levels"] if x["level"] == level)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="application/octet-stream", headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": '"' + info["sha256"] + '"',
        })


def page(table, limit, offset):
    # Table names come exclusively from the five route definitions below.
    with connection() as conn:
        rows = conn.execute(f"SELECT * FROM {table} "
                            "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        items = [{k: row[k] for k in row.keys() if not k.endswith("_url")} for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None}


@router.get("/scans")
def scans(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return page("scans", limit, offset)


@router.get("/twins")
def twins(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return page("twins", limit, offset)


@router.get("/robots")
def robots(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return page("robots", limit, offset)


@router.get("/missions")
def missions(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return page("missions", limit, offset)


@router.get("/deployments")
def deployments(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return page("deployments", limit, offset)


@router.get("/deploy/requests/{request_id}")
def deployment_request(request_id: str):
    """Recover a lost deployment response without issuing another start command."""
    import json
    if not 1 <= len(request_id) <= 128:
        raise HTTPException(400, "request_id must contain 1..128 characters")
    with connection() as conn:
        row = need(conn, "request_results", "deploy:" + request_id)
        return {"request_id": request_id, "status": row["status"],
                "response": json.loads(row["response_json"]) if row["response_json"] else None}


@router.get("/stages/{stage}/requests/{request_id}")
def stage_request(stage: str, request_id: str):
    """Read a durable non-motion stage result without submitting or restarting work."""
    import json
    routes = {"reconstruct": "reconstruct_scan", "segment": "segment",
              "generate-twin": "generate_twin_endpoint", "train": "train", "optimize": "optimize",
              "plan": "plan", "plan-preview": "plan_preview"}
    if stage not in routes:
        raise HTTPException(404, "Unknown recoverable stage")
    if not 1 <= len(request_id) <= 128:
        raise HTTPException(400, "request_id must contain 1..128 characters")
    with connection() as conn:
        row = need(conn, "request_results", routes[stage] + ":" + request_id)
        return {"stage": stage, "request_id": request_id, "status": row["status"],
                "response": json.loads(row["response_json"]) if row["response_json"] else None}


@router.get("/twins/{twin_id}")
def twin_detail(twin_id: str):
    with connection() as conn:
        twin = need(conn, "twins", twin_id)
        mesh = need(conn, "meshes", twin["mesh_id"])
        return {"twin_id": twin_id, "mesh_id": mesh["id"], "scan_id": mesh["scan_id"],
                "created_at": twin["created_at"],
                "visualization_url": f"/twins/{twin_id}/visualization",
                "navmesh_url": f"/twins/{twin_id}/navmesh"}


@router.get("/meshes/{mesh_id}/surface/{format}")
def surface_asset(mesh_id: str, format: str):
    if format not in ("ply", "obj"):
        raise HTTPException(404, "unknown surface format")
    with connection() as conn:
        mesh = need(conn, "meshes", mesh_id)
        try:
            path = artifact_path(str(Path(mesh["ply_url"]).parent / ("surface." + format)))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})
