"""Write the rover's golden vectors: numbers the firmware must reproduce.

tests/fixtures/rover_golden.json. For a quantized copy of the demonstrator policy and a sample
L-shaped route, each row takes a rover pose through the per-tick chain: lookahead index,
observation, int8 policy output, command after limits, wheel speeds, wheel rates, encoder counts per
tick. The odometry section takes a sequence of commands through the limits, the rate limits and the
midpoint-heading pose update, across the ±π wrap.

firmware/scanborn_rover/golden.h holds the same numbers as C++ for the firmware self-test
(selftest.h). firmware/scanborn_rover/rover_spec.h is robot/specs/rover.yaml as the firmware's
defaults, so the firmware never keeps its own copy of the rover.

tests/test_rover_golden.py checks that the Python code still produces the committed JSON and that
both headers are what this script writes. Re-run after changing the controller, the policy format
or robot/specs/rover.yaml.

    python scripts/gen_rover_golden.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures", "rover_golden.json")
GOLDEN_H = os.path.join(ROOT, "firmware", "scanborn_rover", "golden.h")
SPEC_H = os.path.join(ROOT, "firmware", "scanborn_rover", "rover_spec.h")

# (what the case exercises, pose x/y/heading, path index the rover had reached)
CASES = [
    ("at the start, facing along the route", (0.0, 0.0, 0.0), 0),
    ("heading 30 degrees left of the route", (0.3, 0.05, 0.5235987755982988), 0),
    ("facing backwards: no forward speed, turns in place", (0.5, 0.0, 3.141592653589793), 0),
    ("right of the route", (0.6, -0.15, 0.0), 3),
    ("approaching the corner", (0.85, 0.0, 0.0), 5),
    ("past the corner, heading up the second leg", (1.0, 0.4, 1.5707963267948966), 10),
    ("close to the goal", (1.0, 0.95, 1.5707963267948966), 15),
]

# Ten ticks each: a hard left from a heading near π (over w_max, so it crosses the wrap), a right
# turn, a reverse command (clamped to zero speed), then straight on.
ODOMETRY_START = (0.1, -0.2, 3.0)
ODOMETRY_COMMANDS = ([(0.3, 2.5)] * 10 + [(0.1, -3.0)] * 10 + [(-0.2, 0.5)] * 10
                     + [(0.25, 0.0)] * 10)


def build() -> dict:
    import numpy as np

    from policy.controller import GOAL_TOLERANCE, K_V, K_W, LOOKAHEAD, STOP_RADIUS, observe
    from policy.finetune.train_bc import LinearPolicy, dequantize_int8
    from policy.planning import densify
    from policy.sim import _advance, _clip
    from robot.kinematics import counts_per_tick, limit_command, wheel_rates, wheel_speeds
    from robot.spec import load_spec

    spec = load_spec("rover")
    bundle = LinearPolicy(np.diag([K_V, K_W]), np.zeros(2)).quantize_int8()
    policy = dequantize_int8(bundle)
    path = densify([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], 0.1)

    rows = []
    for case, pose, index in CASES:
        obs, index_out = observe(pose, path, index)
        action = [float(a) for a in policy.act(obs)]
        v, omega = limit_command(action[0], action[1], spec)
        left, right = wheel_speeds(v, omega, spec)
        left_rate, right_rate = wheel_rates(left, right, spec)
        rows.append({
            "case": case, "pose": list(pose), "path_index_in": index,
            "path_index_out": index_out, "observation": [float(o) for o in obs],
            "action": action, "command": [v, omega], "wheel_mps": [left, right],
            "wheel_radps": [left_rate, right_rate],
            "encoder_counts_per_tick": [counts_per_tick(left_rate, spec),
                                        counts_per_tick(right_rate, spec)],
        })

    # The same arithmetic DiffDriveSim.step applies to its estimate, without the noise.
    odometry_pose = list(ODOMETRY_START)
    body_v = body_w = 0.0
    ticks = []
    for v_cmd, w_cmd in ODOMETRY_COMMANDS:
        v_target, w_target = limit_command(v_cmd, w_cmd, spec)
        body_v += _clip(v_target - body_v, spec.a_max * spec.dt)
        body_w += _clip(w_target - body_w, spec.alpha_max * spec.dt)
        _advance(odometry_pose, body_v, body_w, spec.dt)
        ticks.append({"command": [v_cmd, w_cmd], "body": [body_v, body_w],
                      "pose": list(odometry_pose)})

    return {
        "generated_by": "python scripts/gen_rover_golden.py",
        "units": "metres, radians, seconds; heading counter-clockwise from +x",
        "spec": {"name": spec.name, "source": spec.source, "wheel_radius_m": spec.wheel_radius_m,
                 "wheelbase_m": spec.wheelbase_m, "counts_per_rev": spec.counts_per_rev,
                 "v_max": spec.v_max, "w_max": spec.w_max, "a_max": spec.a_max,
                 "alpha_max": spec.alpha_max, "control_hz": spec.control_hz, "dt": spec.dt},
        "controller": {"lookahead_m": LOOKAHEAD, "goal_tolerance_m": GOAL_TOLERANCE,
                       "stop_radius_m": STOP_RADIUS},
        "policy_int8": {"W": bundle["W"]["data"].tolist(), "W_scale": bundle["W"]["scale"],
                        "b": bundle["b"]["data"].tolist(), "b_scale": bundle["b"]["scale"]},
        "path": [list(p) for p in path],
        "rows": rows,
        "odometry": {"start": list(ODOMETRY_START), "ticks": ticks},
    }


def _d(value) -> str:
    """A C++ double literal that reads back as exactly this float."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {value}")
    return repr(value)


def _pair(values) -> str:
    return "{" + ", ".join(_d(v) for v in values) + "}"


def golden_header(document: dict) -> str:
    """firmware/scanborn_rover/golden.h for the self-test, from the golden document."""
    spec, controller, policy = document["spec"], document["controller"], document["policy_int8"]
    lines = [
        "// Generated by python scripts/gen_rover_golden.py from tests/fixtures/rover_golden.json.",
        "// Do not edit. selftest.h checks rover_math.h against these numbers.",
        "#pragma once",
        "",
        '#include "rover_math.h"',
        "",
        "namespace golden {",
        "",
        "const rover::Spec SPEC = {" + ", ".join(_d(spec[key]) for key in (
            "wheel_radius_m", "wheelbase_m", "counts_per_rev", "v_max", "w_max", "a_max",
            "alpha_max", "dt")) + "};",
        f"const double LOOKAHEAD_M = {_d(controller['lookahead_m'])};",
        f"const double STOP_RADIUS_M = {_d(controller['stop_radius_m'])};",
        "const rover::Policy POLICY = {{"
        + ", ".join("{" + ", ".join(str(int(v)) for v in row) + "}" for row in policy["W"])
        + f"}}, {_d(policy['W_scale'])}, {{"
        + ", ".join(str(int(v)) for v in policy["b"]) + f"}}, {_d(policy['b_scale'])}}};",
        "",
        f"const int PATH_COUNT = {len(document['path'])};",
        "const rover::Point PATH[] = {",
        *[f"    {_pair(point)}," for point in document["path"]],
        "};",
        "",
        "struct Row {",
        "  const char* name;",
        "  rover::Pose pose;",
        "  int index_in;",
        "  int index_out;",
        "  double observation[2];",
        "  double action[2];",
        "  double command[2];",
        "  double wheel_mps[2];",
        "  double wheel_radps[2];",
        "  double encoder_counts_per_tick[2];",
        "};",
        "",
        f"const int ROW_COUNT = {len(document['rows'])};",
        "const Row ROWS[] = {",
    ]
    for row in document["rows"]:
        fields = [json.dumps(row["case"]), _pair(row["pose"]), str(row["path_index_in"]),
                  str(row["path_index_out"])]
        fields += [_pair(row[key]) for key in ("observation", "action", "command", "wheel_mps",
                                               "wheel_radps", "encoder_counts_per_tick")]
        lines.append("    {" + ", ".join(fields) + "},")
    odometry = document["odometry"]
    lines += [
        "};",
        "",
        "struct OdometryTick {",
        "  rover::Command command;  // sent",
        "  rover::Command body;     // after the limits and the rate limits",
        "  rover::Pose pose;        // after the pose update",
        "};",
        "",
        f"const rover::Pose ODOMETRY_START = {_pair(odometry['start'])};",
        f"const int ODOMETRY_COUNT = {len(odometry['ticks'])};",
        "const OdometryTick ODOMETRY[] = {",
        *[f"    {{{_pair(t['command'])}, {_pair(t['body'])}, {_pair(t['pose'])}}},"
          for t in odometry["ticks"]],
        "};",
        "",
        "}  // namespace golden",
        "",
    ]
    return "\n".join(lines)


def spec_header(spec) -> str:
    """firmware/scanborn_rover/rover_spec.h: the robot spec as the firmware's defaults."""
    return "\n".join([
        "// Generated by python scripts/gen_rover_golden.py from robot/specs/rover.yaml.",
        "// Do not edit.",
        f"// source: {spec.source}. Until Phase 6 measures the rover these are that file's values;",
        "// measured ones are written to flash with POST /config and win over these at boot.",
        "#pragma once",
        "",
        "namespace rover_spec {",
        "",
        f"const char* const NAME = {json.dumps(spec.name)};",
        f"const char* const SOURCE = {json.dumps(spec.source)};",
        f"const double WHEEL_RADIUS_M = {_d(spec.wheel_radius_m)};",
        f"const double WHEELBASE_M = {_d(spec.wheelbase_m)};",
        f"const double COUNTS_PER_REV = {_d(spec.counts_per_rev)};",
        f"const double FOOTPRINT_RADIUS_M = {_d(spec.footprint_radius_m)};",
        f"const double V_MAX = {_d(spec.v_max)};",
        f"const double W_MAX = {_d(spec.w_max)};",
        f"const double A_MAX = {_d(spec.a_max)};",
        f"const double ALPHA_MAX = {_d(spec.alpha_max)};",
        f"const int CONTROL_HZ = {int(spec.control_hz)};",
        "",
        "}  // namespace rover_spec",
        "",
    ])


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> None:
    sys.path.insert(0, ROOT)
    from robot.spec import load_spec

    document = build()
    _write(OUT, json.dumps(document, indent=2) + "\n")
    _write(GOLDEN_H, golden_header(document))
    _write(SPEC_H, spec_header(load_spec("rover")))
    print(f"wrote {OUT}: {len(document['rows'])} rows, {len(document['odometry']['ticks'])} "
          f"odometry ticks, spec {document['spec']['source']}; and {GOLDEN_H}, {SPEC_H}")


if __name__ == "__main__":
    main()
