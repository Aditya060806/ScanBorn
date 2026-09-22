"""A protocol rover — the ESP32 firmware or the fake rover — as the orchestrator talks to it.

docs/robot/rover-protocol.md: the control loop runs on the rover. This adapter loads a mission,
starts and stops it, sends the heartbeat that keeps it alive, and asks for its status. It never
sends velocity commands, so `supports_drive` is False: a WiFi hiccup must not become a steering
input.
"""
import json
import socket
import threading
from urllib.parse import urlsplit

import httpx

from robot.protocol import HEARTBEAT_PERIOD_S

from .base import ScanBornRobot


class RoverError(RuntimeError):
    """The rover refused a request. Carries its status code and its own explanation."""

    def __init__(self, status_code: int, detail):
        message = detail.get("error") or "; ".join(detail.get("errors", [])) \
            if isinstance(detail, dict) else str(detail)
        super().__init__(f"rover answered {status_code}: {message}")
        self.status_code = status_code
        self.detail = detail


class RoverRobot(ScanBornRobot):
    supports_drive = False

    def __init__(self, address: str, timeout: float = 3.0, http: httpx.Client | None = None):
        self.address = address.rstrip("/")
        self._http = http or httpx.Client(base_url=self.address, timeout=timeout)
        self._pose: tuple = (0.0, 0.0, 0.0)
        self._beat_stop = threading.Event()
        self._beat_thread: threading.Thread | None = None
        self.heartbeats_sent = 0

    # ----------------------------------------------------------------------- requests

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self._http.request(method, path, json=body if method == "POST" else None)
        try:
            answer = response.json()
        except ValueError:
            answer = {"error": response.text}
        if response.is_error:
            raise RoverError(response.status_code, answer)
        return answer

    def connect(self) -> bool:
        """True when the rover answers its status; False (not an exception) when it does not."""
        try:
            self.status()
        except (httpx.HTTPError, RoverError):
            return False
        return True

    def status(self) -> dict:
        status = self._call("GET", "/status")
        pose = status.get("pose") or [0.0, 0.0, 0.0]
        self._pose = tuple(float(v) for v in pose)
        return status

    def send_mission(self, payload: dict) -> dict:
        return self._call("POST", "/mission", payload)

    def start(self) -> dict:
        return self._call("POST", "/start", {})

    def stop(self) -> dict:
        return self._call("POST", "/stop", {})

    def estop(self, reason: str = "requested") -> dict:
        return self._call("POST", "/estop", {"reason": reason})

    def reset(self) -> dict:
        return self._call("POST", "/reset", {})

    def calibrate(self, mode: str) -> dict:
        return self._call("POST", "/calibrate", {"mode": mode})

    def begin_calibration(self, mode: str, session: str, **options) -> dict:
        return self._call("POST", "/calibrations", {"mode": mode, "session": session, **options})

    def calibration(self, identifier: str) -> dict:
        return self._call("GET", f"/calibrations/{identifier}")

    def configuration(self) -> dict:
        return self._call("GET", "/config")

    def configure(self, values: dict) -> dict:
        return self._call("POST", "/config", values)

    def set_world(self, navmesh: dict) -> dict:
        """Fake rover only: the twin's map to collide with. A real rover answers 404."""
        return self._call("POST", "/sim/world", {"navmesh": navmesh})

    # ---------------------------------------------------------------------- heartbeat

    def start_heartbeat(self, session: str, port: int | None = None,
                        period: float = HEARTBEAT_PERIOD_S) -> None:
        """Send the session's heartbeat every `period` until stop_heartbeat()."""
        self.stop_heartbeat()
        host = urlsplit(self.address).hostname or "127.0.0.1"
        target = (host, int(port if port is not None else self.status()["heartbeat_port"]))
        self._beat_stop = threading.Event()
        stop = self._beat_stop

        def beat() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            seq = 0
            try:
                while not stop.is_set():
                    try:
                        sock.sendto(json.dumps({"session": session, "seq": seq}).encode(), target)
                        self.heartbeats_sent += 1
                    except OSError:
                        pass  # the rover's deadman handles a network that is gone
                    seq += 1
                    stop.wait(period)
            finally:
                sock.close()

        self._beat_thread = threading.Thread(target=beat, daemon=True)
        self._beat_thread.start()

    def stop_heartbeat(self) -> None:
        self._beat_stop.set()
        if self._beat_thread is not None:
            self._beat_thread.join(timeout=1.0)
            self._beat_thread = None

    def close(self) -> None:
        self.stop_heartbeat()
        self._http.close()

    # ----------------------------------------------------------- ScanBornRobot interface

    def move(self, x: float, y: float, theta: float = 0.0) -> bool:
        """Refused: a protocol rover drives its own mission and takes no waypoint commands."""
        return False

    def capture_frame(self) -> bytes:
        return b""

    def get_pose(self) -> tuple:
        """The rover's estimated pose from its last status."""
        return self._pose
