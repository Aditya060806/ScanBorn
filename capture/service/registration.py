"""Whether a scan shares a physical frame with the rover, read from its meta.json.

A phone scan starts on the home marker and records `home_pose`: its map frame is the marker's,
so a rover placed on the marker starts where the twin says it does. An imported export, or a
scan without a home pose, has no such frame. It can be inspected, planned and simulated, but a
physical drive from it would put the rover somewhere the twin does not describe.
"""
import math

HOME_MARKER, UNKNOWN = "home_marker", "unknown"


def _home_pose(meta: dict):
    pose = meta.get("home_pose")
    if (isinstance(pose, list) and len(pose) == 3 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in pose)):
        return pose
    return None


def registration(meta: dict) -> dict:
    """{state, physically_registered, reason}; state is home_marker or unknown."""
    if meta.get("source") == "imported" or meta.get("registration") == UNKNOWN:
        return {"state": UNKNOWN, "physically_registered": False,
                "reason": "Imported export: it has no home-marker frame. Inspect and simulate "
                          "it; a physical rover needs a phone scan started on the marker."}
    if _home_pose(meta) is None:
        return {"state": UNKNOWN, "physically_registered": False,
                "reason": "No home pose was recorded with this scan, so its frame is not the "
                          "marker's. Inspect and simulate it; rescan on the marker to drive."}
    return {"state": HOME_MARKER, "physically_registered": True,
            "reason": "Started on the home marker: a rover placed on the marker shares this "
                      "scan's frame."}
