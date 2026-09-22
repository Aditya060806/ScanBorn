"""ScanBorn Python SDK — one method per orchestrator endpoint (blueprint section 19)."""
import io
import json
import os
import uuid
from urllib.parse import quote

import httpx


class ScanBornError(RuntimeError):
    """An orchestrator call failed. Carries the server's own explanation."""

    def __init__(self, response: httpx.Response):
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        super().__init__(f"{response.status_code}: {detail}")
        self.status_code = response.status_code
        self.detail = detail


class ScanBorn:
    """Client for a running orchestrator.

    `http` accepts a preconfigured httpx.Client, which is how tests drive the SDK
    against the app in-process with no socket:
        ScanBorn(http=fastapi.testclient.TestClient(app))
    """

    def stage_request(self, stage: str, request_id: str) -> dict:
        """Look up an interrupted/lost stage response; never starts work."""
        return self._get(f"/stages/{quote(stage, safe='')}/requests/{quote(request_id, safe='')}")

    def __init__(self, base_url: str = "http://localhost:8000", transport=None,
                 timeout: float = 120.0, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(base_url=self.base_url, transport=transport,
                                          timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._http.close()

    def _post(self, path: str, **kwargs) -> dict:
        response = self._http.post(path, **kwargs)
        if response.is_error:
            raise ScanBornError(response)
        return response.json()

    def _get(self, path: str) -> dict:
        response = self._http.get(path)
        if response.is_error:
            raise ScanBornError(response)
        return response.json()

    # ---------------------------------------------------------------- the eight verbs

    def capture(self, frames, meta: dict | None = None, scan_id: str | None = None) -> dict:
        """Upload frames (paths, bytes, or dicts of arrays) and close the scan."""
        result = {"scan_id": scan_id or uuid.uuid4().hex}
        uploaded = 0
        for index, frame in enumerate(frames):
            params: dict = {"index": index}
            if result.get("scan_id"):
                params["scan_id"] = result["scan_id"]
            if index == 0 and meta:
                params["meta"] = json.dumps(meta)
            result = self._post("/capture",
                                files={"file": ("frame.npz", _as_bytes(frame))},
                                params=params)
            uploaded += 1
        if not uploaded:
            raise ValueError("capture() needs at least one frame")
        return self._post(f"/capture/{result['scan_id']}/complete")

    def import_scan(self, path: str, units: str | None = None, up_axis: str | None = None,
                    scan_id: str | None = None) -> dict:
        """Open a scan from a .ply/.obj export. Declare `units` (m, cm, mm, ft, in) and
        `up_axis` (z, y) when known; one left out is recorded as assumed (m, z). An import has
        no home-marker frame: `registration` in the reply says a physical rover will refuse it."""
        params = {key: value for key, value in (("units", units), ("up_axis", up_axis))
                  if value is not None}
        route = f"/capture/{scan_id}/import" if scan_id else "/capture/import"
        with open(path, "rb") as stream:
            return self._post(route, params=params,
                              files={"file": (os.path.basename(path), stream.read())})

    def scan_status(self, scan_id: str) -> dict:
        """Upload state, frame count and registration (whether a physical rover can use it)."""
        return self._get(f"/capture/{quote(scan_id, safe='')}")

    def reconstruct(self, scan_id: str, mode: str = "fast") -> dict:
        return self._post("/reconstruct", json={"scan_id": scan_id, "mode": mode})

    def segment(self, mesh_id: str) -> dict:
        return self._post("/segment", json={"mesh_id": mesh_id})

    def generate_twin(self, mesh_id: str, objects_id: str | None = None,
                      environment: str | None = None, cell_m: float | None = None,
                      robot_id: str | None = None) -> dict:
        """`environment` names the robot profile and `cell_m` the grid (0.05 or 0.1 m).

        Omit either to let the orchestrator choose from the scan; the response's `resolution`
        says which grid was used and why.
        """
        body: dict = {"mesh_id": mesh_id, "objects_id": objects_id}
        if environment:
            body["environment"] = environment
        if cell_m is not None:
            body["cell_m"] = cell_m
        if robot_id:
            body["robot_id"] = robot_id
        return self._post("/generate-twin", json=body)

    def plan(self, twin_id: str, text: str, lang: str = "en",
             request_id: str | None = None) -> dict:
        """Plan an instruction for a twin. A plan needing clarification or recognising no action
        is a 422 ScanBornError whose `detail` says why; nothing is stored. Pass a `request_id`
        to make a retry after a lost response return the stored result instead of planning (and,
        with a remote provider, paying) again; it also lets `cancel_provider_request` stop it."""
        body = {"twin_id": twin_id, "text": text, "lang": lang}
        if request_id:
            body["request_id"] = request_id
        return self._post("/plan", json=body)

    def train(self, twin_id: str, task_graph_id: str | None = None, demonstrations=None,
              mission_id: str | None = None, robot_id: str | None = None) -> dict:
        body: dict = {"twin_id": twin_id, "task_graph_id": task_graph_id,
                      "demonstrations": demonstrations or []}
        if mission_id:
            body["mission_id"] = mission_id
        if robot_id:
            body["robot_id"] = robot_id
        return self._post("/train", json=body)

    def optimize(self, policy_id: str, device_label: str | None = None) -> dict:
        """Export the int8 bundle. The response's `format`, `runtime` and `experiments` say what
        the bytes are; an experiment (SCANBORN_EXPORT=ai-hub or qairt) is never deployable."""
        body = {"policy_id": policy_id}
        if device_label:
            body["device_label"] = device_label
        return self._post("/optimize", json=body)

    def cancel_provider_request(self, request_id: str) -> dict:
        """Stop a running planner or profiler chain between attempts; 404 if none is running."""
        return self._post(f"/providers/requests/{request_id}/cancel")

    def save_provider_settings(self, workloads: dict, remote_consent: dict) -> dict:
        """Select provider chains explicitly, with the published outgoing-data scopes."""
        return self._post("/providers/settings", json={"workloads": workloads,
                                                       "remote_consent": remote_consent})

    def providers(self) -> dict:
        """Which provider each workload uses (planner, profiler, export) and whether it is
        ready. Configured on the orchestrator; credential values are never returned."""
        return self._get("/providers")

    def deploy(self, artifact_id: str, robot_id: str | None = None, kind: str = "sim") -> dict:
        return self._post("/deploy", json={"artifact_id": artifact_id,
                                           "robot_id": robot_id, "kind": kind})

    # ----------------------------------------------------------------------- missions

    def navmesh(self, twin_id: str) -> dict:
        """The twin's occupancy grid; decode its cells with `decode_cells`."""
        return self._get(f"/twins/{twin_id}/navmesh")

    def mission(self, twin_id: str, goal=None, start=None, via=None,
                task_graph_id: str | None = None) -> dict:
        """Plan and store a mission. Give `goal` as (x, y), or a `task_graph_id` instead.

        `start` is (x, y) or (x, y, heading); omit it when the scan has a home pose. A goal
        the robot cannot reach raises ScanBornError with status 422, and its `detail` carries
        a `code` and the `nearest` point that would work.
        """
        body: dict = {"twin_id": twin_id}
        if goal is not None:
            body["goal"] = [float(v) for v in goal]
        if start is not None:
            body["start"] = [float(v) for v in start]
        if via:
            body["via"] = [[float(v) for v in point] for point in via]
        if task_graph_id is not None:
            body["task_graph_id"] = task_graph_id
        return self._post("/missions", json=body)

    def get_mission(self, mission_id: str) -> dict:
        return self._get(f"/missions/{mission_id}")

    def robot_spec(self, name: str = "rover") -> dict:
        """The robot's size, limits and noise; `source` says whether they are measured."""
        return self._get(f"/robot-specs/{name}")

    # ------------------------------------------------------------------------- rovers

    def register_robot(self, kind: str = "fake_rover", address: str | None = None,
                       name: str | None = None, time_scale: float = 1.0) -> dict:
        """Register a protocol rover: an ESP32 at `address`, or a fake rover the orchestrator runs.

        The answer carries the rover's own status. A fake rover says `"fake": true`, and so does
        every result it produces. `time_scale` runs a fake rover's model faster than real time.
        """
        body: dict = {"kind": kind, "time_scale": time_scale}
        if address:
            body["address"] = address
        if name:
            body["name"] = name
        return self._post("/robots", json=body)

    def robot_status(self, robot_id: str) -> dict:
        return self._get(f"/robots/{robot_id}/status")

    def estop(self, robot_id: str) -> dict:
        """E-STOP a rover. It latches until `reset_robot`; a drive in progress ends as `estop`."""
        return self._post(f"/robots/{robot_id}/estop")

    def reset_robot(self, robot_id: str) -> dict:
        return self._post(f"/robots/{robot_id}/reset")

    def calibrate(self, robot_id: str, mode: str) -> dict:
        """`straight` (1 m) or `spin` (three turns). A fake rover answers `source: simulated`."""
        return self._post(f"/robots/{robot_id}/calibrate", json={"mode": mode})

    def begin_calibration(self, robot_id: str, mode: str, **options) -> dict:
        """Pass a stable request_id in options to recover a lost start response safely."""
        return self._post(f"/robots/{robot_id}/calibrations", json={"mode": mode, **options})

    def calibration_request(self, robot_id: str, request_id: str) -> dict:
        """Read a calibration start outcome without replaying motion."""
        from urllib.parse import quote
        return self._get(f"/robots/{quote(robot_id, safe='')}/calibration-requests/"
                         f"{quote(request_id, safe='')}")

    def calibration(self, robot_id: str, calibration_id: str) -> dict:
        return self._get(f"/robots/{robot_id}/calibrations/{calibration_id}")

    def calibration_history(self, robot_id: str, *, limit: int = 25, offset: int = 0) -> dict:
        """Discover persisted calibration sessions, including interrupted sessions."""
        return self._get(f"/robots/{robot_id}/calibrations?limit={limit}&offset={offset}")

    def measure_calibration(self, robot_id: str, calibration_id: str, measured: float) -> dict:
        return self._post(f"/robots/{robot_id}/calibrations/{calibration_id}/measurement",
                          json={"measured": measured})

    def apply_calibration(self, robot_id: str, calibration_id: str) -> dict:
        return self._post(f"/robots/{robot_id}/calibrations/{calibration_id}/apply")

    def reconcile_calibration(self, robot_id: str, calibration_id: str) -> dict:
        """Read back configuration and reconcile an interrupted/legacy apply; no motion."""
        return self._post(f"/robots/{robot_id}/calibrations/{calibration_id}/reconcile")

    def deployment(self, deployment_id: str) -> dict:
        """A rover deployment's state, its trace so far and, once it has ended, its result."""
        return self._get(f"/deployments/{deployment_id}")

    def deployment_request(self, request_id: str) -> dict:
        """Read a persisted deployment response without issuing a drive command."""
        return self._get(f"/deploy/requests/{quote(request_id, safe='')}")

    def stop_deployment(self, deployment_id: str) -> dict:
        """Stop a drive. The rover keeps its mission; the drive is recorded as stopped."""
        return self._post(f"/deployments/{deployment_id}/stop")

    # ------------------------------------------------------------------------- extras

    def sync(self, twin_id: str, new_scan_id: str, *, same_marker_confirmed: bool = False) -> dict:
        return self._post("/sync", json={"twin_id": twin_id, "new_scan_id": new_scan_id,
                                         "same_marker_confirmed": same_marker_confirmed})

    def discover(self, kind: str, limit: int = 25, offset: int = 0) -> dict:
        if kind not in {"scans", "twins", "robots", "missions", "deployments"}:
            raise ValueError("unknown discovery collection")
        return self._get(f"/{kind}?limit={int(limit)}&offset={int(offset)}")

    def visualization(self, identifier: str, kind: str = "meshes") -> dict:
        if kind not in {"meshes", "twins"}:
            raise ValueError("visualizations belong to meshes or twins")
        return self._get(f"/{kind}/{quote(identifier, safe='')}/visualization")

    def status(self, job_id: str) -> dict:
        return self._get(f"/status/{job_id}")

    def run_pipeline(self, frames, text: str, meta: dict | None = None, mode: str = "fast",
                     device_label: str | None = None, kind: str = "sim",
                     goal=None, start=None) -> dict:
        """Capture through deploy in one call. Returns every id the run produced.

        With a `goal` (and a `start`, unless the scan has a home pose) the policy is trained
        and deployed on that mission; without one, the goal comes from `text`.
        """
        scan = self.capture(frames, meta=meta)
        mesh = self.reconstruct(scan["scan_id"], mode=mode)
        objects = self.segment(mesh["mesh_id"])
        twin = self.generate_twin(mesh["mesh_id"], objects["objects_id"])
        graph = self.plan(twin["twin_id"], text)
        mission_id = (self.mission(twin["twin_id"], goal=goal, start=start)["mission_id"]
                      if goal is not None else None)
        policy = self.train(twin["twin_id"], graph["task_graph_id"], mission_id=mission_id)
        artifact = self.optimize(policy["policy_id"], device_label)
        deployment = self.deploy(artifact["artifact_id"], kind=kind)
        return {
            "scan_id": scan["scan_id"],
            "mesh_id": mesh["mesh_id"],
            "objects": objects["objects"],
            "twin_id": twin["twin_id"],
            "task_graph_id": graph["task_graph_id"],
            "mission_id": mission_id,
            "policy_id": policy["policy_id"],
            "sim_success_rate": policy["sim_success_rate"],
            "artifact_id": artifact["artifact_id"],
            "deployment_id": deployment["deployment_id"],
            "status": deployment["status"],
        }


def _as_bytes(frame) -> bytes:
    """Accept a path, raw bytes, or a dict of arrays."""
    if isinstance(frame, bytes):
        return frame
    if isinstance(frame, dict):
        import numpy as np

        buf = io.BytesIO()
        np.savez(buf, **frame)
        return buf.getvalue()
    with open(frame, "rb") as f:
        return f.read()


def decode_cells(navmesh: dict, key: str = "cells_b64"):
    """A navmesh response's grid as a (height, width) uint8 numpy array, row 0 at minimum y.

    `key` is "cells_b64" (0 free, 1 blocked, 2 unknown) or "band_b64" (1 where the floor is
    free but the robot does not fit).
    """
    import base64

    import numpy as np

    raw = np.frombuffer(base64.b64decode(navmesh[key]), dtype=np.uint8)
    return raw.reshape(navmesh["height"], navmesh["width"])
