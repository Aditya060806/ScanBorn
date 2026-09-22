"""Job registry for GET /status/{job_id}.

Every REST call that does work gets a job row so the dashboard can poll it.
"""
import uuid
import json
from contextlib import closing

from orchestrator import db
from orchestrator.request_scope import job_ids

_LISTENERS: list = []


def subscribe(callback):
    """Register `callback(job)` for every job create/finish. Returns an unsubscribe."""
    _LISTENERS.append(callback)
    return lambda: _LISTENERS.remove(callback) if callback in _LISTENERS else None


def _notify(job: dict) -> None:
    # A broken listener (disconnected socket) must not fail the pipeline stage
    # that happened to be the one publishing.
    for callback in list(_LISTENERS):
        try:
            callback(dict(job))
        except Exception:  # noqa: BLE001 - listeners are fire-and-forget
            pass


def create_job(stage: str) -> str:
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "stage": stage, "progress": 0.0,
           "status": "running", "detail": None,
           "logs_url": f"/status/{job_id}/logs"}
    with closing(db.connect()) as conn:
        db.init_db(conn)
        db.insert(conn, "jobs", id=job_id, stage=stage, status="running", progress=0.0)
    scope = job_ids.get()
    if scope is not None:
        scope.append(job_id)
    _notify(job)
    return job_id


def finish_job(job_id: str, ok: bool = True, detail=None) -> dict:
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    job.update(status="done" if ok else "failed", progress=1.0 if ok else job["progress"],
               detail=detail)
    with closing(db.connect()) as conn:
        db.update(conn, "jobs", job_id, status=job["status"], progress=job["progress"],
                  detail_json=json.dumps(detail))
    _notify(job)
    return job


def get_job(job_id: str):
    with closing(db.connect()) as conn:
        db.init_db(conn)
        row = db.get(conn, "jobs", job_id)
        return _out(row) if row else None


def all_jobs() -> list:
    """Snapshot, oldest first — what a dashboard needs on connect."""
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return [_out(dict(row)) for row in conn.execute("SELECT * FROM jobs ORDER BY rowid")]


def _out(row: dict) -> dict:
    return {"job_id": row["id"], "stage": row["stage"], "progress": row["progress"],
            "status": row["status"], "detail": json.loads(row["detail_json"] or "null"),
            "logs_url": None}


def interrupt_running() -> None:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        conn.execute("UPDATE jobs SET status='interrupted', detail_json=? WHERE status='running'",
                     (json.dumps("Orchestrator restarted; no action was automatically resumed"),))
        conn.execute("UPDATE request_results SET status='interrupted' WHERE status='running'")
        conn.execute("UPDATE deployments SET status='interrupted' WHERE status='running'")
        conn.execute("UPDATE calibration_sessions SET status='interrupted' WHERE status='running'")
        conn.commit()
