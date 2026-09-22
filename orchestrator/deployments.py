"""Live rover drives: one session each — telemetry in, heartbeats out, a result at the end.

docs/robot/rover-protocol.md: the rover drives itself. The orchestrator loads the mission, starts
it, keeps it alive with heartbeats, relays its telemetry to the dashboard, and records the result.

One UDP socket (`SCANBORN_TELEMETRY_PORT`, default 5005; 0 picks a free port, which is what tests
use) receives telemetry for every drive and routes each datagram by its session token, so a stale
datagram from an earlier drive can never land in the current one. A session ends when the rover
reports a final state, when the operator stops it, or when its telemetry falls silent — and its
`on_finish` callback runs exactly once, which is where the `runs` row is written.

In-memory and single-process, like orchestrator/jobs.py.
"""
import json
import math
import os
import secrets
import socket
import threading
import time

FINAL_STATES = ("done", "estop", "error")
SILENCE_S = 3.0          # a running drive whose telemetry is gone this long ends as "lost"
MAX_TRACE_POINTS = 20_000


class Session:
    """One drive of one mission on one rover."""

    def __init__(self, deployment_id: str, mission_id: str, robot, sim_trace, on_finish,
                 robot_id: str | None = None):
        self.deployment_id = deployment_id
        self.mission_id = mission_id
        self.robot = robot
        self.robot_id = robot_id
        self.token = secrets.token_hex(8)
        self.sim_trace = sim_trace or []
        self.on_finish = on_finish
        self.state = "starting"
        self.reason: str | None = None
        self.fake = False
        self.trace: list = []
        self.truth: list = []
        self.frames = 0
        self.last_frame: dict | None = None
        self.started_at = time.monotonic()
        self.last_heard = time.monotonic()
        self.finished = False
        self.recording_error = None
        self.samples: list = []
        self.last_timestamp = -1.0

    def absorb(self, frame: dict) -> bool:
        for field in ("t", "x", "y", "th", "v", "w"):
            value = frame.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                return False
        if frame["t"] < 0 or frame["t"] <= self.last_timestamp:
            return False
        if frame.get("state") not in ("idle", "ready", "running", "done", "estop", "error"):
            return False
        truth = frame.get("truth")
        if truth is not None and (not isinstance(truth, list) or len(truth) < 2 or
                                  any(isinstance(v, bool) or not isinstance(v, (int, float))
                                      or not math.isfinite(v) for v in truth)):
            return False
        self.last_timestamp = frame["t"]
        self.frames += 1
        self.last_heard = time.monotonic()
        self.last_frame = frame
        self.state = str(frame.get("state") or self.state)
        self.reason = frame.get("reason")
        if len(self.trace) < MAX_TRACE_POINTS:
            self.trace.append([frame.get("x"), frame.get("y")])
            if isinstance(frame.get("truth"), list):
                self.fake = True
                self.truth.append(frame["truth"][:2])
            self.samples.append({key: frame[key] for key in ("t", "x", "y", "th")})
        return True

    def snapshot(self) -> dict:
        return {"deployment_id": self.deployment_id, "mission_id": self.mission_id,
                "state": self.state, "reason": self.reason, "finished": self.finished,
                "fake": self.fake, "frames": self.frames, "trace": list(self.trace),
                "last": self.last_frame, "pose_samples": list(self.samples),
                "recording_error": self.recording_error,
                "elapsed_s": round(time.monotonic() - self.started_at, 2)}


class DeploymentRegistry:
    def __init__(self, port: int | None = None, silence_s: float = SILENCE_S):
        self._port_setting = port
        self.silence_s = silence_s
        self._lock = threading.RLock()
        self._by_token: dict = {}
        self._by_deployment: dict = {}
        self._listeners: dict = {}
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list = []

    # ------------------------------------------------------------------------ socket

    def ensure_listening(self) -> int:
        """Bind the telemetry socket on first use; returns the port rovers should send to."""
        with self._lock:
            if self._sock is None:
                port = (self._port_setting if self._port_setting is not None
                        else int(os.environ.get("SCANBORN_TELEMETRY_PORT", "5005")))
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("0.0.0.0", port))
                sock.settimeout(0.2)
                self._sock = sock
                # A fresh stop event per bind, so the registry can listen again after close().
                self._stop = threading.Event()
                for target in (self._read, self._watch):
                    thread = threading.Thread(target=target, args=(sock, self._stop), daemon=True)
                    thread.start()
                    self._threads.append(thread)
            return int(self._sock.getsockname()[1])

    def _read(self, sock: socket.socket, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
                frame = json.loads(data)
            except (socket.timeout, OSError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            with self._lock:
                session = self._by_token.get(frame.get("session"))
                if session is None or session.finished:
                    continue
                if not session.absorb(frame):
                    continue
            self._notify(session.deployment_id, {"type": "telemetry", **frame})
            if session.state in FINAL_STATES:
                self.finish(session.deployment_id)

    def _watch(self, _sock: socket.socket, stop: threading.Event) -> None:
        while not stop.wait(0.2):
            with self._lock:
                silent = [s for s in self._by_deployment.values()
                          if not s.finished and time.monotonic() - s.last_heard > self.silence_s]
            for session in silent:
                self.finish(session.deployment_id, state="lost", reason="telemetry_silent")

    # ----------------------------------------------------------------------- sessions

    def open(self, deployment_id: str, mission_id: str, robot, sim_trace=None,
             on_finish=None, robot_id: str | None = None) -> Session:
        session = Session(deployment_id, mission_id, robot, sim_trace, on_finish, robot_id)
        with self._lock:
            if robot_id and self.active_for_robot(robot_id) is not None:
                raise ValueError("robot already reserved by an active deployment")
            self._by_token[session.token] = session
            self._by_deployment[deployment_id] = session
        return session

    def get(self, deployment_id: str) -> Session | None:
        with self._lock:
            return self._by_deployment.get(deployment_id)

    def active_for_robot(self, robot_id: str) -> Session | None:
        """The drive a robot is on right now, if any."""
        with self._lock:
            for session in self._by_deployment.values():
                if session.robot_id == robot_id and not session.finished:
                    return session
        return None

    def finish(self, deployment_id: str, state: str | None = None,
               reason: str | None = None) -> Session | None:
        """End a session once: stop its heartbeat, record its end, tell listeners."""
        with self._lock:
            session = self._by_deployment.get(deployment_id)
            if session is None or session.finished:
                return session
            session.finished = True
            if state is not None:
                session.state, session.reason = state, reason
            self._by_token.pop(session.token, None)
        try:
            session.robot.stop_heartbeat()
        except Exception:  # noqa: BLE001 - ending a session must not fail on a dead link
            pass
        if session.on_finish is not None:
            try:
                session.on_finish(session)
            except Exception as exc:  # noqa: BLE001 - report persistence failure after stopping
                session.recording_error = str(exc)
        self._notify(deployment_id, {"type": "finished", **session.snapshot()})
        return session

    def stop(self, deployment_id: str) -> Session | None:
        """Operator stop: the rover halts with its mission kept, and the session ends."""
        session = self.get(deployment_id)
        if session is None or session.finished:
            return session
        try:
            session.robot.stop()
        except Exception:  # noqa: BLE001 - a rover that cannot hear us stops on its deadman
            pass
        return self.finish(deployment_id, state="stopped", reason="operator")

    def estop(self, deployment_id: str, reason: str = "dashboard") -> Session | None:
        session = self.get(deployment_id)
        if session is None:
            return None
        try:
            session.robot.estop(reason)
        except Exception:  # noqa: BLE001 - the heartbeat stopping below still stops the rover
            pass
        return self.finish(deployment_id, state="estop", reason=reason)

    # ---------------------------------------------------------------------- listeners

    def subscribe(self, deployment_id: str, callback):
        """`callback(message)` for every telemetry frame and the final one. Returns unsubscribe."""
        with self._lock:
            self._listeners.setdefault(deployment_id, []).append(callback)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._listeners.get(deployment_id, [])
                if callback in listeners:
                    listeners.remove(callback)

        return unsubscribe

    def _notify(self, deployment_id: str, message: dict) -> None:
        with self._lock:
            listeners = list(self._listeners.get(deployment_id, []))
        for callback in listeners:
            try:
                callback(message)
            except Exception:  # noqa: BLE001 - a closed socket must not stop the others
                pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            sessions = [s for s in self._by_deployment.values() if not s.finished]
        for session in sessions:
            self.finish(session.deployment_id, state="lost", reason="orchestrator_shutdown")
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads = []
        with self._lock:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
