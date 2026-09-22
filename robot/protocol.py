"""The rover protocol's payloads: the executable half of docs/robot/rover-protocol.md.

Shared by the orchestrator (which builds missions), the fake rover (which validates and runs them)
and the tests, so the three can never disagree about a field. The ESP32 firmware implements the
same checks in C++ (firmware/scanborn_rover/mission.cpp).
"""
import math

import numpy as np

PROTOCOL_VERSION = 1
MAX_PATH_POINTS = 256        # a 25 m route at the 10 cm path spacing
MAX_ID_LENGTH = 64           # session and mission_id: the firmware keeps them in fixed buffers
FIRMWARE_V_MAX = 0.30        # m/s: the rover's own cap, whatever a mission asks for
HEARTBEAT_PORT = 4210        # UDP, orchestrator -> rover
HEARTBEAT_PERIOD_S = 0.2
DEADMAN_S = 1.0              # running without a heartbeat for this long latches an E-STOP
TELEMETRY_HZ = 20
STATES = ("idle", "ready", "running", "done", "estop", "error")


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int8(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -127 <= value <= 127


def _positive(container: dict, key: str, where: str, errors: list) -> None:
    value = container.get(key)
    if not (isinstance(value, (int, float)) and _finite(value) and value > 0):
        errors.append(f"{where}.{key}: must be a positive number")


def _point(payload: dict, key: str, size: int, errors: list) -> None:
    value = payload.get(key)
    if not (isinstance(value, list) and len(value) == size and all(_finite(v) for v in value)):
        errors.append(f"{key}: must be {size} finite numbers")


def validate_mission(payload, v_cap: float = FIRMWARE_V_MAX) -> list:
    """Every problem with a mission payload, each naming its field; an empty list means valid."""
    if not isinstance(payload, dict):
        return ["payload: must be a JSON object"]
    errors: list = []
    protocol = payload.get("protocol")
    # An integer: 1.0 and true compare equal to 1 in Python but are not what the firmware accepts.
    if not (isinstance(protocol, int) and not isinstance(protocol, bool)
            and protocol == PROTOCOL_VERSION):
        errors.append(f"protocol: must be {PROTOCOL_VERSION}")
    for key in ("session", "mission_id"):
        value = payload.get(key)
        if not (isinstance(value, str) and value):
            errors.append(f"{key}: must be a non-empty string")
        elif len(value) > MAX_ID_LENGTH:
            errors.append(f"{key}: at most {MAX_ID_LENGTH} characters")
    _point(payload, "start", 3, errors)
    _point(payload, "goal", 2, errors)

    path = payload.get("path")
    if not isinstance(path, list) or len(path) < 2:
        errors.append("path: must have at least 2 points")
    elif len(path) > MAX_PATH_POINTS:
        errors.append(f"path: at most {MAX_PATH_POINTS} points (got {len(path)})")
    elif not all(isinstance(p, list) and len(p) == 2 and all(_finite(v) for v in p) for p in path):
        errors.append("path: every point must be [x, y] finite numbers")

    policy = payload.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy: must be an object")
    else:
        weights, bias = policy.get("W"), policy.get("b")
        if not (isinstance(weights, list) and len(weights) == 2 and all(
                isinstance(row, list) and len(row) == 2 and all(_int8(v) for v in row)
                for row in weights)):
            errors.append("policy.W: must be a 2x2 array of int8 (-127..127)")
        if not (isinstance(bias, list) and len(bias) == 2 and all(_int8(v) for v in bias)):
            errors.append("policy.b: must be 2 int8 values (-127..127)")
        _positive(policy, "W_scale", "policy", errors)
        _positive(policy, "b_scale", "policy", errors)

    limits = payload.get("limits")
    if not isinstance(limits, dict):
        errors.append("limits: must be an object")
    else:
        for key in ("v_max", "w_max", "a_max", "alpha_max"):
            _positive(limits, key, "limits", errors)
        if _finite(limits.get("v_max")) and limits["v_max"] > v_cap:
            errors.append(f"limits.v_max: above the rover's cap of {v_cap} m/s")

    controller = payload.get("controller")
    if not isinstance(controller, dict):
        errors.append("controller: must be an object")
    else:
        _positive(controller, "lookahead_m", "controller", errors)
        _positive(controller, "stop_radius_m", "controller", errors)

    port = payload.get("telemetry_port")
    if not (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
        errors.append("telemetry_port: must be a port number 1..65535")
    return errors


def load_bundle(path: str) -> dict:
    """The exported int8 policy (policy_int8.npz) as protocol fields."""
    with np.load(path) as npz:
        return {"W": [[int(v) for v in row] for row in npz["W"]], "W_scale": float(npz["W_scale"]),
                "b": [int(v) for v in npz["b"]], "b_scale": float(npz["b_scale"])}


def mission_payload(mission: dict, policy: dict, spec, session: str, telemetry_port: int) -> dict:
    """A `POST /mission` body from a stored mission, an int8 policy and the robot spec."""
    from policy.controller import LOOKAHEAD, STOP_RADIUS

    start = list(mission["start"]) + [0.0] * (3 - len(mission["start"]))
    return {
        "protocol": PROTOCOL_VERSION,
        "session": session,
        "mission_id": mission["mission_id"],
        "start": [float(v) for v in start[:3]],
        "goal": [float(v) for v in mission["goal"]],
        "path": [[round(float(x), 4), round(float(y), 4)] for x, y in mission["path"]],
        "policy": policy,
        "limits": {"v_max": min(spec.v_max, FIRMWARE_V_MAX), "w_max": spec.w_max,
                   "a_max": spec.a_max, "alpha_max": spec.alpha_max},
        "controller": {"lookahead_m": LOOKAHEAD, "stop_radius_m": STOP_RADIUS},
        "telemetry_port": int(telemetry_port),
    }


def policy_act(policy: dict):
    """The int8 policy from a payload as `act(obs) -> [v, ω]` — what the rover computes."""
    weights = np.asarray(policy["W"], dtype=np.float64) * float(policy["W_scale"])
    bias = np.asarray(policy["b"], dtype=np.float64) * float(policy["b_scale"])

    def act(obs):
        out = weights @ np.asarray(obs, dtype=np.float64) + bias
        return [float(out[0]), float(out[1])]

    return act
