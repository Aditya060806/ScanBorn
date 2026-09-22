import math

import pytest

from robot.calibration import measurement
from robot.spec import load_spec


def test_tape_distance_and_angle_determine_geometry_without_changing_noise():
    spec = load_spec("rover")
    straight = measurement({"mode": "straight", "status": "done", "encoder_counts": [3000, 3000],
                            "source": "simulated"}, 1.0, spec)
    assert straight["derived"]["counts_per_metre"] == 3000
    assert straight["settings"]["wheel_radius_m"] == pytest.approx(
        spec.counts_per_rev / (6000 * math.pi))
    assert straight["noise_unchanged"] and straight["provenance"] == "simulated"
    spin = measurement({"mode": "spin", "status": "done", "encoder_counts": [-1000, 1000]},
                       2 * math.pi, spec)
    assert spin["settings"]["wheelbase_m"] == pytest.approx(
        2000 * spec.wheel_radius_m / spec.counts_per_rev)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_measurements_are_refused(value):
    with pytest.raises(ValueError):
        measurement({"mode": "straight", "status": "done", "encoder_counts": [3000, 3000]},
                    value, load_spec("rover"))
