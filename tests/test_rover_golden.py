"""The committed firmware golden vectors must still be what the Python reference computes.

If this fails after a deliberate change to the controller, the policy format or the rover spec,
regenerate with `python scripts/gen_rover_golden.py` — and the firmware must then match the new
file too.
"""
import json
import os

import pytest

from robot.kinematics import counts_per_tick, limit_command, wheel_rates, wheel_speeds
from robot.spec import load_spec
from scripts.gen_rover_golden import GOLDEN_H, SPEC_H, build, golden_header, spec_header

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rover_golden.json")
NUMERIC = ("pose", "observation", "action", "command", "wheel_mps", "wheel_radps",
           "encoder_counts_per_tick")


def committed() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_the_golden_vectors_match_the_python_reference():
    want, got = committed(), build()
    hint = "regenerate with python scripts/gen_rover_golden.py"
    assert want["spec"] == got["spec"], hint
    assert want["controller"] == got["controller"], hint
    assert want["policy_int8"] == got["policy_int8"], hint
    flat = [value for point in want["path"] for value in point]
    assert flat == pytest.approx([value for point in got["path"] for value in point], abs=1e-12)
    assert len(want["rows"]) == len(got["rows"]), hint
    for row_want, row_got in zip(want["rows"], got["rows"]):
        assert row_want["case"] == row_got["case"]
        assert row_want["path_index_out"] == row_got["path_index_out"], row_want["case"]
        for key in NUMERIC:
            assert row_want[key] == pytest.approx(row_got[key], abs=1e-9), (row_want["case"], key)


def test_the_odometry_ticks_match_the_python_reference():
    want, got = committed()["odometry"], build()["odometry"]
    assert want["start"] == got["start"]
    assert len(want["ticks"]) == len(got["ticks"]) == 40
    for i, (tick_want, tick_got) in enumerate(zip(want["ticks"], got["ticks"])):
        for key in ("command", "body", "pose"):
            assert tick_want[key] == pytest.approx(tick_got[key], abs=1e-9), (i, key)
    headings = [tick["pose"][2] for tick in want["ticks"]]
    assert max(headings) > 3.0 and min(headings) < -3.0  # the ticks cross the ±π wrap
    assert min(tick["body"][0] for tick in want["ticks"]) >= 0.0  # reverse is clamped


def test_the_firmware_headers_are_what_the_generator_writes():
    # The firmware's golden vectors and its default spec cannot drift from the Python side.
    for path, text in ((GOLDEN_H, golden_header(committed())),
                       (SPEC_H, spec_header(load_spec("rover")))):
        with open(path, encoding="utf-8") as f:
            assert f.read() == text, f"{path} is stale: python scripts/gen_rover_golden.py"


def test_the_vectors_cover_turning_in_place_and_both_turn_directions():
    rows = {row["case"]: row for row in committed()["rows"]}
    backwards = rows["facing backwards: no forward speed, turns in place"]
    assert backwards["command"][0] == 0.0 and backwards["command"][1] != 0.0
    assert any(row["command"][1] > 0 for row in rows.values())
    assert any(row["command"][1] < 0 for row in rows.values())


def test_kinematics_round_trip():
    spec = load_spec("rover")
    assert limit_command(5.0, -9.0, spec) == (spec.v_max, -spec.w_max)
    assert limit_command(-1.0, 0.0, spec) == (0.0, 0.0)
    left, right = wheel_speeds(0.2, 1.0, spec)
    assert (left + right) / 2 == pytest.approx(0.2)
    assert (right - left) / spec.wheelbase_m == pytest.approx(1.0)
    rate, _ = wheel_rates(spec.wheel_radius_m, 0.0, spec)  # 1 m/s per metre of radius = 1 rad/s
    assert rate == pytest.approx(1.0)
    one_turn_per_second = 2 * 3.141592653589793
    expected = spec.counts_per_rev * spec.dt
    assert counts_per_tick(one_turn_per_second, spec) == pytest.approx(expected)
