from policy.sim import DiffDriveSim, Noise
from twin.navmesh import is_blocked

from .base import ScanBornRobot


class SimRobot(ScanBornRobot):
    """The rover in the twin.

    With a navmesh and a robot spec it takes velocity commands through the differential-drive
    model (policy/sim.py): limits, wheel slip, gyro bias and latency included. `move()` still
    teleports to a waypoint, refusing blocked cells, for callers that predate the model.
    """

    supports_drive = True

    def __init__(self, navmesh: dict | None = None, start=(0.0, 0.0, 0.0), spec=None,
                 noise: Noise | None = None, seed: int = 0):
        self.navmesh = navmesh
        self.pose = tuple(start)
        self._sim = (DiffDriveSim(navmesh, spec, noise, seed)
                     if navmesh is not None and spec is not None else None)

    def connect(self) -> bool:
        return True

    def move(self, x: float, y: float, theta: float = 0.0) -> bool:
        """Teleport to a waypoint; refuses (returns False) if it is not traversable."""
        if self.navmesh and is_blocked(self.navmesh, x, y):
            return False
        self.pose = (x, y, theta)
        return True

    # --------------------------------------------------------------- velocity interface

    def _model(self) -> DiffDriveSim:
        if self._sim is None:
            raise RuntimeError("SimRobot needs a navmesh and a robot spec to take velocity "
                               "commands")
        return self._sim

    def reset(self, pose) -> bool:
        ok = self._model().reset(pose)
        self.pose = self._model().true_pose
        return ok

    def step(self, v: float, omega: float) -> bool:
        ok = self._model().step(v, omega)
        self.pose = self._model().true_pose
        return ok

    @property
    def estimated_pose(self) -> tuple:
        return self._model().estimated_pose

    @property
    def true_pose(self) -> tuple:
        return self._model().true_pose

    def capture_frame(self) -> bytes:
        return b"sim_frame"

    def get_pose(self) -> tuple:
        return self.pose
