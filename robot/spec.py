"""The robot the simulation behaves like: one file of numbers, validated on load.

`robot/specs/<name>.yaml` is the only place the rover's size, limits and noise live. Values are
placeholders until Phase 6 calibration writes measured ones, and the file says which through
`source` (and `noise.source`), so nothing downstream can present an assumption as a measurement.
"""
import math
import os
import re
from dataclasses import asdict, dataclass

import yaml

SPEC_DIR = os.path.join(os.path.dirname(__file__), "specs")

# The adapters' absolute ceiling (robot/adapters/base.py:MAX_LINEAR_SPEED). Repeated rather than
# imported so loading a spec never imports the adapter package.
SAFETY_MAX_SPEED = 0.5

_POSITIVE = ("wheel_radius_m", "wheelbase_m", "counts_per_rev", "footprint_radius_m", "height_m",
             "v_max", "w_max", "a_max", "alpha_max", "control_hz")
_NON_NEGATIVE = ("step_height_m",)
_NOISE = ("wheel_slip", "gyro_bias_rad_s", "latency_ticks", "start_xy_m", "start_heading_rad")
_INTEGER = ("counts_per_rev", "latency_ticks")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class NoiseSpec:
    source: str
    wheel_slip: float
    gyro_bias_rad_s: float
    latency_ticks: int
    start_xy_m: float
    start_heading_rad: float


@dataclass(frozen=True)
class RobotSpec:
    name: str
    source: str
    wheel_radius_m: float
    wheelbase_m: float
    counts_per_rev: int
    footprint_radius_m: float
    height_m: float
    step_height_m: float
    v_max: float
    w_max: float
    a_max: float
    alpha_max: float
    control_hz: float
    noise: NoiseSpec

    @property
    def dt(self) -> float:
        return 1.0 / self.control_hz

    def as_dict(self) -> dict:
        return asdict(self)


def _number(doc: dict, key: str, where: str, positive: bool):
    if key not in doc:
        raise ValueError(f"{where}.{key} is missing")
    value = doc[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where}.{key} must be a finite number, got {value!r}")
    if (value <= 0) if positive else (value < 0):
        raise ValueError(f"{where}.{key} must be {'positive' if positive else 'zero or more'}, "
                         f"got {value}")
    if key in _INTEGER:
        if int(value) != value:
            raise ValueError(f"{where}.{key} must be a whole number, got {value}")
        return int(value)
    return float(value)


def spec_from_dict(doc: dict, where: str = "spec") -> RobotSpec:
    """Validate a spec document; every error names the field it is about."""
    fields = {key: _number(doc, key, where, True) for key in _POSITIVE}
    fields.update({key: _number(doc, key, where, False) for key in _NON_NEGATIVE})
    noise_doc = doc.get("noise")
    if not isinstance(noise_doc, dict):
        raise ValueError(f"{where}.noise is missing")
    noise = NoiseSpec(source=str(noise_doc.get("source", "assumed")),
                      **{key: _number(noise_doc, key, f"{where}.noise", False) for key in _NOISE})
    if fields["v_max"] > SAFETY_MAX_SPEED:
        raise ValueError(f"{where}.v_max {fields['v_max']} m/s exceeds the "
                         f"{SAFETY_MAX_SPEED} m/s safety ceiling")
    return RobotSpec(name=str(doc.get("name", where)), source=str(doc.get("source", "placeholder")),
                     noise=noise, **fields)


def load_spec(name: str = "rover") -> RobotSpec:
    """robot/specs/<name>.yaml. The name is checked so a request can never reach another path."""
    if not _NAME.match(name):
        raise ValueError(f"invalid robot spec name {name!r}")
    path = os.path.join(SPEC_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no robot spec named {name!r}")
    with open(path, encoding="utf-8") as f:
        return spec_from_dict(yaml.safe_load(f) or {}, where=name)
