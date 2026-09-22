"""Bind an exported policy's gate result to its bytes, scene and robot model."""
import hashlib
import json
from pathlib import Path


def checksum(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validation_record(artifact: str, navmesh: str, spec, mission_id, metrics: dict) -> dict:
    return {"version": 1, "artifact_sha256": checksum(artifact),
            "navmesh_sha256": checksum(navmesh), "robot_spec": spec.as_dict(),
            "mission_id": mission_id, "result": metrics}


def refusal(record: dict | None, artifact: str, navmesh: str, spec, mission_id) -> str | None:
    if not record or not record.get("result", {}).get("passed"):
        return "Exported artifact has no passing validation. Optimize the policy again."
    if record.get("robot_spec") != spec.as_dict():
        return "Robot specification changed after export validation. Train and optimize again."
    if record.get("mission_id") != mission_id:
        return "Artifact validation belongs to a different mission."
    if record.get("artifact_sha256") != checksum(artifact):
        return "Artifact bytes changed after validation. Optimize again."
    if record.get("navmesh_sha256") != checksum(navmesh):
        return "Twin geometry changed after artifact validation. Train and optimize again."
    return None


def read_record(artifact: str) -> dict | None:
    path = Path(artifact).parent / "validation.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
