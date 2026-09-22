"""A rover that exists only in software but speaks the real rover protocol.

docs/robot/rover-protocol.md over real sockets — standard-library HTTP for commands, UDP for
heartbeats and telemetry — with the Phase 3 rover model (policy/sim.py) where the motors would be.
It runs the same per-tick step as the firmware (policy/controller.py:control_step) with the int8
policy from the mission payload, so a drive here exercises everything the real rover will do except
the physics.

Two ways to run it:
    python -m robot.adapters.fake_rover --port 8081 [--navmesh navmesh.json]
or in-process, as the orchestrator does for `POST /robots {"kind": "fake_rover"}`.

`time_scale` makes the model run faster than real time, for tests. Heartbeat deadman, telemetry
rate and every timeout stay on the wall clock, so safety behaviour is tested as it will run.
"""
import argparse
import dataclasses
import json
import math
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from policy.controller import control_step
from policy.sim import DiffDriveSim, Noise
from robot.protocol import (
    DEADMAN_S,
    FIRMWARE_V_MAX,
    PROTOCOL_VERSION,
    TELEMETRY_HZ,
    policy_act,
    validate_mission,
)
from robot.spec import load_spec, spec_from_dict

FIRMWARE = "fake-0.4.0"

# Without a twin's map the fake rover drives on an empty 100 x 100 m floor: nothing to hit but the
# edge. The real rover has no map at all.
OPEN_FLOOR = {"origin": [-50.0, -50.0], "cell": 0.5, "width": 200, "height": 200,
              "grid": [[0] * 200 for _ in range(200)]}


class FakeRover:
    """One fake rover: an HTTP server, a heartbeat socket, a control loop and a telemetry loop."""

    def __init__(self, spec=None, navmesh: dict | None = None, noise: Noise | None = None,
                 seed: int = 0, time_scale: float = 1.0, host: str = "127.0.0.1",
                 http_port: int = 0, heartbeat_port: int = 0, deadman_s: float = DEADMAN_S):
        self.spec = spec or load_spec("rover")
        self.noise = Noise.from_spec(self.spec) if noise is None else noise
        self.seed = seed
        self.time_scale = time_scale
        self.deadman_s = deadman_s
        self._navmesh: dict = navmesh or OPEN_FLOOR
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._threads: list = []
        self._runs = 0
        self._calibration: dict | None = None
        self._configuration = {key: getattr(self.spec, key) for key in
                               ("wheel_radius_m", "wheelbase_m", "counts_per_rev")}
        self._configuration.update(pi_kp=1.0, pi_ki=0.1)

        self.state = "idle"
        self.reason: str | None = None
        self._mission: dict | None = None
        self._sim: DiffDriveSim | None = None
        self._act = None
        self._index = 0
        self._telemetry_to: tuple | None = None
        self._last_heartbeat = 0.0
        self._telemetry_timestamp = 0.0

        handler = type("FakeRoverHandler", (_Handler,), {"rover": self})
        self._http = ThreadingHTTPServer((host, http_port), handler)
        self._http.daemon_threads = True
        self._heartbeat = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._heartbeat.bind((host, heartbeat_port))
        self._heartbeat.settimeout(0.1)
        self._telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._host = host

    # ------------------------------------------------------------------------ lifecycle

    @property
    def address(self) -> str:
        return f"http://{self._host}:{self._http.server_address[1]}"

    @property
    def heartbeat_port(self) -> int:
        return int(self._heartbeat.getsockname()[1])

    def start(self) -> "FakeRover":
        for target in (self._http.serve_forever, self._listen_heartbeats, self._control_loop,
                       self._telemetry_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def close(self) -> None:
        self._stopping.set()
        self._http.shutdown()
        self._http.server_close()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._heartbeat.close()
        self._telemetry.close()

    def __enter__(self) -> "FakeRover":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------------ commands

    def status(self) -> dict:
        with self._lock:
            pose = list(self._sim.estimated_pose) if self._sim else [0.0, 0.0, 0.0]
            return {"protocol": PROTOCOL_VERSION, "firmware": FIRMWARE, "fake": True,
                    "state": self.state, "reason": self.reason,
                    "mission_id": self._mission["mission_id"] if self._mission else None,
                    "session": self._mission["session"] if self._mission else None,
                    "pose": [round(v, 4) for v in pose],
                    "v": round(self._sim.v, 4) if self._sim else 0.0,
                    "w": round(self._sim.w, 4) if self._sim else 0.0,
                    "index": self._index, "battery_v": None, "calibrated": False,
                    "heartbeat_port": self.heartbeat_port,
                    "capabilities": ["async_calibration_v1", "config_readback_v1"]}

    def _wrong_state(self) -> tuple:
        return 409, {"error": f"not allowed in state {self.state!r}", "state": self.state}

    def load_mission(self, body: dict, client_host: str) -> tuple:
        with self._lock:
            if self.state not in ("idle", "ready", "done"):
                return self._wrong_state()
            errors = validate_mission(body)
            if errors:
                return 400, {"errors": errors}
            self._mission = body
            self._telemetry_to = (client_host, int(body["telemetry_port"]))
            self._sim, self._act, self._index = None, None, 0
            self.state, self.reason = "ready", None
            return 200, {"state": self.state}

    def begin(self) -> tuple:
        with self._lock:
            if self.state != "ready" or self._mission is None:
                return self._wrong_state()
            mission, limits = self._mission, self._mission["limits"]
            # The rover obeys the stricter of the mission's limits and its own.
            spec = dataclasses.replace(
                self.spec, v_max=min(limits["v_max"], self.spec.v_max, FIRMWARE_V_MAX),
                w_max=min(limits["w_max"], self.spec.w_max),
                a_max=min(limits["a_max"], self.spec.a_max),
                alpha_max=min(limits["alpha_max"], self.spec.alpha_max))
            self._sim = DiffDriveSim(self._navmesh, spec, self.noise, seed=self.seed + self._runs)
            self._runs += 1
            self._act = policy_act(mission["policy"])
            self._index = 0
            self._last_heartbeat = time.monotonic()
            if not self._sim.reset(mission["start"]):
                self.state, self.reason = "error", "start_in_collision"
                return 200, {"state": self.state, "reason": self.reason}
            self.state, self.reason = "running", None
            return 200, {"state": self.state}

    def halt(self) -> tuple:
        with self._lock:
            if self.state == "calibrating":
                self.state = "idle"
                if self._calibration is not None:
                    self._calibration["status"] = "stopped"
                return 200, {"state": "idle"}
            if self.state != "running":
                return self._wrong_state()
            self.state = "ready"
            return 200, {"state": self.state}

    def emergency_stop(self, body: dict) -> tuple:
        with self._lock:
            if self.state != "estop":  # a second E-STOP keeps the reason the rover latched on
                self.state, self.reason = "estop", str(body.get("reason") or "requested")
            return 200, {"state": self.state, "reason": self.reason}

    def reset(self) -> tuple:
        with self._lock:
            if self.state in ("running", "calibrating"):
                return self._wrong_state()
            self.state, self.reason = "idle", None
            self._mission, self._sim, self._act, self._index = None, None, None, 0
            return 200, {"state": self.state}

    def calibrate(self, body: dict) -> tuple:
        return 410, {"error": "Blocking calibration was removed. POST /calibrations, keep its "
                              "session heartbeat alive, and poll GET /calibrations/{id}."}

    def begin_calibration(self, body: dict) -> tuple:
        with self._lock:
            if self.state != "idle":
                return self._wrong_state()
            mode = body.get("mode")
            if mode not in ("straight", "spin", "wheel_step"):
                return 400, {"error": "mode must be straight, spin or wheel_step"}
            duration = body.get("duration_s", 1.0)
            speed = body.get("target_mps", 0.1)
            if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not math.isfinite(duration) or not 0.1 <= duration <= 3):
                return 400, {"error": "duration_s must be between 0.1 and 3"}
            if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                    or not math.isfinite(speed) or not 0 < speed <= min(self.spec.v_max, 0.2)):
                return 400, {"error": "target_mps must be positive and within the 0.2 m/s cap"}
            identifier = body.get("session", uuid.uuid4().hex)
            if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
                return 400, {"error": "session must contain 1..64 characters"}
            self._calibration = {"id": identifier, "session": identifier, "mode": mode,
                                 "status": "running", "source": "simulated", "elapsed_s": 0.0,
                                 "encoder_counts": [0.0, 0.0], "gyro_yaw_rad": 0.0,
                                 "duration_s": duration, "target_mps": speed, "samples": []}
            self._calibration_started = self._last_heartbeat = time.monotonic()
            self.state, self.reason = "calibrating", None
            self._sim = DiffDriveSim(self._navmesh, self.spec, self.noise, seed=self.seed)
            world = self._navmesh
            centre = [world["origin"][0] + world["width"] * world["cell"] / 2,
                      world["origin"][1] + world["height"] * world["cell"] / 2, 0.0]
            if not self._sim.reset(centre):
                self.state = "idle"
                self._calibration["status"] = "error"
                return 409, {"error": "calibration origin is blocked"}
            return 202, dict(self._calibration)

    def calibration(self, identifier: str) -> tuple:
        with self._lock:
            if self._calibration is None or self._calibration["id"] != identifier:
                return 404, {"error": "calibration not found"}
            return 200, json.loads(json.dumps(self._calibration))

    def _calibration_tick(self) -> None:
        c = self._calibration
        if not c or c["status"] != "running":
            return
        if self.state != "calibrating":
            c["status"], c["reason"] = self.state, self.reason
            return
        if time.monotonic() - self._last_heartbeat > self.deadman_s:
            self.state, self.reason = "estop", "heartbeat_lost"
            c["status"], c["reason"] = self.state, self.reason
            return
        if time.monotonic() - self._calibration_started > 25:
            self.state = "idle"
            c["status"], c["reason"] = "timeout", "calibration_timeout"
            return
        mode = c["mode"]
        v, w = ((0.0, 1.0) if mode == "spin" else
                (c["target_mps"] if mode == "wheel_step" else 0.15, 0.0))
        sim = self._sim
        if sim is None or not sim.step(v, w):
            self.state, self.reason = "error", "collision"
            c["status"], c["reason"] = self.state, self.reason
            return
        dt = self.spec.dt
        c["elapsed_s"] += dt
        per_metre = self.spec.counts_per_rev / (2 * math.pi * self.spec.wheel_radius_m)
        differential = sim.w * self.spec.wheelbase_m / 2
        left, right = sim.v - differential, sim.v + differential
        c["encoder_counts"][0] += left * dt * per_metre
        c["encoder_counts"][1] += right * dt * per_metre
        c["gyro_yaw_rad"] += sim.w * dt
        if len(c["samples"]) < 2000:
            c["samples"].append({"t": c["elapsed_s"], "target_mps": v,
                                 "left_mps": left, "right_mps": right})
        distance = sum(c["encoder_counts"]) / (2 * per_metre)
        done = (abs(c["gyro_yaw_rad"]) >= 6 * math.pi if mode == "spin" else
                c["elapsed_s"] >= c["duration_s"] if mode == "wheel_step" else distance >= 1)
        if done:
            c["status"] = "done"
            self.state = "idle"

    def configuration(self) -> dict:
        with self._lock:
            return {"settings": dict(self._configuration), "source": "simulated"}

    def configure(self, body: dict) -> tuple:
        with self._lock:
            if self.state != "idle":
                return self._wrong_state()
            if not body or any(key not in self._configuration for key in body):
                return 400, {"error": "unknown or empty configuration"}
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   or v <= 0 for v in body.values()):
                return 400, {"error": "configuration values must be finite and positive"}
            proposed = {**self._configuration, **body}
            fields = {key: proposed[key] for key in
                      ("wheel_radius_m", "wheelbase_m", "counts_per_rev")}
            try:
                updated = spec_from_dict({**self.spec.as_dict(), **fields})
            except ValueError as exc:
                return 400, {"error": str(exc)}
            self._configuration = proposed
            self.spec = updated
            return 200, {"saved": dict(self._configuration), "source": "simulated"}

    def set_world(self, body: dict) -> tuple:
        navmesh = body.get("navmesh")
        needed = ("origin", "cell", "width", "height", "grid")
        if not isinstance(navmesh, dict) or not all(key in navmesh for key in needed):
            return 400, {"errors": ["navmesh: must be a navmesh object"]}
        with self._lock:
            if self.state in ("running", "calibrating"):
                return self._wrong_state()
            self._navmesh = navmesh
            return 200, {"ok": True}

    # ------------------------------------------------------------------------- loops

    def _listen_heartbeats(self) -> None:
        while not self._stopping.is_set():
            try:
                data, _ = self._heartbeat.recvfrom(1024)
            except (socket.timeout, OSError):
                continue
            try:
                beat = json.loads(data)
            except ValueError:
                continue
            with self._lock:
                session = (self._calibration["session"] if self.state == "calibrating"
                           and self._calibration
                           else self._mission["session"] if self._mission else None)
                if session and isinstance(beat, dict) and beat.get("session") == session:
                    self._last_heartbeat = time.monotonic()

    def _control_loop(self) -> None:
        period = self.spec.dt / max(self.time_scale, 1e-6)
        next_tick = time.monotonic()
        while not self._stopping.is_set():
            with self._lock:
                self._calibration_tick()
                if self.state == "running" and self._sim is not None and self._mission is not None:
                    if time.monotonic() - self._last_heartbeat > self.deadman_s:
                        self.state, self.reason = "estop", "heartbeat_lost"
                    else:
                        mission = self._mission
                        command, _, self._index = control_step(
                            self._sim.estimated_pose, mission["path"], self._index,
                            mission["goal"], self._act, mission["controller"]["stop_radius_m"])
                        if command is None:
                            self.state = "done"
                        elif not self._sim.step(command[0], command[1]):
                            self.state, self.reason = "error", "collision"
            next_tick += period
            self._stopping.wait(max(0.0, next_tick - time.monotonic()))

    def _telemetry_frame(self) -> dict | None:
        with self._lock:
            if self.state == "idle" or self._mission is None or self._telemetry_to is None:
                return None
            sim = self._sim
            estimate = sim.estimated_pose if sim else tuple(self._mission["start"])
            # Terminal frames must advance too, even after simulated motion has stopped.
            self._telemetry_timestamp = max(
                self._telemetry_timestamp + 0.001, sim.t if sim else 0.0)
            frame = {"session": self._mission["session"], "t": self._telemetry_timestamp,
                     "x": round(estimate[0], 4), "y": round(estimate[1], 4),
                     "th": round(estimate[2], 4), "v": round(sim.v, 4) if sim else 0.0,
                     "w": round(sim.w, 4) if sim else 0.0, "state": self.state,
                     "reason": self.reason, "index": self._index, "battery_v": None}
            if sim is not None:
                frame["truth"] = [round(v, 4) for v in sim.true_pose]  # fake rover only
            return frame

    def _telemetry_loop(self) -> None:
        while not self._stopping.wait(1.0 / TELEMETRY_HZ):
            frame = self._telemetry_frame()
            if frame is None or self._telemetry_to is None:
                continue
            try:
                self._telemetry.sendto(json.dumps(frame).encode(), self._telemetry_to)
            except OSError:
                pass  # nobody listening: a real rover keeps driving on its heartbeat, not on this


class _Handler(BaseHTTPRequestHandler):
    rover: FakeRover  # set on the subclass FakeRover builds

    def log_message(self, *args) -> None:  # noqa: D401 - keep test output quiet
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        if self.path == "/status":
            self._send(200, self.rover.status())
        elif self.path == "/config":
            self._send(200, self.rover.configuration())
        elif self.path.startswith("/calibrations/"):
            self._send(*self.rover.calibration(self.path.rsplit("/", 1)[-1]))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            self._send(400, {"errors": ["payload: invalid JSON"]})
            return
        if not isinstance(body, dict):
            self._send(400, {"errors": ["payload: must be a JSON object"]})
            return
        rover = self.rover
        routes = {
            "/mission": lambda: rover.load_mission(body, self.client_address[0]),
            "/start": rover.begin,
            "/stop": rover.halt,
            "/estop": lambda: rover.emergency_stop(body),
            "/reset": rover.reset,
            "/calibrate": lambda: rover.calibrate(body),
            "/calibrations": lambda: rover.begin_calibration(body),
            "/config": lambda: rover.configure(body),
            "/sim/world": lambda: rover.set_world(body),
        }
        route = routes.get(self.path)
        if route is None:
            self._send(404, {"error": "not found"})
            return
        code, answer = route()
        self._send(code, answer)


def main() -> None:
    parser = argparse.ArgumentParser(description="A fake ScanBorn rover speaking the protocol.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--heartbeat-port", type=int, default=4210)
    parser.add_argument("--navmesh", help="a twin's navmesh.json to collide with")
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-noise", action="store_true", help="drive the perfect rover")
    args = parser.parse_args()

    navmesh = None
    if args.navmesh:
        with open(args.navmesh, encoding="utf-8") as f:
            navmesh = json.load(f)
    rover = FakeRover(navmesh=navmesh, noise=Noise() if args.no_noise else None, seed=args.seed,
                      time_scale=args.time_scale, host=args.host, http_port=args.port,
                      heartbeat_port=args.heartbeat_port).start()
    print(f"fake rover at {rover.address}, heartbeats on UDP {rover.heartbeat_port} (Ctrl+C stops)")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        rover.close()


if __name__ == "__main__":
    main()
