"""Resolve a robot's accepted specification without treating its display name as a path."""
import hashlib
import json

from fastapi import HTTPException

from orchestrator import db
from robot.spec import load_spec, spec_from_dict


def revision(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def uncertain_sessions(conn, robot_id: str, config: dict) -> list:
    """Sessions whose effect on the rover is unknown, oldest first, as (rowid, id, result).

    An apply that was interrupted or failed without a verified rollback, and an apply recorded
    before accepted specs existed, are uncertain until reconciled from live read-back. A session
    that reconciliation superseded is settled, and says by which session and why.
    """
    pending = []
    for session in conn.execute("SELECT rowid, id, result_json FROM calibration_sessions "
                                "WHERE robot_id=? ORDER BY rowid", (robot_id,)):
        result = json.loads(session["result_json"])
        if result.get("superseded_by"):
            continue
        uncertain = result.get("apply_state") in {"verifying", "needs_reconciliation"} or (
            result.get("apply_state") == "failed" and result.get("rollback_verified") is False)
        uncertain = uncertain or (result.get("applied") and not config.get("accepted_spec"))
        if uncertain:
            pending.append((session["rowid"], session["id"], result))
    return pending


def resolve(conn, robot_id=None, spec_id=None, default="rover", *, reconciling=None):
    config = {}
    if robot_id:
        row = db.get(conn, "robots", robot_id)
        if row is None:
            raise HTTPException(404, "Robot not found")
        config = json.loads(row["config_json"] or "{}")
        pending = uncertain_sessions(conn, robot_id, config)
        if reconciling is not None:
            # Reconciling one session settles every older one; only newer uncertainty blocks it.
            anchor = conn.execute("SELECT rowid FROM calibration_sessions WHERE id=?",
                                  (reconciling,)).fetchone()
            floor = anchor["rowid"] if anchor else -1
            pending = [entry for entry in pending if entry[0] > floor]
        if pending:
            raise HTTPException(409, {"code": "calibration_reconciliation",
                                      "calibration_id": pending[-1][1],
                                      "pending": [entry[1] for entry in pending],
                                      "error": "Reconcile uncertain rover configuration first, "
                                               "newest session first"})
    accepted = config.get("accepted_spec")
    if accepted:
        if spec_id and spec_id != accepted["spec_id"]:
            raise HTTPException(409, "Selected specification differs from the accepted robot spec")
        spec = spec_from_dict(accepted["snapshot"])
        if revision(spec.as_dict()) != accepted["revision"]:
            raise HTTPException(409, "Stored robot specification revision is inconsistent")
        if spec.source == "simulated" and not config.get("fake"):
            raise HTTPException(409, "Simulated calibration cannot configure a physical robot")
        return spec, accepted["spec_id"]
    identifier = spec_id or default
    try:
        return load_spec(identifier), identifier
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
