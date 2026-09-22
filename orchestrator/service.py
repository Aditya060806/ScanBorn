"""ScanBorn orchestrator — the single REST surface (blueprint section 16).

Stage modules are imported directly rather than called over HTTP: one process, one
SQLite metadata store, every call recorded as a job. Execution is synchronous, which is
what makes the demo debuggable; the job registry is already in place for when it isn't.
"""
import asyncio
import json
import math
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
import numpy as np
from fastapi import Body, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from scipy import ndimage

from capture.scaniverse import (DEFAULT_UNITS, DEFAULT_UP_AXIS, DEFERRED_EXTS, SUPPORTED_EXTS,
                                to_map)
from capture.service import imports
from capture.service.imports import prepare_import, staging_directory
from capture.service import store
from capture.service.registration import registration
from capture.service.validation import MAX_CHUNK_BYTES, MAX_INDEX, validate_frame
from deployment import artifact_format
from deployment.aihub_export.export_script import ExportConfigurationError, export_model
from orchestrator import db, jobs, calibrations, providers, specifications
from orchestrator import artifact_validation
from orchestrator.body_limits import BodyLimit
from orchestrator.request_scope import connections, scoped
from orchestrator.idempotency import idempotent, serialize_start
from orchestrator.visualization import router as visualization_router
from orchestrator.deployments import DeploymentRegistry
from policy.controller import OBSERVATION, policy_manifest
from policy.evaluate import (
    MISSION_GATE,
    SIM_GATE,
    evaluate,
    evaluate_mission,
    free_cells,
    inflate,
    record_demos,
    rms_deviation,
)
from policy.finetune.train_bc import finetune_bc, make_baseline
from policy.planning import (
    EPS,
    PREFERRED_MARGIN,
    MissionError,
    cell_of,
    clearance_field,
    densify,
    nearest_valid,
    path_clearance,
    plan_mission,
    plan_path,
    robot_radius,
    traversable,
)
from policy.sim import Noise
from reconstruction.floor import estimate_floor_z
from reconstruction.reconstruct import read_ply, reconstruct
from robot.adapters.fake_rover import FakeRover
from robot.adapters.registry import ADAPTERS, get_robot
from robot.adapters.rover import RoverError, RoverRobot
from robot.policy_runner import load_int8_policy, run_policy
from robot.protocol import MAX_PATH_POINTS, load_bundle, mission_payload
from robot.spec import load_spec, spec_from_dict
from sarvam.task_engine.graph import TaskGraph
from sarvam.task_engine.runner import plan_instruction, profile_scene
from semantic.service.inference import segment_points
from twin.generator import generate_twin
from twin.navmesh import (
    FREE,
    GRID_CELLS,
    ROBOT_HEIGHT,
    UNKNOWN,
    as_array,
    counts,
    encode_cells,
    nearest_free,
    rasterise,
)
from twin.profile import profile_for


@asynccontextmanager
async def _lifespan(_app):
    jobs.interrupt_running()
    providers.log_configuration()
    yield
    # Live drives end as "lost", and the fake rovers this process started stop with it.
    calibrations.close_sessions()
    DEPLOYMENTS.close()
    for rover in list(FAKE_ROVERS.values()):
        rover.close()
    FAKE_ROVERS.clear()


app = FastAPI(title="ScanBorn Orchestrator", version="2.0", lifespan=_lifespan)
app.add_middleware(BodyLimit)
app.include_router(visualization_router)
app.include_router(calibrations.router)

OBS_DIM = ACT_DIM = 2  # [d·cos e, e] -> (v, ω); see policy/controller.py
DEFAULT_SPEC = "rover"  # robot/specs/rover.yaml, the reference rover
DEPLOY_SEED = 7         # a simulated deployment is one fixed, reproducible noisy run
# How far from its goal a deployment without a mission starts. The rover dead-reckons, and in the
# rover model routes up to about 2.5 m hold the 10 cm bar (Phase 3 plan, decision 14).
DEPLOY_START_DISTANCE = 2.5
# Live rover drives (orchestrator/deployments.py), and the fake rovers this process runs for
# `POST /robots {"kind": "fake_rover"}`, by robot id.
DEPLOYMENTS = DeploymentRegistry()
FAKE_ROVERS: dict = {}


def artifacts_dir(*parts) -> str:
    path = os.path.join(store.data_root(), *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _db():
    conn = db.connect()
    db.init_db(conn)
    scope = connections.get()
    if scope is not None:
        scope.append(conn)
    return conn


def _need(conn, table: str, row_id: str) -> dict:
    row = db.get(conn, table, row_id)
    if row is None:
        raise HTTPException(404, f"{table[:-1]} {row_id!r} not found")
    return row


@app.get("/health")
@scoped
def health():
    return {"status": "ok"}


@app.get("/providers")
def provider_settings():
    """Each workload's provider (planner, profiler, export), where it runs, and whether it is
    ready. Local unless a provider is named; credential values are never returned."""
    return providers.report()


@app.post("/providers/settings")
def save_provider_settings(body: dict = Body(...)):
    """Persist explicit provider chains and consent; secrets remain server-side."""
    return providers.save_settings(body)


# --------------------------------------------------------------------------- capture

# What /reconstruct repeats from a scan's meta.json: how it was captured and where it starts.
CAPTURE_SUMMARY = ("device", "depth_source", "depth_mode", "min_confidence", "range_m",
                   "home_pose", "frames", "uploaded", "failed", "skipped")


def _meta_update(raw) -> dict:
    """A capture's meta, checked: a JSON object whose `home_pose`, if given, is 3 finite numbers.

    Checked on the way in, so `_home_pose` and every later reader can trust meta.json.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise HTTPException(400, {"error": "meta is not valid JSON", "code": "bad_meta"})
    if not isinstance(raw, dict):
        raise HTTPException(400, {"error": "meta must be a JSON object", "code": "bad_meta"})
    if "home_pose" in raw:
        pose = raw["home_pose"]
        if not (isinstance(pose, list) and len(pose) == 3 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in pose)):
            raise HTTPException(400, {"error": "meta.home_pose must be [x, y, heading]: three "
                                               "finite numbers",
                                      "code": "bad_meta", "field": "home_pose"})
    return raw


def _merge_meta(scan_id: str, update: dict) -> dict:
    """Merge into meta.json: a field sent again replaces the earlier value, and fields sent only
    earlier (the home pose on the first chunk) are kept."""
    merged = {**store.read_meta(scan_id), **update}
    store.save_meta(scan_id, merged)
    return merged


@app.post("/capture")
@scoped
async def capture(file: UploadFile | None = None, scan_id: str | None = None, index: int = 0,
                  meta: str | None = None, device: str = "unknown"):
    """Upload one frame chunk. Resumable: re-POSTing an index overwrites it.

    `meta` (a JSON object) is merged into the scan's meta.json. The phone sends its capture settings
    and `home_pose` with the first chunk; a malformed `home_pose` is refused before anything is
    stored.
    """
    update = _meta_update(meta) if meta else None
    scan_id = scan_id or db.new_id()
    try:
        store.scan_path(scan_id)
        if not 0 <= index <= MAX_INDEX:
            raise ValueError("frame index must be between 0 and 99999")
        payload = await file.read(MAX_CHUNK_BYTES + 1) if file is not None else None
        if payload is not None:
            validate_frame(payload)
    except ValueError as exc:
        raise HTTPException(400, {"code": "bad_frame", "message": str(exc)}) from exc
    with store.scan_lock(scan_id):
        conn = _db()
        try:
            existing = db.get(conn, "scans", scan_id)
            if existing is not None and existing["status"] == "complete":
                raise HTTPException(409, "scan is already complete")
            if existing is None:
                db.insert(conn, "scans", id=scan_id, device=device, status="uploading",
                          capture_frame_json=meta)
            if update is not None:
                _merge_meta(scan_id, update)
            if payload is not None:
                store.save_chunk(scan_id, index, payload)
            return {"scan_id": scan_id, **store.status(scan_id)}
        finally:
            conn.close()


@app.post("/capture/import")
@app.post("/capture/{scan_id}/import")
@scoped
async def capture_import(scan_id: str | None = None, file: UploadFile | None = None,
                         export_path: str | None = None, device: str = "scaniverse",
                         units: str | None = None, up_axis: str | None = None):
    """Open a scan from a finished export (Scaniverse .ply/.obj) instead of frame chunks.

    Two callers, one route: the dashboard posts the file itself, while a server-side
    batch run can name a file inside `exports/` in the data directory.

    An export does not say its units or which axis is up: `units` (m, cm, mm, ft, in) and
    `up_axis` (z, y) declare them, and one left out is recorded as assumed (m, z). The points
    are moved into the map frame; the export itself is kept unchanged with its transform. An
    import has no home-marker frame, so its registration is unknown: it can be inspected and
    simulated, and a physical rover refuses it.
    """
    if (file is None) == (export_path is None):
        raise HTTPException(400, "provide exactly one of: file upload, export_path")
    try:
        to_map(DEFAULT_UNITS if units is None else units,
               DEFAULT_UP_AXIS if up_axis is None else up_axis)
        source_path = imports.export_path(export_path) if export_path is not None else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    scan_id = scan_id or db.new_id()
    try:
        target = store.scan_path(scan_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = (file.filename or "") if file is not None else str(source_path)
    ext = os.path.splitext(filename)[1].lower()
    if ext in DEFERRED_EXTS:
        raise HTTPException(501, DEFERRED_EXTS[ext])
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(400, f"unsupported export type {ext!r}; expected PLY or OBJ")
    conn = _db()
    job_id = jobs.create_job("capture_import")
    with store.scan_lock(scan_id), staging_directory() as temporary:
        stage = Path(temporary) / "scan"
        stage.mkdir()
        if db.get(conn, "scans", scan_id) is not None or target.exists():
            raise HTTPException(409, "scan already exists; import with a new scan identifier")
        try:
            if file is not None:
                result = prepare_import(file.file, filename, stage, units, up_axis)
            else:
                with open(str(source_path), "rb") as source:
                    result = prepare_import(source, filename, stage, units, up_axis)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, target)
            try:
                db.insert(conn, "scans", id=scan_id, device=device, status="complete")
            except Exception:
                conn.rollback()
                os.replace(target, stage)
                raise
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
    jobs.finish_job(job_id, detail=scan_id)
    return {"scan_id": scan_id, "status": "complete", "frame_count": 0,
            "point_count": result["point_count"], "source": result["source"],
            "format": result["format"], "job_id": job_id, "import": result["import"],
            "registration": registration(store.read_meta(scan_id))}


@app.post("/capture/{scan_id}/complete")
@scoped
def capture_complete(scan_id: str, body: dict | None = Body(None)):
    """Close a scan. An optional `{"meta": {...}}` body is merged into its meta.json, so the app can
    add its final counts without resending, or losing, the home pose."""
    update = _meta_update(body["meta"]) if body and "meta" in body else None
    with store.scan_lock(scan_id):
        conn = _db()
        _need(conn, "scans", scan_id)
        if update is not None:
            _merge_meta(scan_id, update)
        frame_count = store.complete(scan_id)
        conn.execute("UPDATE scans SET status = 'complete' WHERE id = ?", (scan_id,))
        conn.commit()
        return {"scan_id": scan_id, "status": "complete", "frame_count": frame_count}


@app.get("/capture/{scan_id}")
@scoped
def capture_status(scan_id: str):
    """Upload state, frame count and whether the scan shares the rover's physical frame."""
    try:
        status = store.status(scan_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**status, "registration": registration(store.read_meta(scan_id))}


# ----------------------------------------------------------------------- reconstruct

@app.post("/reconstruct")
@scoped
@idempotent
def reconstruct_scan(body: dict = Body(...)):
    scan_id, mode = body["scan_id"], body.get("mode", "fast")
    conn = _db()
    _need(conn, "scans", scan_id)
    job_id = jobs.create_job("reconstruct")

    try:
        result = reconstruct(store.scan_dir(scan_id), mode=mode,
                             out_dir=artifacts_dir("meshes", scan_id, db.new_id()))
    except NotImplementedError as exc:
        jobs.finish_job(job_id, ok=False, detail=str(exc))
        raise HTTPException(501, str(exc))
    except (ValueError, RuntimeError) as exc:
        jobs.finish_job(job_id, ok=False, detail=str(exc))
        raise HTTPException(400, str(exc))

    mesh_id = db.insert(conn, "meshes", scan_id=scan_id, mode=mode,
                        ply_url=result["ply_path"], glb_url=result.get("glb_path"))
    jobs.finish_job(job_id, detail=mesh_id)
    meta = store.read_meta(scan_id)
    capture = {key: meta[key] for key in CAPTURE_SUMMARY if key in meta}
    return {"mesh_id": mesh_id, "job_id": job_id,
            # Legacy: the server-local path of the point cloud. Clients load geometry through
            # visualization_url, which names only IDs.
            "glb_url": result.get("glb_path") or result["ply_path"],
            "visualization_url": f"/meshes/{mesh_id}/visualization",
            "point_count": result["point_count"],
            # Frames fused, the views a voxel needed, voxels dropped; None for an imported cloud.
            "fusion": result.get("fusion"),
            "capture": capture or None,
            "registration": registration(meta)}


# --------------------------------------------------------------------------- segment

@app.post("/segment")
@scoped
@idempotent
def segment(body: dict = Body(...)):
    mesh_id = body["mesh_id"]
    conn = _db()
    mesh = _need(conn, "meshes", mesh_id)
    job_id = jobs.create_job("semantic")

    # Meshes are immutable, and the mesh ID names their semantic set. Repeating this
    # stage must preserve object IDs instead of appending duplicate boxes to the twin.
    with store.scan_lock(mesh["scan_id"]):
        objects = _objects_for(conn, mesh_id)
        if not objects:
            points, colors = read_ply(mesh["ply_url"])
            objects = segment_points(points, colors)
            with conn:
                conn.executemany(
                    "INSERT INTO semantic_objects(id,mesh_id,label,bbox3d_json,confidence) "
                    "VALUES(?,?,?,?,?)",
                    [(obj["id"], mesh_id, obj["label"], json.dumps(obj["bbox3d"]),
                      obj["confidence"]) for obj in objects])

    jobs.finish_job(job_id, detail=f"{len(objects)} objects")
    # Objects are scoped to their mesh, so the mesh id names the set.
    return {"objects_id": mesh_id, "job_id": job_id, "objects": objects}


def _objects_for(conn, mesh_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM semantic_objects WHERE mesh_id = ?", (mesh_id,)
    ).fetchall()
    return [{"id": r["id"], "label": r["label"], "confidence": r["confidence"],
             "bbox3d": json.loads(r["bbox3d_json"])} for r in rows]


# ---------------------------------------------------------------------- generate-twin

@app.post("/generate-twin")
@scoped
@idempotent
def generate_twin_endpoint(body: dict = Body(...)):
    mesh_id = body["mesh_id"]
    cell_m = body.get("cell_m")
    if cell_m is not None and cell_m not in GRID_CELLS:
        raise HTTPException(400, f"cell_m must be one of {list(GRID_CELLS)}, or omitted to "
                                 "let the scan's density decide")
    conn = _db()
    mesh = _need(conn, "meshes", mesh_id)
    if body.get("objects_id") not in (None, mesh_id):
        raise HTTPException(400, "semantic objects belong to another mesh")
    # The profiler is consulted only when no environment is named; check it before any work,
    # and resolve the robot spec before anything is written, so a refusal leaves no files.
    profiler_chain = None if body.get("environment") else providers.chain("profiler")
    spec, spec_id = specifications.resolve(
        conn, body.get("robot_id"), body.get("robot_spec"), DEFAULT_SPEC)
    job_id = jobs.create_job("twin_generate")

    objects = _objects_for(conn, body.get("objects_id") or mesh_id)
    if not objects:
        jobs.finish_job(job_id, ok=False, detail="no objects")
        raise HTTPException(
            400, f"no semantic objects for mesh {mesh_id!r}; POST /segment first")

    # An explicitly named environment wins over inference: an operator who knows the space
    # should not have to argue with a classifier. Otherwise the profiler chain reads the scene.
    profile_provenance = None
    if body.get("environment"):
        profile = profile_for(body["environment"])
    else:
        with providers.track(body.get("request_id")) as cancel:
            outcome = profile_scene(objects, profiler_chain, limits=providers.limits(),
                                    budget=providers.budget(), cancel=cancel,
                                    request_id=body.get("request_id"))
        profile_provenance = outcome.provenance
        if outcome.status != "accepted":
            jobs.finish_job(job_id, ok=False, detail=outcome.status)
            raise _provider_refusal("profiler", outcome.status, outcome.provenance)
        profile = outcome.profile

    # The cloud, not the bounding boxes, decides what the robot can drive through.
    points, _ = read_ply(mesh["ply_url"])
    folder = artifacts_dir("twins", mesh_id, db.new_id())
    result = generate_twin(objects, folder, cell=cell_m, points=points, profile=profile)
    with open(result["navmesh_path"], encoding="utf-8") as stream:
        refusal = _footprint_refusal(json.load(stream), spec)
    if refusal:
        shutil.rmtree(folder, ignore_errors=True)  # never published: no row points at it
        jobs.finish_job(job_id, ok=False, detail=refusal["code"])
        raise HTTPException(409, refusal)
    binding = {"spec_id": spec_id, "revision": specifications.revision(spec.as_dict()),
               "snapshot": spec.as_dict(), "robot_id": body.get("robot_id")}
    store.atomic_write(os.path.join(os.path.dirname(result["scene_path"]), "robot-spec.json"),
                       json.dumps(binding, allow_nan=False).encode())
    twin_id = db.insert(conn, "twins", mesh_id=mesh_id,
                        scene_url=result["scene_path"],
                        navmesh_url=result["navmesh_path"],
                        profile_provenance_json=(json.dumps(profile_provenance)
                                                 if profile_provenance else None))
    jobs.finish_job(job_id, detail=twin_id)
    return {"twin_id": twin_id, "job_id": job_id,
            "scene_url": result["scene_path"],
            "object_count": result["object_count"],
            # The twin is only meaningful for a specific robot; say which one it was built
            # for rather than letting the caller assume the old hardcoded default.
            "profile": result["profile"],
            # "named" when the operator chose the environment; otherwise the profiler chain's
            # record of which provider answered and why any fallback happened.
            "profile_source": "named" if body.get("environment") else "profiler",
            "profile_provenance": profile_provenance,
            "floor_z": result["floor_z"],
            "counts": result["counts"],
            # Which grid the twin uses, and the scan density that decided it.
            "resolution": result["resolution"]}


# ------------------------------------------------------------------------------ plan

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}


def _provider_refusal(workload: str, status: str, provenance: dict) -> HTTPException:
    """The chain ended without an accepted result: cancelled (409) or exhausted (502)."""
    if status == "cancelled":
        return HTTPException(409, {"code": "cancelled", "workload": workload,
                                   "error": "The request was cancelled.",
                                   "provenance": provenance})
    return HTTPException(502, {"code": "provider_exhausted", "workload": workload,
                               "error": "No configured provider produced a usable result: "
                                        + (provenance.get("fallback_reason") or status),
                               "provenance": provenance})


def _plan_fields(result) -> dict:
    """What a caller needs to know about a plan: status, graph and who produced it."""
    attempts = result.provenance["attempts"]
    provider = result.provenance["provider"] or (attempts[-1]["provider"] if attempts else None)
    location = result.provenance["location"] or (
        providers.LOCATIONS.get(provider) if provider else None)
    return {"status": result.status, "graph_json": json.dumps(result.graph or EMPTY_GRAPH),
            "provider": provider, "location": location,
            # Kept for older clients: true whenever the answer did not come from a remote
            # service. `location` says where it actually ran ("orchestrator" or "remote").
            "on_device": location != "remote",
            "problems": result.assessment.problems if result.assessment else [],
            "provenance": result.provenance}


def _run_planner(text: str, lang: str, scene_labels, request_id):
    chain = providers.chain("planner")
    with providers.track(request_id) as cancel:
        return plan_instruction(text, lang, scene_labels, chain, limits=providers.limits(),
                                budget=providers.budget(), cancel=cancel,
                                request_id=request_id)


@app.post("/plan/preview")
@scoped
@idempotent
def plan_preview(body: dict = Body(...)):
    """Plan an instruction without a scanned room, for the landing page playground.

    Same provider chain and contract as /plan, but nothing is stored and no job is recorded:
    there is no twin for it to belong to. `labels` optionally stands in for a scene's objects
    (one entry per object). A plan that needs clarification or recognises no action is still a
    200 here, with `status` and `problems` saying why; only a failed or cancelled chain is not.
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    labels = body.get("labels")
    if labels is not None and (not isinstance(labels, list)
                               or not all(isinstance(label, str) for label in labels)):
        raise HTTPException(400, "labels must be a list of strings")
    result = _run_planner(text, body.get("lang", "en"), labels or None, body.get("request_id"))
    if result.status in ("failed", "cancelled"):
        raise _provider_refusal("planner", result.status, result.provenance)
    return _plan_fields(result)


@app.post("/plan")
@scoped
@idempotent
def plan(body: dict = Body(...)):
    """Turn an instruction into a task graph for this twin, through the planner chain.

    Only a plan whose every step is supported and whose every target names exactly one object in
    this scan is stored. A plan that needs clarification (a missing, absent or shared target)
    or recognises no action is a 422 that says why; nothing is stored and nothing is guessed.
    """
    twin_id, text = body["twin_id"], body["text"]
    lang = body.get("lang", "en")
    conn = _db()
    twin = _need(conn, "twins", twin_id)
    providers.chain("planner")  # refuse a bad setting before the job exists
    job_id = jobs.create_job("plan")

    objects = _objects_for(conn, twin["mesh_id"])
    result = _run_planner(text, lang, [o["label"] for o in objects], body.get("request_id"))
    fields = _plan_fields(result)
    if result.status in ("failed", "cancelled"):
        jobs.finish_job(job_id, ok=False, detail=result.status)
        raise _provider_refusal("planner", result.status, result.provenance)
    if result.status != "planned":
        jobs.finish_job(job_id, ok=False, detail=result.status)
        code = ("clarification_needed" if result.status == "clarification_needed"
                else "unsupported_instruction")
        message = (fields["problems"][0] if fields["problems"]
                   else "No supported action was recognised in that instruction.")
        detail = {"code": code, "error": message, **fields}
        if result.assessment is not None:
            detail.update(unresolved=result.assessment.unresolved,
                          choices=result.assessment.choices)
        raise HTTPException(422, detail)

    graph_id = db.insert(conn, "task_graphs", twin_id=twin_id, source_text=text,
                         lang=lang, provider=fields["provider"],
                         graph_json=fields["graph_json"],
                         provenance_json=json.dumps(result.provenance))
    jobs.finish_job(job_id, detail=graph_id)
    return {"task_graph_id": graph_id, "job_id": job_id, **fields}


@app.post("/providers/requests/{request_id}/cancel")
def cancel_provider_request(request_id: str):
    """Stop a running planner or profiler chain between attempts. A request already sent to a
    remote provider finishes or times out; its reply is then discarded."""
    if not providers.cancel(request_id):
        raise HTTPException(404, {"code": "not_running",
                                  "error": "No provider request with this id is running."})
    return {"request_id": request_id, "cancelled": True}


def _load_navmesh(twin: dict) -> dict:
    with open(twin["navmesh_url"]) as f:
        return json.load(f)


def _navigation_points(conn, twin: dict, graph: TaskGraph, standable: dict) -> list:
    """An approach point for every navigate_to step, in order; a refusal instead of a guess.

    Each target must name exactly one object in this scan. A label that is absent, or shared by
    several objects, is a 422 the operator resolves on the map — a plan never quietly becomes a
    drive somewhere else. The point is picked on the inflated grid, so "go to the table"
    resolves to a spot the robot fits in rather than one flush against the table's edge.
    """
    by_label: dict = {}
    for obj in _objects_for(conn, twin["mesh_id"]):
        by_label.setdefault(obj["label"], []).append(obj)
    points = []
    for i, node in enumerate(graph.nodes):
        if node.action != "navigate_to":
            continue
        matches = by_label.get(node.target, [])
        if not matches:
            raise _refused("target_not_in_scene",
                           f"Step {i} goes to {node.target!r}, which is not in this scan. Place "
                           "the goal on the map or plan the instruction again.")
        if len(matches) > 1:
            raise HTTPException(422, {
                "code": "ambiguous_target", "nearest": None,
                "error": f"This scan has {len(matches)} objects labelled {node.target!r}; "
                         "choose one on the map.",
                "candidates": [{"object_id": obj["id"],
                                "centre": [round((obj["bbox3d"][0] + obj["bbox3d"][3]) / 2, 3),
                                           round((obj["bbox3d"][1] + obj["bbox3d"][4]) / 2, 3)]}
                               for obj in matches]})
        xmin, ymin, _, xmax, ymax, _ = matches[0]["bbox3d"]
        point = nearest_free(standable, (xmin + xmax) / 2, (ymin + ymax) / 2)
        if point is None:
            raise _refused("goal_blocked", f"There is nowhere the robot fits beside the "
                                           f"{node.target}.")
        points.append(point)
    return points


def _goal_for(conn, twin: dict, graph: TaskGraph):
    """Where a policy trained without a mission drives to: beside the plan's first navigation
    target (`_navigation_points` refuses an absent or shared target).

    With no plan at all, the legacy no-mission gate aims at the middle of the room — the only
    case where ScanBorn chooses a goal itself, and the response of /train says so.
    """
    navmesh = _load_navmesh(twin)
    standable = inflate(navmesh)
    if graph.nodes:
        points = _navigation_points(conn, twin, graph, standable)
        if not points:
            raise _refused("no_navigation_step",
                           "This plan has no navigate_to step, so it names no goal.")
        return points[0], navmesh
    centre_x = navmesh["origin"][0] + navmesh["width"] * navmesh["cell"] / 2
    centre_y = navmesh["origin"][1] + navmesh["height"] * navmesh["cell"] / 2
    return nearest_free(standable, centre_x, centre_y), navmesh


# -------------------------------------------------------------------------- missions

Coord = Annotated[float, Field(allow_inf_nan=False)]
Point = Annotated[list[Coord], Field(min_length=2, max_length=2)]
Pose = Annotated[list[Coord], Field(min_length=2, max_length=3)]


class MissionRequest(BaseModel):
    """Where the robot starts, where it should end up, and what it must pass on the way."""

    twin_id: str
    goal: Point | None = Field(
        None, description="[x, y] in metres, map frame. Give this or task_graph_id.")
    task_graph_id: str | None = Field(
        None, description="Take the route from a plan: its last navigate_to target is the "
                          "goal and earlier ones are via points, in order. Every step must be "
                          "navigate_to, and every target must name one object in this scan.")
    start: Pose | None = Field(
        None, description="[x, y] or [x, y, heading in radians]. Defaults to the scan's home "
                          "pose; without one it is required.")
    via: list[Point] = Field(
        default_factory=list, max_length=16, description="Points to pass through, in order.")


# Clearance fields keyed by navmesh file and modification time, so repeated clicks on one twin
# skip the distance transform. Cached navmeshes are shared: callers must not mutate them.
_FIELD_CACHE: dict = {}
_FIELD_CACHE_SIZE = 8


def _navmesh_and_field(twin: dict) -> tuple:
    path = twin["navmesh_url"]
    key = (path, os.path.getmtime(path))
    cached = _FIELD_CACHE.get(key)
    if cached is None:
        navmesh = _load_navmesh(twin)
        cached = (navmesh, clearance_field(navmesh))
        while len(_FIELD_CACHE) >= _FIELD_CACHE_SIZE:
            _FIELD_CACHE.pop(next(iter(_FIELD_CACHE), None), None)
        _FIELD_CACHE[key] = cached
    return cached


def _home_pose(conn, twin: dict):
    """The scan's home pose [x, y, heading] from its meta.json, or None.

    Phase 5's capture writes it when a scan starts on the home marker. Read by path rather
    than through store.scan_dir, which would create the directory as a side effect of a GET.
    """
    mesh = db.get(conn, "meshes", twin["mesh_id"])
    if mesh is None:
        return None
    path = os.path.join(store.data_root(), "scans", mesh["scan_id"], "meta.json")
    try:
        with open(path, encoding="utf-8") as f:
            pose = json.load(f).get("home_pose")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(pose, list) and len(pose) == 3 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in pose):
        return [float(v) for v in pose]
    return None


def _refused(code: str, message: str, nearest=None) -> HTTPException:
    """A mission request that cannot be met, shaped like MissionError.as_detail()."""
    return HTTPException(422, {"error": message, "code": code, "nearest": nearest})


def _mission_out(row: dict) -> dict:
    return {
        "mission_id": row["id"], "twin_id": row["twin_id"],
        "task_graph_id": row["task_graph_id"], "status": row["status"],
        "stale_reason": row["stale_reason"],
        "start": json.loads(row["start_json"]), "goal": json.loads(row["goal_json"]),
        "via": json.loads(row["via_json"]), "waypoints": json.loads(row["waypoints_json"]),
        "path": json.loads(row["path_json"]),
        "length_m": row["length_m"], "min_clearance_m": row["min_clearance_m"],
        "created_at": row["created_at"],
    }


def _stale_detail(mission: dict) -> dict:
    return {"error": "The room changed along this mission's path. Generate a twin from the "
                     "new scan and plan the mission again.",
            "code": "mission_stale", "mission_id": mission["id"],
            "stale_reason": mission["stale_reason"]}


def _record_run(conn, mission_id: str, policy_id, deployment_id, kind: str, reached: bool,
                trace, metrics: dict) -> str:
    return db.insert(conn, "runs", mission_id=mission_id, policy_id=policy_id,
                     deployment_id=deployment_id, kind=kind, reached=int(bool(reached)),
                     trace_json=json.dumps([[round(float(x), 3), round(float(y), 3)]
                                            for x, y in trace]),
                     metrics_json=json.dumps(metrics))


@app.get("/twins/{twin_id}/navmesh")
@scoped
def twin_navmesh(twin_id: str):
    """The twin's occupancy grid for the mission map.

    `cells_b64` holds FREE 0 / BLOCKED 1 / UNKNOWN 2 per cell; `band_b64` marks free cells the
    robot still does not fit in (closer than its radius to something). Both are uint8,
    base64, row-major, with row 0 at the minimum y.
    """
    conn = _db()
    twin = _need(conn, "twins", twin_id)
    navmesh, field = _navmesh_and_field(twin)
    grid = as_array(navmesh)
    radius = robot_radius(navmesh)
    band = (grid == FREE) & ~traversable(field, radius)
    return {"twin_id": twin_id, "version": navmesh.get("version", 1),
            "origin": navmesh["origin"], "cell": navmesh["cell"],
            "width": navmesh["width"], "height": navmesh["height"],
            "floor_z": navmesh.get("floor_z"),
            "counts": navmesh.get("counts") or counts(grid),
            "profile": navmesh.get("profile"), "robot_radius": radius,
            "resolution": navmesh.get("resolution"),
            "home_pose": _home_pose(conn, twin),
            "encoding": "uint8, base64, row-major, row 0 at minimum y",
            "cells_b64": encode_cells(grid), "band_b64": encode_cells(band)}


@app.post("/missions")
@scoped
def create_mission(request: MissionRequest):
    """Plan a mission on a twin and store it.

    Not recorded as a job: the map plans on every click, and a job per click would bury the
    live feed. Missions are immutable — a new plan is a new row — so a policy always points at
    the exact route it was trained on. Refusals are 422 with a `code` and, where one exists,
    the `nearest` point that would work.
    """
    if (request.goal is None) == (request.task_graph_id is None):
        raise _refused("bad_request", "Give exactly one of goal or task_graph_id.")
    conn = _db()
    twin = _need(conn, "twins", request.twin_id)
    navmesh, field = _navmesh_and_field(twin)

    goal = request.goal
    via = request.via
    if request.task_graph_id is not None:
        graph_row = _need(conn, "task_graphs", request.task_graph_id)
        if graph_row["twin_id"] != twin["id"]:
            raise _refused("bad_request", "That plan was made for a different twin.")
        if request.via:
            raise _refused("bad_request", "Give via points on the map or in the plan, not both.")
        graph = TaskGraph.from_json(graph_row["graph_json"])
        other = sorted({node.action for node in graph.nodes if node.action != "navigate_to"})
        if other:
            # Dropping them would drive a different mission than the one the operator asked for.
            raise HTTPException(422, {
                "code": "unsupported_step", "nearest": None, "actions": other,
                "error": "The rover can only drive to places; this plan also asks it to "
                         f"{', '.join(other)}. Plan only the driving, or place the goal on "
                         "the map."})
        points = _navigation_points(conn, twin, graph, inflate(navmesh))
        if not points:
            raise _refused("no_navigation_step",
                           "This plan has no navigate_to step, so it names no goal.")
        goal = [float(points[-1][0]), float(points[-1][1])]
        via = [[float(x), float(y)] for x, y in points[:-1]]
    assert goal is not None

    start = request.start or _home_pose(conn, twin)
    if start is None:
        centre = (navmesh["origin"][0] + navmesh["width"] * navmesh["cell"] / 2,
                  navmesh["origin"][1] + navmesh["height"] * navmesh["cell"] / 2)
        suggestion = nearest_valid(navmesh, traversable(field, robot_radius(navmesh)), *centre)
        raise _refused("no_start", "No start was given and this scan has no home pose. "
                                   "Place the start on the map.",
                       [round(v, 4) for v in suggestion] if suggestion else None)

    try:
        planned = plan_mission(navmesh, start, goal, via, field=field)
    except MissionError as exc:
        raise HTTPException(422, exc.as_detail())

    mission_id = db.insert(
        conn, "missions", twin_id=twin["id"], task_graph_id=request.task_graph_id,
        start_json=json.dumps(planned["start"]), goal_json=json.dumps(planned["goal"]),
        via_json=json.dumps(planned["via"]), waypoints_json=json.dumps(planned["waypoints"]),
        path_json=json.dumps(planned["path"]), length_m=planned["length_m"],
        min_clearance_m=planned["min_clearance_m"])
    return {**_mission_out(_need(conn, "missions", mission_id)), "plan_ms": planned["plan_ms"]}


@app.get("/missions/{mission_id}")
@scoped
def get_mission(mission_id: str):
    return _mission_out(_need(_db(), "missions", mission_id))


# ------------------------------------------------------------------------ robot spec

def _spec(name: str | None):
    """A robot spec by file name, or the orchestrator's 400/404 when there is none."""
    try:
        return load_spec(str(name or DEFAULT_SPEC))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/robot-specs/{name}")
@scoped
def robot_spec(name: str):
    """robot/specs/<name>.yaml. `source` says whether its numbers are measured or placeholders."""
    return _spec(name).as_dict()


def _footprint_refusal(navmesh: dict, spec) -> dict | None:
    """A twin planned for a smaller robot has routes this one does not fit through (plan §4.5)."""
    radius = robot_radius(navmesh)
    if spec.footprint_radius_m <= radius + EPS:
        return None
    return {"error": f"This twin was built for a robot {radius:.2f} m in radius; {spec.name} "
                     f"needs {spec.footprint_radius_m:.2f} m. Generate the twin with a profile "
                     "for a larger robot.",
            "code": "footprint_too_large", "twin_robot_radius_m": radius,
            "footprint_radius_m": spec.footprint_radius_m}


# ----------------------------------------------------------------------------- train

@app.post("/train")
@scoped
@idempotent
def train(body: dict = Body(...)):
    """Behaviour-clone the controller in the rover model and gate the result.

    With a mission, the policy must reach the goal in at least MISSION_GATE of the noisy runs of
    that mission; without one, SIM_GATE of random starts. A 409 carries the numbers, including
    how the failed runs ended. Noise comes from the robot spec, which labels it as assumed until
    calibration measures it. Optional robot_id selects its accepted calibration revision;
    robot_spec is the stable specification ID, not its display name.
    """
    twin_id = body["twin_id"]
    conn = _db()
    twin = _need(conn, "twins", twin_id)
    graph_id = body.get("task_graph_id")
    mission = _need(conn, "missions", body["mission_id"]) if body.get("mission_id") else None
    if mission is not None:
        if mission["twin_id"] != twin_id:
            raise HTTPException(400, f"mission {mission['id']!r} belongs to another twin")
        if mission["status"] == "stale":
            raise HTTPException(409, _stale_detail(mission))
    spec, spec_id = specifications.resolve(
        conn, body.get("robot_id"), body.get("robot_spec"), DEFAULT_SPEC)
    job_id = jobs.create_job("train")

    graph = TaskGraph()
    if graph_id:
        graph_row = _need(conn, "task_graphs", graph_id)
        if graph_row["twin_id"] != twin_id:
            jobs.finish_job(job_id, ok=False, detail="graph belongs to another twin")
            raise HTTPException(400, "task graph belongs to another twin")
        graph = TaskGraph.from_json(graph_row["graph_json"])

    if mission is not None:
        navmesh = _load_navmesh(twin)
        goal = tuple(json.loads(mission["goal_json"]))
    else:
        goal, navmesh = _goal_for(conn, twin, graph)
    if goal is None:
        jobs.finish_job(job_id, ok=False, detail="navmesh fully blocked")
        raise HTTPException(400, "navmesh has no traversable cell to train against")
    refusal = _footprint_refusal(navmesh, spec)
    if refusal:
        jobs.finish_job(job_id, ok=False, detail=refusal["code"])
        raise HTTPException(409, refusal)

    route = _mission_out(mission) if mission is not None else None
    demos = body.get("demonstrations") or (
        record_demos(navmesh, goal, spec, n=10, path=route["path"], start=route["start"])
        if route is not None else record_demos(navmesh, goal, spec, n=10))
    policy = finetune_bc(make_baseline(OBS_DIM, ACT_DIM), demos)

    trace: list = []
    if route is not None:
        metrics = evaluate_mission(policy, navmesh, route, spec)
        trace = metrics.pop("trace")
    else:
        metrics = evaluate(policy, navmesh, goal, spec, episodes=20)

    if not metrics["passed"]:
        if mission is not None:
            _record_run(conn, mission["id"], None, None, "sim", False, trace, metrics)
        jobs.finish_job(job_id, ok=False, detail=metrics)
        # Section 11.5: an unvalidated policy must never reach hardware.
        if mission is not None:
            outcomes = metrics["outcomes"]
            detail = {"error": "policy failed the mission gate in simulation",
                      "code": "mission_gate", "success_rate": metrics["success_rate"],
                      "required": MISSION_GATE, "reached": metrics["reached"],
                      "runs": metrics["runs"],
                      "stopped_off_goal": outcomes["stopped_off_goal"],
                      "collided": outcomes["collided"], "timed_out": outcomes["timeout"],
                      "median_miss_m": metrics["median_miss_m"],
                      "median_drift_m": metrics["median_drift_m"],
                      "noise": spec.noise.source}
        else:
            detail = {"error": "policy failed sim validation", "code": "sim_gate",
                      "sim_success_rate": metrics["success_rate"], "required": SIM_GATE}
        raise HTTPException(409, detail)

    # One folder per policy: a second training run on the same twin must not overwrite the
    # checkpoint an earlier policy row points at.
    policy_id = db.new_id()
    folder = artifacts_dir("policies", twin_id, policy_id)
    policy_path = policy.save(os.path.join(folder, "policy.npz"))
    # What the weights read and command. /optimize copies it into the export manifest; a policy
    # without it predates the rover model and /deploy refuses it.
    with open(os.path.join(folder, "policy_meta.json"), "w", encoding="utf-8") as f:
        json.dump({**policy_manifest(spec), "spec_id": spec_id,
                   "spec_revision": specifications.revision(spec.as_dict()),
                   "robot_id": body.get("robot_id")}, f, indent=2)
    db.insert(conn, "policies", id=policy_id, twin_id=twin_id, task_graph_id=graph_id,
              base_checkpoint="zero-baseline", finetuned_ckpt_url=policy_path,
              sim_success_rate=metrics["success_rate"],
              mission_id=mission["id"] if mission else None)
    if mission is not None:
        _record_run(conn, mission["id"], policy_id, None, "sim", True, trace, metrics)
    jobs.finish_job(job_id, detail=policy_id)
    return {"policy_id": policy_id, "job_id": job_id,
            "sim_success_rate": metrics["success_rate"],
            "episodes": metrics.get("runs", metrics.get("episodes")),
            "robot_spec": spec.as_dict(),
            "spec_id": spec_id, "spec_revision": specifications.revision(spec.as_dict()),
            "mission_id": mission["id"] if mission else None,
            "mission_metrics": metrics if mission else None,
            "mission_trace": trace}


# -------------------------------------------------------------------------- optimize

@app.post("/optimize")
@scoped
@idempotent
def optimize(body: dict = Body(...)):
    policy_id = body["policy_id"]
    # Blank means "where this bundle is really built": host-cpu locally, an AI Hub device there.
    device_label = body.get("device_label") or None
    conn = _db()
    policy_row = _need(conn, "policies", policy_id)
    # SCANBORN_EXPORT names an optional experiment; an AI Hub token alone selects nothing.
    export_provider = providers.require("export")

    checkpoint = policy_row["finetuned_ckpt_url"]
    meta_path = os.path.join(os.path.dirname(checkpoint), "policy_meta.json")
    meta = None
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    # Refusals that need no export come first, so a refused request writes no files.
    twin = _need(conn, "twins", policy_row["twin_id"])
    mission = (db.get(conn, "missions", policy_row["mission_id"])
               if policy_row.get("mission_id") else None)
    if mission is not None and mission["status"] == "stale":
        raise HTTPException(409, _stale_detail(mission))
    snapshot = (meta or {}).get("robot_spec_snapshot")
    if snapshot is None:
        raise HTTPException(409, "Train again to capture the robot specification before export")
    job_id = jobs.create_job("optimize")
    try:
        manifest = export_model(checkpoint, device_label,
                                out_dir=artifacts_dir("artifacts", policy_id, db.new_id()),
                                meta=meta, provider=export_provider)
    except ExportConfigurationError as exc:
        jobs.finish_job(job_id, ok=False, detail=str(exc))
        raise HTTPException(503, {"code": "provider_configuration", "workload": "export",
                                  "provider": export_provider, "error": str(exc)})
    refusal = artifact_format.runtime_refusal(manifest["artifact_path"])
    if refusal:
        jobs.finish_job(job_id, ok=False, detail=refusal)
        raise HTTPException(409, {"code": "unsupported_runtime", "error": refusal})
    spec = spec_from_dict(snapshot)
    exported = load_int8_policy(manifest["artifact_path"])
    navmesh = _load_navmesh(twin)
    if mission is not None:
        validation = evaluate_mission(exported, navmesh, _mission_out(mission), spec)
    else:
        graph_row = (db.get(conn, "task_graphs", policy_row["task_graph_id"])
                     if policy_row["task_graph_id"] else None)
        graph = TaskGraph.from_json(graph_row["graph_json"]) if graph_row else TaskGraph()
        goal, _ = _goal_for(conn, twin, graph)
        validation = evaluate(exported, navmesh, goal, spec, episodes=20)
    validation.pop("trace", None)
    if not validation["passed"]:
        jobs.finish_job(job_id, ok=False, detail=validation)
        raise HTTPException(409, {"code": "export_gate",
                                  "error": "Exported int8 policy failed validation",
                                  "validation": validation})
    record = artifact_validation.validation_record(
        manifest["artifact_path"], twin["navmesh_url"], spec,
        mission["id"] if mission else None, validation)
    record.update(robot_id=(meta or {}).get("robot_id"), spec_id=(meta or {}).get("spec_id"),
                  spec_revision=specifications.revision(spec.as_dict()))
    store.atomic_write(os.path.join(os.path.dirname(manifest["artifact_path"]), "validation.json"),
                       json.dumps(record, allow_nan=False).encode())
    artifact_id = db.insert(conn, "artifacts", policy_id=policy_id,
                            device_label=manifest["device_label"], precision=manifest["precision"],
                            op_coverage_pct=manifest["op_coverage"],
                            est_latency_ms=manifest["est_latency_ms"],
                            artifact_url=manifest["artifact_path"])
    db.insert(conn, "artifact_validations", artifact_id=artifact_id,
              validation_json=json.dumps(record))
    jobs.finish_job(job_id, detail=artifact_id)
    return {"artifact_id": artifact_id, "job_id": job_id,
            # None on the local path — the dashboard renders "not profiled" rather than
            # letting a host-CPU bundle read as an NPU-compiled one.
            "op_coverage": manifest["op_coverage"],
            "est_latency": manifest["est_latency_ms"],
            "latency_source": manifest.get("latency_source", "host-cpu"),
            "backend": manifest["backend"], "validation": record,
            # What the bytes are and what runs them; experiments are never deployable.
            "format": manifest["format"], "runtime": manifest["runtime"],
            "export_provider": manifest["export_provider"],
            "experiments": manifest["experiments"]}


BENCHMARK_DIR = os.path.join(os.path.dirname(__file__), "..", "benchmarks")


@app.get("/benchmarks")
@scoped
def benchmarks():
    """Measured numbers for the dashboard's Telemetry panel.

    `models` is written by `python -m deployment.aihub_export.profile_models` and is empty
    until that has been run: an empty payload means "not profiled yet", never zero. `phone`
    is benchmarks/phone.json, the on-device model measured on the project's phone, or None.
    """
    def load(name: str):
        path = os.path.join(BENCHMARK_DIR, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    summary = load("summary.json")
    result: dict = ({**summary, "profiled": True} if summary
                    else {"models": [], "profiled": False})
    result["phone"] = load("phone.json")
    return result


# ---------------------------------------------------------------------------- robots

class RobotRequest(BaseModel):
    """A protocol rover to register: an ESP32 at `address`, or a fake rover run in-process."""

    kind: Literal["rover", "fake_rover"]
    address: str | None = Field(
        None, description="http://host[:port] of an ESP32 rover. Not used for fake_rover.")
    name: str | None = None
    time_scale: float = Field(
        1.0, ge=1.0, le=50.0,
        description="fake_rover only: run its model this many times faster than real time.")


def _rover_address(robot_row: dict) -> str:
    config = json.loads(robot_row["config_json"] or "{}")
    if robot_row["kind"] != "rover" or not config.get("address"):
        raise HTTPException(400, {"error": f"robot {robot_row['id']!r} is not a protocol rover",
                                  "code": "not_a_rover"})
    return str(config["address"])


def _rover_refusal(exc: Exception) -> HTTPException:
    """A rover's refusal, or its silence, as the orchestrator's answer."""
    if isinstance(exc, RoverError):
        code = exc.status_code if exc.status_code in (400, 404, 409, 410) else 502
        return HTTPException(code, {"error": str(exc), "code": "rover_refused",
                                    "rover": exc.detail})
    return HTTPException(502, {"error": f"the rover did not answer ({exc.__class__.__name__})",
                               "code": "rover_unreachable"})


def _rover_call(robot_row: dict, action):
    """Run `action(RoverRobot)` against a registered rover."""
    rover = RoverRobot(_rover_address(robot_row))
    try:
        return action(rover)
    except (RoverError, httpx.HTTPError) as exc:
        raise _rover_refusal(exc)
    finally:
        rover.close()


@app.post("/robots")
@scoped
def register_robot(request: RobotRequest):
    """Register a protocol rover and return what it reports about itself.

    `fake_rover` starts a fake rover inside this orchestrator (robot/adapters/fake_rover.py): the
    real protocol over localhost sockets with the rover model inside, so a drive can be shown
    without hardware. Its status says `"fake": true`, and so does every result it produces.
    """
    fake: FakeRover | None = None
    if request.kind == "fake_rover":
        fake = FakeRover(load_spec(DEFAULT_SPEC), seed=len(FAKE_ROVERS),
                         time_scale=request.time_scale).start()
        address = fake.address
    elif request.address:
        address = request.address.rstrip("/")
    else:
        raise HTTPException(422, {"error": "address is required for a rover",
                                  "code": "bad_request"})

    rover = RoverRobot(address)
    try:
        status = rover.status()
    except (RoverError, httpx.HTTPError):
        if fake is not None:
            fake.close()
        raise HTTPException(502, {"error": f"no rover answered at {address}",
                                  "code": "rover_unreachable"})
    finally:
        rover.close()

    conn = _db()
    robot_id = db.insert(conn, "robots", kind="rover", config_json=json.dumps(
        {"address": address, "fake": fake is not None, "name": request.name,
         "firmware": status.get("firmware")}))
    if fake is not None:
        FAKE_ROVERS[robot_id] = fake
    return {"robot_id": robot_id, "kind": "rover", "fake": fake is not None,
            "address": address, "status": status}


@app.get("/robots/{robot_id}/status")
@scoped
def robot_status(robot_id: str):
    return _rover_call(_need(_db(), "robots", robot_id), lambda rover: rover.status())


@app.post("/robots/{robot_id}/estop")
@scoped
def robot_estop(robot_id: str):
    """E-STOP a rover. It latches on the rover until reset; a live drive on it ends as `estop`."""
    robot_row = _need(_db(), "robots", robot_id)
    session = DEPLOYMENTS.active_for_robot(robot_id)
    if session is not None:
        DEPLOYMENTS.estop(session.deployment_id, reason="dashboard")
        return {"state": "estop", "reason": "dashboard", "deployment_id": session.deployment_id}
    return _rover_call(robot_row, lambda rover: rover.estop("dashboard"))


@app.post("/robots/{robot_id}/reset")
@scoped
def robot_reset(robot_id: str):
    """Clear a latched E-STOP or error. The rover refuses while it is driving."""
    return _rover_call(_need(_db(), "robots", robot_id), lambda rover: rover.reset())


@app.post("/robots/{robot_id}/calibrate")
@scoped
def robot_calibrate(robot_id: str, body: dict = Body(...)):
    """Run a calibration move (`straight` or `spin`). Turning it into spec values is Phase 6."""
    mode = str(body.get("mode") or "")
    return _rover_call(_need(_db(), "robots", robot_id), lambda rover: rover.calibrate(mode))


# ---------------------------------------------------------------------------- deploy

def _start_pose(navmesh: dict, goal):
    """A cell the robot fits in, as near as possible to DEPLOY_START_DISTANCE from the goal.

    Without a mission nobody chose a start, so pick a fair one: far enough that the drive goes
    somewhere, not so far that odometry drift decides the result. (It used to be the farthest
    cell in the room, which on the rover model measures drift more than the policy.)
    """
    cells = free_cells(inflate(navmesh))
    return min(cells, key=lambda c: abs(math.dist(c, goal) - DEPLOY_START_DISTANCE),
               default=None)


def _artifact_manifest(artifact: dict) -> dict:
    path = os.path.join(os.path.dirname(artifact["artifact_url"]), "manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _sim_trace_for(conn, policy_id: str) -> list:
    """The median simulated run training recorded for this policy: what a drive is compared to."""
    row = conn.execute(
        "SELECT trace_json FROM runs WHERE policy_id = ? AND kind = 'sim' "
        "AND deployment_id IS NULL ORDER BY rowid DESC LIMIT 1", (policy_id,)).fetchone()
    return json.loads(row["trace_json"]) if row and row["trace_json"] else []


def _record_rover_drive(mission: dict, policy_id: str, job_id: str, fake: bool):
    """A rover session's `on_finish`: the deployment's final state and its `runs` row."""
    goal = json.loads(mission["goal_json"])

    def record(session) -> None:
        conn = _db()
        try:
            reached = session.state == "done"
            trace = [p for p in session.trace if None not in p]
            metrics: dict = {"state": session.state, "reason": session.reason,
                             "frames": session.frames, "fake": fake,
                             "pose_samples": session.samples,
                             "duration_s": round(time.monotonic() - session.started_at, 2)}
            if trace and session.sim_trace:
                metrics["deviation_from_sim_m"] = round(rms_deviation(trace, session.sim_trace), 4)
            if trace:
                # Where the rover believed it stopped: all a real rover can report.
                metrics["reported_miss_m"] = round(math.dist(trace[-1], goal), 4)
            if fake and session.truth:
                # Known only because the rover is fake: where it really stopped.
                metrics["true_miss_m"] = round(math.dist(session.truth[-1], goal), 4)
            db.update(conn, "deployments", session.deployment_id, status=session.state)
            _record_run(conn, mission["id"], policy_id, session.deployment_id,
                        "fake_rover" if fake else "rover", reached, trace, metrics)
            jobs.finish_job(job_id, ok=reached, detail=session.state)
        except Exception as exc:
            conn.rollback()
            db.update(conn, "deployments", session.deployment_id, status="recording_failed")
            jobs.finish_job(job_id, ok=False, detail={
                "error": "drive result recording failed", "reason": str(exc)})
            raise
        finally:
            conn.close()
            session.robot.close()

    return record


@serialize_start
def _deploy_rover(conn, artifact: dict, policy_row: dict, mission, navmesh: dict, spec,
                  robot_row) -> dict:
    """Load the mission onto a protocol rover, start it, and return while it drives."""
    if robot_row is None:
        raise HTTPException(400, {"error": "A rover deployment needs a robot_id from POST /robots.",
                                  "code": "robot_required"})
    if mission is None:
        raise HTTPException(409, {"error": "A rover drives a mission from where it was placed: "
                                           "train the policy on a mission first.",
                                  "code": "mission_required"})
    address = _rover_address(robot_row)
    busy = DEPLOYMENTS.active_for_robot(robot_row["id"])
    if busy is not None:
        raise HTTPException(409, {"error": "This rover is already driving.",
                                  "code": "robot_busy", "deployment_id": busy.deployment_id})
    route = _mission_out(mission)
    if len(route["path"]) > MAX_PATH_POINTS:
        raise HTTPException(409, {"error": f"This route has {len(route['path'])} path points; a "
                                           f"rover takes at most {MAX_PATH_POINTS}.",
                                  "code": "route_too_long"})

    config = json.loads(robot_row["config_json"] or "{}")
    fake = bool(config.get("fake"))
    job_id = jobs.create_job("deploy")
    deployment_id = db.insert(conn, "deployments", artifact_id=artifact["id"],
                              robot_id=robot_row["id"], status="running")
    robot = RoverRobot(address)
    port = DEPLOYMENTS.ensure_listening()
    session = DEPLOYMENTS.open(deployment_id, mission["id"], robot,
                               sim_trace=_sim_trace_for(conn, policy_row["id"]),
                               on_finish=_record_rover_drive(mission, policy_row["id"], job_id,
                                                             fake),
                               robot_id=robot_row["id"])
    try:
        status = robot.status()
        if "config_readback_v1" in status.get("capabilities", []):
            configuration = robot.configuration()["settings"]
            mismatch = [
                key for key in ("wheel_radius_m", "wheelbase_m", "counts_per_rev")
                if not math.isclose(configuration.get(key, float("nan")),
                                    getattr(spec, key), rel_tol=1e-6)]
            if mismatch:
                raise RoverError(409, {
                    "error": "Rover geometry changed after policy validation; "
                             "reconcile the robot specification and retrain",
                    "fields": mismatch})
        if fake:
            robot.set_world(navmesh)  # the fake rover's model needs the room to collide with
        robot.send_mission(mission_payload(route, load_bundle(artifact["artifact_url"]), spec,
                                           session.token, port))
        robot.start_heartbeat(session.token)
        robot.start()
    except (RoverError, httpx.HTTPError) as exc:
        session.on_finish = None  # nothing drove, so there is no run to record
        DEPLOYMENTS.finish(deployment_id, state="error", reason="start_failed")
        robot.close()
        db.update(conn, "deployments", deployment_id, status="error")
        jobs.finish_job(job_id, ok=False, detail="start_failed")
        raise _rover_refusal(exc)
    return {"deployment_id": deployment_id, "job_id": job_id, "status": "running",
            "robot_id": robot_row["id"], "reached": False, "mission_id": mission["id"],
            "fake": fake, "live": f"/ws/deployments/{deployment_id}",
            "robot_spec": {"name": spec.name, "source": spec.source}, "pose_trace": []}


@app.post("/deploy")
@scoped
@idempotent
def deploy(body: dict = Body(...)):
    artifact_id = body["artifact_id"]
    conn = _db()
    artifact = _need(conn, "artifacts", artifact_id)
    # Only bytes a ScanBorn runtime executes can drive; a float or foreign file is refused, and
    # the simulated drive below runs these same int8 bytes, never the float checkpoint.
    runtime_problem = artifact_format.runtime_refusal(artifact["artifact_url"])
    if runtime_problem:
        raise HTTPException(409, {"code": "unsupported_runtime", "error": runtime_problem})
    policy_row = _need(conn, "policies", artifact["policy_id"])
    mission = (db.get(conn, "missions", policy_row["mission_id"])
               if policy_row.get("mission_id") else None)
    if mission is not None and mission["status"] == "stale":
        # The policy was trained for a route that is no longer safe to drive.
        raise HTTPException(409, _stale_detail(mission))
    if _artifact_manifest(artifact).get("observation") != list(OBSERVATION):
        raise HTTPException(409, {
            "error": "This policy was trained before the rover model and reads a different "
                     "observation. Train it again.",
            "code": "policy_outdated"})

    twin = _need(conn, "twins", policy_row["twin_id"])
    navmesh = _load_navmesh(twin)
    spec, spec_id = specifications.resolve(
        conn, body.get("robot_id"), body.get("robot_spec"), DEFAULT_SPEC)
    refusal = _footprint_refusal(navmesh, spec)
    if refusal:
        raise HTTPException(409, refusal)

    record = artifact_validation.read_record(artifact["artifact_url"])
    if record and record.get("robot_id") and record["robot_id"] != body.get("robot_id"):
        raise HTTPException(409, {"code": "artifact_validation",
                                  "error": "Policy was validated for another robot"})
    validation_error = artifact_validation.refusal(
        artifact_validation.read_record(artifact["artifact_url"]), artifact["artifact_url"],
        twin["navmesh_url"], spec, mission["id"] if mission else None)
    if validation_error:
        raise HTTPException(409, {"code": "artifact_validation", "error": validation_error})

    robot_id, kind = body.get("robot_id"), body.get("kind", "sim")
    existing = db.get(conn, "robots", robot_id) if robot_id else None
    if (existing is not None and existing["kind"] == "rover"
            and not json.loads(existing["config_json"] or "{}").get("fake")):
        # A physical rover placed on the marker is only where the twin says if the scan was
        # registered on that marker; an import or a scan without a home pose is not.
        scan = _need(conn, "meshes", twin["mesh_id"])["scan_id"]
        frame = registration(store.read_meta(scan))
        if not frame["physically_registered"]:
            raise HTTPException(409, {"code": "scan_not_registered", "registration": frame,
                                      "error": frame["reason"]})
    if kind == "rover" or (existing is not None and existing["kind"] == "rover"):
        # A protocol rover drives the mission itself: start it and return while it drives.
        return _deploy_rover(conn, artifact, policy_row, mission, navmesh, spec, existing)
    if existing is None and kind not in ADAPTERS:
        raise HTTPException(400, f"unknown robot kind {kind!r}; "
                                 f"expected one of {sorted(ADAPTERS)}")
    if existing is not None and existing["kind"] not in ADAPTERS:
        # An older database can hold a robot whose adapter was removed (the UNO Q, Phase 7).
        raise HTTPException(400, f"robot {robot_id!r} is a {existing['kind']!r}, which has no "
                                 f"adapter; expected one of {sorted(ADAPTERS)}")
    job_id = jobs.create_job("deploy")
    if existing is None:
        robot_id = db.insert(conn, "robots", id=robot_id, kind=kind,
                             config_json=json.dumps({"source": "deploy"}))
    else:
        kind = existing["kind"]

    graph = TaskGraph()
    if policy_row["task_graph_id"]:
        raw = db.get(conn, "task_graphs", policy_row["task_graph_id"])["graph_json"]
        graph = TaskGraph.from_json(raw)

    robot = (get_robot(kind, navmesh=navmesh, spec=spec, noise=Noise.from_spec(spec),
                       seed=DEPLOY_SEED)
             if kind == "sim" else get_robot(kind))
    connected = robot.connect()
    trace, telemetry, reached = [], {}, False
    if connected:
        goal: tuple | None
        start = None
        path = None
        if mission is not None:
            route = _mission_out(mission)
            goal, start, path = tuple(route["goal"]), route["start"], route["path"]
        else:
            goal, _ = _goal_for(conn, twin, graph)
            cell = _start_pose(navmesh, goal) if goal else None
            path = plan_path(inflate(navmesh), cell, goal) if cell else None
            if cell and path:
                # Face along the route: a random heading would test turning, not the drive.
                ahead = path[1] if len(path) > 1 else path[0]
                start = (cell[0], cell[1], math.atan2(ahead[1] - cell[1], ahead[0] - cell[0]))
        if start and path and goal:
            # Drive the route under the deployed int8 policy in the rover model: the trace is
            # only evidence the twin works if the robot had to get there through the room.
            result = run_policy(robot, load_int8_policy(artifact["artifact_url"]),
                                goal, start, path, spec)
            trace = result.pop("trace", [])
            reached = result.pop("reached", False)
            telemetry = result

    status = "running" if reached else ("connected" if connected else "offline")
    deployment_id = db.insert(conn, "deployments", artifact_id=artifact_id,
                              robot_id=robot_id, status=status)
    if mission is not None and connected:
        _record_run(conn, mission["id"], policy_row["id"], deployment_id, kind, reached,
                    trace, telemetry)
    jobs.finish_job(job_id, ok=reached, detail=status)
    return {"deployment_id": deployment_id, "job_id": job_id, "status": status,
            "robot_id": robot_id, "reached": reached,
            "mission_id": mission["id"] if mission else None,
            "robot_spec": {"name": spec.name, "source": spec.source},
            "pose_trace": [[round(float(x), 3), round(float(y), 3)] for x, y in trace],
            **telemetry}


@app.get("/deployments/{deployment_id}")
@scoped
def get_deployment(deployment_id: str):
    """A deployment's state, its trace so far and, once it has ended, its recorded result."""
    conn = _db()
    row = _need(conn, "deployments", deployment_id)
    run = conn.execute("SELECT kind, reached, trace_json, metrics_json FROM runs "
                       "WHERE deployment_id = ? ORDER BY rowid DESC LIMIT 1",
                       (deployment_id,)).fetchone()
    session = DEPLOYMENTS.get(deployment_id)
    live = session is not None and not session.finished
    if session is not None:
        trace = session.snapshot()["trace"]
    else:
        trace = json.loads(run["trace_json"]) if run and run["trace_json"] else []
    result = None
    if run is not None:
        result = {"kind": run["kind"], "reached": bool(run["reached"]),
                  **json.loads(run["metrics_json"] or "{}")}
    return {"deployment_id": deployment_id, "live": live,
            "robot_id": row["robot_id"], "artifact_id": row["artifact_id"],
            "status": session.state if live and session is not None else row["status"],
            "reason": session.reason if session is not None else None,
            "trace": trace, "result": result,
            "last": session.last_frame if session else None,
            "pose_samples": session.samples if session else (result or {}).get("pose_samples", []),
            "recording_error": session.recording_error if session else None}


@app.post("/deployments/{deployment_id}/stop")
@scoped
def stop_deployment(deployment_id: str):
    """Operator stop: the rover halts and keeps its mission; the drive is recorded as stopped."""
    _need(_db(), "deployments", deployment_id)
    session = DEPLOYMENTS.get(deployment_id)
    if session is None or session.finished:
        raise HTTPException(409, {"error": "This deployment is not driving.", "code": "not_live"})
    DEPLOYMENTS.stop(deployment_id)
    return {"deployment_id": deployment_id, "status": "stopped"}


@app.websocket("/ws/deployments/{deployment_id}")
async def ws_deployment(websocket: WebSocket, deployment_id: str):
    """A rover drive live: a snapshot on connect, every telemetry frame, then the final state.

    A drive always ends (done, estop, error, stopped, or lost after its telemetry falls silent), so
    this socket always closes, even for a dashboard that stopped reading.
    """
    await websocket.accept()
    session = DEPLOYMENTS.get(deployment_id)
    if session is None:
        await websocket.send_json({"type": "missing", "deployment_id": deployment_id})
        await websocket.close()
        return
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    unsubscribe = DEPLOYMENTS.subscribe(
        deployment_id, lambda message: loop.call_soon_threadsafe(queue.put_nowait, message))
    try:
        # Subscribed before the snapshot: a drive the snapshot shows running cannot end unheard.
        snapshot = session.snapshot()
        await websocket.send_json({"type": "snapshot", **snapshot})
        while not snapshot["finished"]:
            message = await queue.get()
            await websocket.send_json(message)
            if message.get("type") == "finished":
                break
        await websocket.close()
    except (WebSocketDisconnect, RuntimeError):
        pass  # the dashboard went away; the drive carries on
    finally:
        unsubscribe()


# ------------------------------------------------------------------------------ sync

def _voxel_set(points, voxel: float = 0.05) -> set:
    if len(points) == 0:
        return set()
    keys = np.floor(np.asarray(points) / voxel).astype(np.int64)
    return {tuple(k) for k in keys}


def _corridor(navmesh: dict, path: list, reach: float) -> np.ndarray:
    """Cells within `reach` metres of the path."""
    height, width = navmesh["height"], navmesh["width"]
    on_path = np.zeros((height, width), dtype=bool)
    for x, y in densify(path, navmesh["cell"] / 2):
        row, col = cell_of(navmesh, x, y)
        if 0 <= row < height and 0 <= col < width:
            on_path[row, col] = True
    if not on_path.any():
        return on_path
    return ndimage.distance_transform_edt(~on_path) * navmesh["cell"] <= reach + EPS


def _check_missions(conn, twin: dict, new_points, new_scan_id: str) -> list:
    """Mark this twin's missions stale when a rescan puts something in their way.

    The rescan is rasterised onto the twin's own grid, so it must share the first scan's frame
    (registration arrives in Phase 5). Cells the rescan did not see keep their old state. A
    mission is stale when its path no longer clears the robot's radius on that merged grid. A
    removed obstacle never makes a route unsafe, so it marks nothing stale.
    """
    rows = conn.execute("SELECT * FROM missions WHERE twin_id = ? AND status = 'ready'",
                        (twin["id"],)).fetchall()
    if not rows or len(new_points) == 0:
        return []
    navmesh = _load_navmesh(twin)
    old = as_array(navmesh)
    if old.size == 0:
        return []
    sweep = float((navmesh.get("profile") or {}).get("robot_height", ROBOT_HEIGHT))
    new = rasterise(new_points, navmesh["origin"], navmesh["cell"], navmesh["width"],
                    navmesh["height"], estimate_floor_z(new_points), sweep)
    merged = {**navmesh, "grid": np.where(new == UNKNOWN, old, new).tolist()}
    field = clearance_field(merged)
    radius = robot_radius(navmesh)

    report = []
    for row in rows:
        path = json.loads(row["path_json"])
        corridor = _corridor(navmesh, path, radius + PREFERRED_MARGIN)
        flipped = int((corridor & (old != UNKNOWN) & (new != UNKNOWN) & (old != new)).sum())
        unverified = int((corridor & (new == UNKNOWN)).sum())
        clearance = path_clearance(merged, field, path)
        stale = clearance <= radius + EPS
        if stale:
            db.update(conn, "missions", row["id"], status="stale",
                      stale_reason=f"rescan {new_scan_id} leaves {clearance:.2f} m beside the "
                                   f"path; the robot needs more than {radius:.2f} m")
        report.append({"mission_id": row["id"], "stale": stale,
                       "min_clearance_m": round(clearance, 3),
                       "changed_cells": flipped, "unverified_cells": unverified})
    return report


@app.post("/sync")
@scoped
@idempotent
def sync(body: dict = Body(...)):
    twin_id, new_scan_id = body["twin_id"], body["new_scan_id"]
    if body.get("same_marker_confirmed") is not True:
        raise HTTPException(409, "Confirm the same physical marker, origin and "
                            "heading as the original scan before invalidating missions")
    conn = _db()
    twin = _need(conn, "twins", twin_id)
    _need(conn, "scans", new_scan_id)
    job_id = jobs.create_job("reconstruct")

    old_mesh = db.get(conn, "meshes", twin["mesh_id"])
    old_points, _ = read_ply(old_mesh["ply_url"])
    new = reconstruct(store.scan_dir(new_scan_id), mode="fast",
                      out_dir=artifacts_dir("meshes", new_scan_id, db.new_id()))
    new_points, new_colors = read_ply(new["ply_path"])

    old_voxels, new_voxels = _voxel_set(old_points), _voxel_set(new_points)
    old_labels = {o["label"] for o in _objects_for(conn, twin["mesh_id"])}
    new_labels = {o["label"] for o in segment_points(new_points, new_colors)}

    diff = {
        "added_voxels": len(new_voxels - old_voxels),
        "removed_voxels": len(old_voxels - new_voxels),
        "unchanged_voxels": len(old_voxels & new_voxels),
    }
    changed = sorted((new_labels - old_labels) | (old_labels - new_labels))
    stale_missions = _check_missions(conn, twin, new_points, new_scan_id)

    db.insert(conn, "sync_events", twin_id=twin_id, new_scan_id=new_scan_id,
              diff_summary_json=json.dumps(diff), triggered_by="manual")
    jobs.finish_job(job_id, detail=diff)
    return {"diff_summary": diff, "changed_objects": changed,
            "stale_missions": stale_missions, "job_id": job_id}


# ---------------------------------------------------------------------------- status

@app.get("/status/{job_id}")
@scoped
def status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return {"stage": job["stage"], "progress": job["progress"],
            "status": job["status"], "logs_url": job["logs_url"],
            "detail": job["detail"]}


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """Push job updates to the dashboard instead of making it poll /status.

    Sends a snapshot of every known job on connect, then one message per job
    create/finish. Stage handlers run synchronously in a threadpool, so their
    notifications arrive on another thread — hence call_soon_threadsafe.
    """
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    unsubscribe = jobs.subscribe(
        lambda job: loop.call_soon_threadsafe(queue.put_nowait, job)
    )
    try:
        for job in jobs.all_jobs():
            await websocket.send_json(job)
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
