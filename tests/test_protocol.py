import copy
import json
import os

import numpy as np
import pytest

from policy.controller import K_V, K_W, control_step
from policy.finetune.train_bc import LinearPolicy, dequantize_int8
from policy.planning import plan_mission
from robot.protocol import (
    FIRMWARE_V_MAX,
    MAX_PATH_POINTS,
    PROTOCOL_VERSION,
    mission_payload,
    policy_act,
    validate_mission,
)
from robot.spec import load_spec

SPEC = load_spec("rover")
OPEN = {"origin": [0.0, 0.0], "cell": 0.1, "width": 40, "height": 40,
        "grid": [[0] * 40 for _ in range(40)], "profile": {"robot_radius": 0.2}}
GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "rover_golden.json")


def bundle() -> dict:
    quantized = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    return {"W": quantized["W"]["data"].tolist(), "W_scale": quantized["W"]["scale"],
            "b": quantized["b"]["data"].tolist(), "b_scale": quantized["b"]["scale"]}


def payload() -> dict:
    mission = {**plan_mission(OPEN, (0.6, 0.6, 0.0), (3.0, 2.0)), "mission_id": "m1"}
    return mission_payload(mission, bundle(), SPEC, session="s1", telemetry_port=5005)


def test_a_payload_built_from_a_mission_is_valid_json_the_rover_accepts():
    body = json.loads(json.dumps(payload()))
    assert validate_mission(body) == []
    assert body["protocol"] == PROTOCOL_VERSION
    assert body["limits"]["v_max"] <= FIRMWARE_V_MAX
    planned = plan_mission(OPEN, (0.6, 0.6, 0.0), (3.0, 2.0))
    assert body["start"][2] == pytest.approx(planned["start"][2])


@pytest.mark.parametrize("change, field", [
    (lambda p: p.update(protocol=2), "protocol"),
    (lambda p: p.update(protocol=1.0), "protocol"),   # the firmware accepts only the integer
    (lambda p: p.update(protocol=True), "protocol"),
    (lambda p: p.update(session=""), "session"),
    (lambda p: p.update(session="s" * 65), "session"),  # the firmware's buffer holds 64
    (lambda p: p.update(mission_id=7), "mission_id"),
    (lambda p: p.update(start=[0.0, 0.0]), "start"),
    (lambda p: p["goal"].__setitem__(0, float("nan")), "goal"),
    (lambda p: p.update(path=[[0.0, 0.0]]), "path"),
    (lambda p: p["path"].__setitem__(1, [0.0, "x"]), "path"),
    (lambda p: p["policy"]["W"][0].__setitem__(0, 128), "policy.W"),
    (lambda p: p["policy"]["b"].__setitem__(1, True), "policy.b"),
    (lambda p: p["policy"].update(W_scale=0), "policy.W_scale"),
    (lambda p: p["limits"].update(a_max=-1), "limits.a_max"),
    (lambda p: p["controller"].pop("lookahead_m"), "controller.lookahead_m"),
    (lambda p: p.update(telemetry_port=70000), "telemetry_port"),
])
def test_each_bad_field_is_named(change, field):
    body = copy.deepcopy(payload())
    change(body)
    errors = validate_mission(body)
    assert errors and any(e.startswith(field) for e in errors), errors


def test_a_route_too_long_or_too_fast_for_the_rover_is_refused():
    body = payload()
    body["path"] = [[i * 0.1, 0.0] for i in range(MAX_PATH_POINTS + 1)]
    assert any(e.startswith("path") for e in validate_mission(body))
    body = payload()
    body["limits"]["v_max"] = FIRMWARE_V_MAX + 0.05
    assert any("cap" in e for e in validate_mission(body))
    assert validate_mission(["not", "an", "object"]) == ["payload: must be a JSON object"]


def test_the_payload_policy_is_the_exported_int8_policy():
    body = bundle()
    act = policy_act(body)
    quantized = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    reference = dequantize_int8(quantized)
    for obs in ([0.3, 0.1], [-0.2, 1.5], [0.0, -3.0]):
        assert act(obs) == pytest.approx(list(reference.act(obs)), abs=1e-12)


def test_control_step_reproduces_the_golden_vectors():
    with open(GOLDEN, encoding="utf-8") as f:
        golden = json.load(f)
    act = policy_act(golden["policy_int8"])
    path = [tuple(p) for p in golden["path"]]
    for row in golden["rows"]:
        command, obs, index = control_step(row["pose"], path, row["path_index_in"], path[-1], act)
        assert index == row["path_index_out"], row["case"]
        assert obs == pytest.approx(row["observation"], abs=1e-9), row["case"]
        assert command == pytest.approx(row["action"], abs=1e-9), row["case"]


def test_control_step_stops_inside_the_stop_radius():
    path = [(0.0, 0.0), (1.0, 0.0)]
    command, obs, index = control_step((0.99, 0.005, 0.0), path, 1, (1.0, 0.0), lambda o: [1, 1])
    assert command is None and obs is None and index == 1
