"""Calibration arithmetic from externally measured travel; never infer noise calibration."""
import math


def measurement(result: dict, measured, spec) -> dict:
    if isinstance(measured, bool) or not isinstance(measured, (int, float)):
        raise ValueError("measurement must be a finite positive number")
    if not math.isfinite(measured) or measured <= 0:
        raise ValueError("measurement must be a finite positive number")
    values = result.get("encoder_counts")
    if not isinstance(values, list) or len(values) != 2 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("two finite encoder counts are required")
    left, right = values
    if result.get("status") != "done":
        raise ValueError("only completed experiments may produce calibration settings")
    if result.get("mode") == "straight":
        if left <= 0 or right <= 0:
            raise ValueError("both encoders must increase during forward travel; verify signs")
        per_metre = (left + right) / (2 * measured)
        patch = {"wheel_radius_m": spec.counts_per_rev / (2 * math.pi * per_metre)}
        derived = {"counts_per_metre": per_metre,
                   "left_counts_per_metre": left / measured,
                   "right_counts_per_metre": right / measured}
        unit = "metres"
    elif result.get("mode") == "spin":
        if left >= 0 or right <= 0:
            raise ValueError("positive rotation requires negative left and positive right counts")
        metres_per_count = 2 * math.pi * spec.wheel_radius_m / spec.counts_per_rev
        patch = {"wheelbase_m": (right - left) * metres_per_count / measured}
        derived = {"wheel_travel_m": [left * metres_per_count, right * metres_per_count]}
        unit = "radians"
    else:
        raise ValueError("only straight and spin experiments update geometry")
    for value in patch.values():
        if not 0.005 <= value <= 2:
            raise ValueError("measurement implies implausible geometry; check units and signs")
    return {"measurement": measured, "unit": unit, "settings": patch, "derived": derived,
            "before": {key: getattr(spec, key) for key in patch},
            "provenance": result.get("source", "unknown"), "noise_unchanged": True}
