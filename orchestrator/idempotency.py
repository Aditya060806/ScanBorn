"""Persistent request identities; interrupted mutations require an explicit new request."""
import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from functools import wraps

from fastapi import HTTPException

from orchestrator import db

_START_LOCK = threading.RLock()


def serialize_start(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _START_LOCK:
            return function(*args, **kwargs)
    return wrapped


def idempotent(function):
    @wraps(function)
    def wrapped(body):
        identifier = body.get("request_id")
        if identifier is None:
            return function(body)
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
            raise HTTPException(400, "request_id must be a string of 1..128 characters")
        key = f"{function.__name__}:{identifier}"
        payload = json.dumps(body, sort_keys=True, allow_nan=False).encode()
        fingerprint = hashlib.sha256(payload).hexdigest()
        with closing(db.connect()) as conn:
            db.init_db(conn)
            try:
                db.insert(conn, "request_results", id=key, route=function.__name__,
                          payload_hash=fingerprint, status="running")
            except sqlite3.IntegrityError:
                row = db.get(conn, "request_results", key)
                if row["payload_hash"] != fingerprint:
                    raise HTTPException(409, "request_id was used with different inputs")
                if row["status"] == "done":
                    return json.loads(row["response_json"])
                raise HTTPException(409, {
                    "code": "request_" + row["status"],
                    "error": "Inspect the previous result before a new request"})
        try:
            result = function(body)
        except BaseException:
            with closing(db.connect()) as conn:
                db.update(conn, "request_results", key, status="failed")
            raise
        with closing(db.connect()) as conn:
            db.update(conn, "request_results", key, status="done", response_json=json.dumps(result))
        return result
    return wrapped
