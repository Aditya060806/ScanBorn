"""Record a real orchestrator run for the dashboard's offline fixture.

Drives every stage of orchestrator/service.py in-process, on the same synthetic top-down
room the tests use, and writes the raw responses to dashboard/src/fixtures.json. The
dashboard replays them through the same mappers a live run uses (dashboard/src/pipeline.js),
so the fixture cannot drift from the API's shape and contains nothing the orchestrator did
not actually return.

    python scripts/record_dashboard_fixture.py
"""
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dashboard", "src", "fixtures.json")


def _sanitize(value, replacements):
    """Swap throwaway temp paths for the orchestrator's defaults, so the file is stable."""
    if isinstance(value, dict):
        return {key: _sanitize(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            if old in value:
                return value.replace(old, new).replace("\\", "/")
    return value


def main() -> None:
    sys.path.insert(0, ROOT)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data_root = os.path.join(tmp, "data")
        os.environ["SCANBORN_DATA"] = data_root
        for key in ("SCANBORN_PLANNER", "SCANBORN_PROFILER", "AI_HUB_API_TOKEN"):
            os.environ.pop(key, None)

        from fastapi.testclient import TestClient

        from orchestrator import db
        from orchestrator.service import app
        from tests.test_orchestrator import frame_bytes, scan_frame

        db.DB_PATH = os.path.join(tmp, "scanborn.db")
        client = TestClient(app)

        def call(method: str, path: str, **kwargs) -> dict:
            response = getattr(client, method)(path, **kwargs)
            if response.status_code != 200:
                raise SystemExit(
                    f"{method.upper()} {path} -> {response.status_code}: {response.text}")
            return response.json()

        def upload(frame: dict) -> dict:
            first = call("post", "/capture", params={"index": 0},
                         files={"file": ("frame.npz", frame_bytes(frame))})
            return call("post", f"/capture/{first['scan_id']}/complete")

        capture = upload(scan_frame())
        reconstruct = call("post", "/reconstruct",
                           json={"scan_id": capture["scan_id"], "mode": "fast"})
        segment = call("post", "/segment", json={"mesh_id": reconstruct["mesh_id"]})
        twin = call("post", "/generate-twin", json={
            "mesh_id": reconstruct["mesh_id"], "objects_id": segment["objects_id"],
            "environment": "home"})
        navmesh = call("get", f"/twins/{twin['twin_id']}/navmesh")
        visualization = call("get", f"/twins/{twin['twin_id']}/visualization")
        fixture_dir = Path(ROOT) / "dashboard" / "public" / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        for asset in visualization["geometry"]:
            geometry = client.get(asset["url"])
            geometry.raise_for_status()
            name = f"recorded-room-{asset['level']}.bin"
            (fixture_dir / name).write_bytes(geometry.content)
            asset["url"] = f"/fixtures/{name}"
        plan = call("post", "/plan", json={
            "twin_id": twin["twin_id"], "text": "go to the table", "lang": "en"})
        # From near the far wall to the spot beside the table the plan names: about the length
        # the rover can dead-reckon within the 10 cm bar (Phase 3 plan, decision 14).
        mission = call("post", "/missions", json={
            "twin_id": twin["twin_id"], "start": [0.2, 1.9],
            "task_graph_id": plan["task_graph_id"]})
        train = call("post", "/train", json={
            "twin_id": twin["twin_id"], "task_graph_id": plan["task_graph_id"],
            "mission_id": mission["mission_id"]})
        optimize = call("post", "/optimize", json={"policy_id": train["policy_id"]})
        deploy = call("post", "/deploy", json={
            "artifact_id": optimize["artifact_id"], "kind": "sim"})
        rescan = upload(scan_frame(table=False))
        sync = call("post", "/sync", json={
            "twin_id": twin["twin_id"], "new_scan_id": rescan["scan_id"],
            "same_marker_confirmed": True})

        responses = {
            "capture": capture, "reconstruct": reconstruct, "segment": segment,
            "twin": twin, "navmesh": navmesh, "plan": plan, "mission": mission,
            "train": train, "optimize": optimize, "deploy": deploy, "sync": sync,
            "visualization": visualization,
        }
        document = {
            "source": ("Real orchestrator responses (orchestrator/service.py, run in-process) "
                       "on the synthetic room from tests/test_orchestrator.py: a floor, a "
                       "table and a chair-height box seen from 2.5 m above, with a mission "
                       "from the far corner to the table."),
            "recorded_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"),
            "command": "python scripts/record_dashboard_fixture.py",
            # Paths read as the orchestrator's default SCANBORN_DATA=data would write them.
            "responses": _sanitize(responses, [(data_root, "data"), (tmp, "<tmp>")]),
        }

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(document, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT}: {len(segment['objects'])} objects, "
          f"mission {mission['length_m']} m, {len(deploy['pose_trace'])} poses, "
          f"sim success {train['sim_success_rate']}")


if __name__ == "__main__":
    main()
