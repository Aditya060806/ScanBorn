"""Asynchronous calibration orchestration, measured arithmetic and verified configuration apply."""
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Body, HTTPException, Query

from capture.service.store import atomic_write, data_root
from orchestrator import db, specifications
from orchestrator.idempotency import serialize_start
from orchestrator.visualization import connection, need
from robot.adapters.rover import RoverError, RoverRobot
from robot.calibration import measurement
from robot.spec import spec_from_dict

router = APIRouter()
_ACTIVE: dict = {}
_LOCK = threading.RLock()


def _save(identifier, result):
    with connection() as conn:
        db.update(conn, "calibration_sessions", identifier, status=result["status"],
                  result_json=json.dumps(result, allow_nan=False))


def _interrupt_orphan(conn, identifier):
    row = db.get(conn, "calibration_sessions", identifier)
    result = json.loads(row["result_json"] or "{}")
    result.update(status="interrupted",
                  error="No monitor was running this session; it was not resumed")
    with conn:
        conn.execute("UPDATE calibration_sessions SET status='interrupted', result_json=? "
                     "WHERE id=?", (json.dumps(result, allow_nan=False), identifier))


def _supersede(sessions, by: str, reason: str):
    """Settle uncertain sessions explicitly: recorded as superseded, never silently cleared."""
    with connection() as conn:
        with conn:
            for _, identifier, result in sessions:
                result.update(superseded_by=by, apply_state="superseded",
                              supersession_reason=reason)
                conn.execute("UPDATE calibration_sessions SET result_json=? WHERE id=?",
                             (json.dumps(result, allow_nan=False), identifier))


def _matches(received: dict, result: dict) -> bool:
    intended = {**result["previous_settings"], **result["proposal"]["settings"]}
    return all(math.isclose(received.get(k, float("nan")), value, rel_tol=1e-6)
               for k, value in intended.items())


def _record(robot_id, identifier):
    with connection() as conn:
        row = need(conn, "calibration_sessions", identifier)
        if row["robot_id"] != robot_id:
            raise HTTPException(404, "calibration belongs to another robot")
        result = json.loads(row["result_json"])
        result["status"] = row["status"]
        return result


def _monitor(identifier, robot, result):
    started = time.monotonic()
    samples: list[dict] = []
    try:
        while True:
            current = robot._call("GET", f"/calibrations/{identifier}")
            result.update(current)
            if "samples" not in current and len(samples) < 2000:
                samples.append({key: current.get(key) for key in
                                ("elapsed_s", "left_mps", "right_mps", "target_mps")})
                result["samples"] = samples
            _save(identifier, result)
            if current["status"] != "running":
                break
            if time.monotonic() - started > 28:
                robot.stop()
                raise TimeoutError("calibration timed out")
            time.sleep(0.1)
    except Exception as exc:
        result.update(status="error", error=str(exc))
        _save(identifier, result)
    finally:
        robot.close()  # ends heartbeat; the firmware deadman remains the last stop
        with _LOCK:
            _ACTIVE.pop(identifier, None)


@router.post("/robots/{robot_id}/calibrations")
@serialize_start
def start_calibration(robot_id: str, body: dict = Body(...)):
    """Start once per optional request_id; a retry returns the durable session without motion.

    Keep the same request_id after transport failure. An interrupted/unknown result is not
    evidence of a completed experiment; inspect rover status before an explicit new experiment.
    """
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or
                                   not 1 <= len(request_id) <= 128):
        raise HTTPException(400, "request_id must be a string of 1 to 128 characters")
    identifier = (uuid.uuid5(uuid.NAMESPACE_URL, f"calibration:{robot_id}:{request_id}").hex
                  if request_id is not None else db.new_id())
    try:
        encoded = json.dumps(body, sort_keys=True, allow_nan=False).encode()
    except ValueError as exc:
        raise HTTPException(400, "Calibration parameters must be finite") from exc
    fingerprint = hashlib.sha256(encoded).hexdigest()
    with connection() as conn:
        robot_row = need(conn, "robots", robot_id)
        existing = db.get(conn, "calibration_sessions", identifier)
        if existing:
            recorded = json.loads(existing["result_json"])
            if recorded.get("request_fingerprint") != fingerprint:
                raise HTTPException(409, "request_id reused with different calibration parameters")
            return {**recorded, "status": existing["status"]}
        config = json.loads(robot_row["config_json"] or "{}")
        running = [row["id"] for row in conn.execute(
            "SELECT id FROM calibration_sessions WHERE robot_id=? AND status='running'",
            (robot_id,))]
        with _LOCK:
            monitored = [identifier for identifier in running if identifier in _ACTIVE]
        # A 'running' row nobody is monitoring (its monitor died) would block this robot until a
        # restart; record it as interrupted instead. Nothing is resumed.
        for orphan in set(running) - set(monitored):
            _interrupt_orphan(conn, orphan)
        if monitored:
            raise HTTPException(409, "robot already has an active calibration")
        robot = RoverRobot(config["address"])
        result = None
        try:
            status = robot.status()
            if "async_calibration_v1" not in status.get("capabilities", []):
                raise HTTPException(409, "Update firmware: asynchronous calibration unsupported")
            previous = robot._call("GET", "/config")["settings"]
            spec, spec_id = specifications.resolve(conn, robot_id, body.get("robot_spec"))
            source = "simulated" if config.get("fake") or status.get("fake") else "measured"
            snapshot = spec.as_dict()
            geometry = {key: previous[key] for key in
                        ("wheel_radius_m", "wheelbase_m", "counts_per_rev") if key in previous}
            if source == "simulated":
                snapshot.update(geometry)
                spec_from_dict(snapshot)
            elif any(snapshot[key] != value for key, value in geometry.items()):
                raise HTTPException(409, "Rover geometry differs from the local specification")
            result = dict(id=identifier, mode=body.get("mode"), status="running",
                          start_state="pending", request_id=request_id,
                          request_fingerprint=fingerprint, robot_id=robot_id, source=source,
                          previous_settings=previous, robot_spec=snapshot, spec_id=spec_id,
                          spec_revision=specifications.revision(spec.as_dict()))
            # Commit identity before any motion command. Restart recovery interrupts this row;
            # a retry reads it and must never replay the start command.
            db.insert(conn, "calibration_sessions", id=identifier, robot_id=robot_id,
                      status=result["status"], provenance=source, result_json=json.dumps(result))
            response = robot._call("POST", "/calibrations", {**body, "session": identifier})
            result.update(response, start_state="accepted")
            _save(identifier, result)
            robot.start_heartbeat(identifier)
        except (RoverError, httpx.HTTPError) as exc:
            robot.close()
            if result is not None:
                result.update(status="interrupted", start_state="unknown", error=str(exc))
                _save(identifier, result)
            code = exc.status_code if isinstance(exc, RoverError) else 502
            raise HTTPException(code, str(exc)) from exc
        except Exception as exc:
            robot.close()
            if result is not None:
                result.update(status="interrupted", start_state="unknown", error=str(exc))
                _save(identifier, result)
            raise
    with _LOCK:
        _ACTIVE[identifier] = robot
    threading.Thread(target=_monitor, args=(identifier, robot, result), daemon=True).start()
    return result


@router.get("/robots/{robot_id}/calibration-requests/{request_id}")
def calibration_request(robot_id: str, request_id: str):
    """Recover the recorded outcome without sending any command to the rover."""
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"calibration:{robot_id}:{request_id}").hex
    return _record(robot_id, identifier)


@router.get("/robots/{robot_id}/calibrations")
def calibration_history(robot_id: str, limit: int = Query(25, ge=1, le=100),
                        offset: int = Query(0, ge=0)):
    with connection() as conn:
        need(conn, "robots", robot_id)
        rows = conn.execute(
            "SELECT status,result_json FROM calibration_sessions WHERE robot_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
            (robot_id, limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM calibration_sessions WHERE robot_id=?",
                             (robot_id,)).fetchone()[0]
        items = [{**json.loads(row["result_json"]), "status": row["status"]} for row in rows]
        return {"items": items,
                "total": total, "next_offset": offset + limit if offset + limit < total else None}


@router.get("/robots/{robot_id}/calibrations/{identifier}")
def calibration_status(robot_id: str, identifier: str):
    return _record(robot_id, identifier)


@router.post("/robots/{robot_id}/calibrations/{identifier}/stop")
def stop_calibration(robot_id: str, identifier: str):
    _record(robot_id, identifier)
    with _LOCK:
        robot = _ACTIVE.get(identifier)
    if robot is None:
        raise HTTPException(409, "calibration is not running")
    try:
        return robot.stop()
    except (RoverError, httpx.HTTPError) as exc:
        robot.stop_heartbeat()
        raise HTTPException(502, str(exc)) from exc


@router.post("/robots/{robot_id}/calibrations/{identifier}/measurement")
def measure(robot_id: str, identifier: str, body: dict = Body(...)):
    result = _record(robot_id, identifier)
    try:
        result["proposal"] = measurement(result, body.get("measured"),
                                         spec_from_dict(result["robot_spec"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _save(identifier, result)
    return result


def _publish(robot_id, identifier, result, config, spec_id, received, simulated):
    snapshot = result["robot_spec"]
    proposal = result["proposal"]
    updated = {**snapshot, **proposal["settings"],
               "source": "simulated" if simulated else "calibrated_geometry"}
    spec_from_dict(updated)
    revision = specifications.revision(updated)
    folder = "simulated-specs" if simulated else "robot-specs"
    path = Path(data_root()) / folder / robot_id / f"{revision}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(str(path), yaml.safe_dump(updated, sort_keys=False).encode())
    result.update(applied=True, apply_state="verified", read_back=received,
                  local_spec=str(path), noise_unchanged=True, applied_revision=revision)
    config["accepted_spec"] = {"spec_id": spec_id, "revision": revision,
                               "snapshot": updated, "calibration_id": identifier}
    # Publish the revision and verified session together; historical files remain immutable.
    with connection() as conn:
        with conn:
            conn.execute("UPDATE robots SET config_json=? WHERE id=?",
                         (json.dumps(config, allow_nan=False), robot_id))
            conn.execute("UPDATE calibration_sessions SET result_json=? WHERE id=?",
                         (json.dumps(result, allow_nan=False), identifier))
    return result


@router.post("/robots/{robot_id}/calibrations/{identifier}/apply")
@serialize_start
def apply(robot_id: str, identifier: str):
    result = _record(robot_id, identifier)
    if result.get("applied"):
        return result
    proposal = result.get("proposal")
    if not proposal or result["status"] != "done":
        raise HTTPException(409, "Complete the experiment and record a measurement first")
    with connection() as conn:
        robot_row = need(conn, "robots", robot_id)
    config = json.loads(robot_row["config_json"] or "{}")
    robot = RoverRobot(config["address"])
    previous = None
    try:
        previous = robot._call("GET", "/config")["settings"]
        if previous != result["previous_settings"]:
            raise HTTPException(409, "Rover configuration changed since this experiment")
        status = robot.status()
        simulated = bool(config.get("fake") or status.get("fake"))
        if (result["source"] == "simulated") != simulated:
            raise HTTPException(409, "Calibration provenance does not match the rover")
        snapshot = result["robot_spec"]
        with connection() as conn:
            current, spec_id = specifications.resolve(conn, robot_id, result.get("spec_id"))
        current_revision = specifications.revision(current.as_dict())
        if result.get("spec_revision") and current_revision != result["spec_revision"]:
            raise HTTPException(409, "Robot specification changed since the experiment")
        if not simulated and current.as_dict() != snapshot:
            raise HTTPException(409, "Local robot specification changed since the experiment")
        intended = {**previous, **proposal["settings"]}
        result["apply_state"] = "verifying"
        result["previous_settings"] = previous
        _save(identifier, result)
        robot.configure(proposal["settings"])
        received = robot._call("GET", "/config")["settings"]
        if any(not math.isclose(received.get(k, float("nan")), v, rel_tol=1e-6)
               for k, v in intended.items()):
            raise ValueError("Configuration read-back differs from requested settings")
        return _publish(robot_id, identifier, result, config, spec_id, received, simulated)
    except (RoverError, httpx.HTTPError, ValueError, OSError, sqlite3.Error) as exc:
        result.update(apply_state="failed", apply_error=str(exc), applied=False)
        if previous is not None:
            try:
                robot.configure(previous)
                result["rollback_verified"] = robot._call("GET", "/config")["settings"] == previous
            except Exception:
                result["rollback_verified"] = False
        _save(identifier, result)
        raise HTTPException(409, result) from exc
    finally:
        robot.close()


@router.post("/robots/{robot_id}/calibrations/{identifier}/reconcile")
@serialize_start
def reconcile(robot_id: str, identifier: str):
    """Verify interrupted or legacy apply from live read-back; never command motion/config."""
    result = _record(robot_id, identifier)
    if result.get("status") != "done" or not result.get("proposal"):
        raise HTTPException(409, "Only a completed, measured experiment can be reconciled")
    with connection() as conn:
        config = json.loads(need(conn, "robots", robot_id)["config_json"] or "{}")
        current, spec_id = specifications.resolve(
            conn, robot_id, result.get("spec_id"), reconciling=identifier)
        anchor = conn.execute("SELECT rowid FROM calibration_sessions WHERE id=?",
                              (identifier,)).fetchone()["rowid"]
        rows = conn.execute(
            "SELECT id,result_json FROM calibration_sessions WHERE robot_id=? AND rowid>?",
            (robot_id, anchor)).fetchall()
        newer_applied = [row["id"] for row in rows
                         if json.loads(row["result_json"]).get("applied")]
        own = [entry for entry in specifications.uncertain_sessions(conn, robot_id, config)
               if entry[1] == identifier]
        # Older uncertain sessions, newest first: settled by this reconciliation.
        older = [entry for entry in specifications.uncertain_sessions(conn, robot_id, config)
                 if entry[0] < anchor][::-1]
    if newer_applied:
        if own:
            # This session's uncertainty is moot: a newer calibration was applied after it.
            _supersede(own, newer_applied[-1], "a newer calibration was applied after it")
            return _record(robot_id, identifier)
        raise HTTPException(409, "A newer calibration was applied; inspect that session")
    accepted = config.get("accepted_spec")
    if (result.get("spec_revision")
            and specifications.revision(current.as_dict()) != result["spec_revision"]
            and (not accepted or accepted.get("calibration_id") != identifier)):
        raise HTTPException(409, "Specification changed; this proposal cannot be adopted")
    robot = RoverRobot(config["address"])
    try:
        status = robot.status()
        if status.get("state") not in {"idle", "done", "stopped", "estop", "error"}:
            raise HTTPException(409, "Stop the rover before reconciling configuration")
        simulated = bool(config.get("fake") or status.get("fake"))
        if simulated != (result["source"] == "simulated"):
            raise HTTPException(409, "Calibration provenance does not match the rover")
        received = robot.configuration()["settings"]
        previous = result["previous_settings"]
        if _matches(received, result):
            published = _publish(robot_id, identifier, result, config, spec_id, received,
                                 simulated)
            _supersede(older, identifier, "a newer calibration was verified on the rover")
            return published
        if received == previous and not result.get("applied"):
            result.update(apply_state="not_applied", applied=False, rollback_verified=True,
                          read_back=received)
            # This session never reached the rover, so what it found there is what an older
            # session left. Adopt the newest older proposal the rover is running, if any.
            adopted = next((entry for entry in older
                            if entry[2].get("status") == "done" and entry[2].get("proposal")
                            and (entry[2].get("source") == "simulated") == simulated
                            and _matches(received, entry[2])), None)
            if adopted is not None:
                _publish(robot_id, adopted[1], adopted[2], config,
                         adopted[2].get("spec_id") or spec_id, received, simulated)
                result["adopted_calibration_id"] = adopted[1]
                _supersede([entry for entry in older if entry is not adopted], adopted[1],
                           "an older session's proposal was found live on the rover")
            else:
                _supersede(older, identifier,
                           "the live configuration matched none of their proposals")
            _save(identifier, result)
            return result
        result.update(apply_state="needs_reconciliation", applied=False,
                      apply_error="Live configuration differs from proposal and prior state")
        _save(identifier, result)
        raise HTTPException(409, result)
    except (RoverError, httpx.HTTPError, ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(409, f"Configuration reconciliation not confirmed: {exc}") from exc
    finally:
        robot.close()


def close_sessions():
    with _LOCK:
        robots = list(_ACTIVE.values())
    for robot in robots:
        robot.stop_heartbeat()
