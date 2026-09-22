import copy
import os

import pytest
import yaml

from robot.adapters.base import MAX_LINEAR_SPEED
from robot.spec import SAFETY_MAX_SPEED, SPEC_DIR, load_spec, spec_from_dict


def shipped() -> dict:
    with open(os.path.join(SPEC_DIR, "rover.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_the_shipped_rover_spec_loads_and_says_it_is_not_measured():
    spec = load_spec("rover")
    assert spec.source == "placeholder" and spec.noise.source == "assumed"
    assert spec.dt == pytest.approx(0.05)
    assert isinstance(spec.counts_per_rev, int) and isinstance(spec.noise.latency_ticks, int)
    # The footprint has to cover the 20 x 20 cm chassis it describes.
    assert spec.footprint_radius_m >= (0.2 ** 2 + 0.2 ** 2) ** 0.5 / 2
    assert spec.as_dict()["noise"]["wheel_slip"] == spec.noise.wheel_slip


def test_the_spec_ceiling_matches_the_adapters_ceiling():
    assert SAFETY_MAX_SPEED == MAX_LINEAR_SPEED


@pytest.mark.parametrize("field, value", [
    ("wheelbase_m", 0), ("v_max", -0.1), ("a_max", float("nan")), ("control_hz", "fast"),
    ("w_max", True), ("counts_per_rev", 660.5), ("step_height_m", -0.01),
])
def test_a_bad_value_is_refused_by_name(field, value):
    doc = shipped()
    doc[field] = value
    with pytest.raises(ValueError, match=field):
        spec_from_dict(doc)


@pytest.mark.parametrize("field, value", [
    ("wheel_slip", -0.1), ("latency_ticks", 0.5), ("gyro_bias_rad_s", float("inf")),
])
def test_noise_values_are_checked_too(field, value):
    doc = copy.deepcopy(shipped())
    doc["noise"][field] = value
    with pytest.raises(ValueError, match=field):
        spec_from_dict(doc)


def test_a_missing_field_or_noise_block_is_refused():
    doc = shipped()
    del doc["wheel_radius_m"]
    with pytest.raises(ValueError, match="wheel_radius_m"):
        spec_from_dict(doc)
    doc = shipped()
    del doc["noise"]
    with pytest.raises(ValueError, match="noise"):
        spec_from_dict(doc)


def test_a_speed_above_the_safety_ceiling_is_refused():
    doc = shipped()
    doc["v_max"] = SAFETY_MAX_SPEED + 0.1
    with pytest.raises(ValueError, match="safety ceiling"):
        spec_from_dict(doc)


def test_spec_names_cannot_leave_the_spec_folder():
    with pytest.raises(ValueError):
        load_spec("../secrets")
    with pytest.raises(FileNotFoundError):
        load_spec("no-such-robot")
