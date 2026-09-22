"""Semantic objects -> scene manifest (scene.json) + navmesh occupancy grid (navmesh.json).

Rule-based: label -> prefab name + primitive collider sized from the object's bounding box
(twin/rules/mapping.yaml). No per-vertex mesh colliders, no inferred lighting. The occupancy grid
itself is built in twin/navmesh.py.

Nothing in this repository imports scene.json: the planner, the simulator and the dashboard use
navmesh.json and the objects table. The manifest is kept as an export for a game engine. Its
prefab and collider names use Unity's vocabulary, and positions are converted once, here, from
the map frame (right-handed, Z up, metres) to the Y-up left-handed frame such an importer expects;
`unity_from_world` records that transform.
"""
import json
import os

import yaml

from twin.navmesh import (  # noqa: F401 - re-exported for callers that predate twin/navmesh.py
    FLOOR_CLEARANCE,
    ROBOT_HEIGHT,
    WALKABLE_LABELS,
    build_navmesh,
    choose_cell,
    is_blocked,
    nearest_free,
)
from twin.profile import profile_for

RULES_PATH = os.path.join(os.path.dirname(__file__), "rules", "mapping.yaml")
DEFAULT_RULE = {"prefab": "Props/Generic", "collider": "BoxCollider"}

# (x, y, z) Z-up right-handed -> (x, z, y) Y-up left-handed. Swapping two axes is what
# flips the handedness.
UNITY_FROM_WORLD = [
    [1, 0, 0, 0],
    [0, 0, 1, 0],
    [0, 1, 0, 0],
    [0, 0, 0, 1],
]


def load_rules(path: str = RULES_PATH) -> dict:
    """mapping.yaml -> {label: {'prefab', 'collider'}}."""
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    return {m["label"]: {"prefab": m["prefab"], "collider": m["collider"]}
            for m in doc.get("mappings", [])}


def _to_unity(x, y, z) -> list:
    return [x, z, y]


def _scene_object(obj: dict, rules: dict) -> dict:
    xmin, ymin, zmin, xmax, ymax, zmax = obj["bbox3d"]
    rule = rules.get(obj["label"], DEFAULT_RULE)
    centre = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
    return {
        "id": obj["id"],
        "label": obj["label"],
        "prefab": rule["prefab"],
        "position": _to_unity(*centre),
        "collider": {
            "type": rule["collider"],
            "size": _to_unity(xmax - xmin, ymax - ymin, zmax - zmin),
        },
        "confidence": obj.get("confidence"),
    }


def generate_twin(objects: list, out_dir: str, cell: float | None = None, points=None,
                  profile=None) -> dict:
    """Write scene.json (the manifest above) and navmesh.json.

    `points` is the reconstructed cloud; pass it whenever it is on hand, so the navmesh
    is built from measured geometry rather than from bounding boxes.

    `cell` is the grid size in metres. Omit it and the scan's own density picks 5 or 10 cm
    (twin/navmesh.py:choose_cell); the choice and its evidence are recorded as `resolution`.

    `profile` is an EnvironmentProfile (twin/profile.py). Omit it and the twin is built for
    the generic robot, which is exactly the behaviour that existed before profiles — so the
    same scan can be re-generated for a different robot without re-scanning the room.
    """
    os.makedirs(out_dir, exist_ok=True)
    rules = load_rules()
    resolved = profile if profile is not None else profile_for(None)

    scene = {
        "unity_from_world": UNITY_FROM_WORLD,
        "profile": resolved.as_dict(),
        "objects": [_scene_object(o, rules) for o in objects],
    }
    scene_path = os.path.join(out_dir, "scene.json")
    with open(scene_path, "w") as f:
        json.dump(scene, f, indent=2)

    if cell is None:
        cell, evidence = choose_cell(objects, points)
        resolution = {"cell_m": cell, "chosen": "auto", **evidence}
    else:
        resolution = {"cell_m": cell, "chosen": "requested"}
    navmesh = build_navmesh(objects, cell, points, resolved)
    navmesh["resolution"] = resolution
    navmesh_path = os.path.join(out_dir, "navmesh.json")
    with open(navmesh_path, "w") as f:
        json.dump(navmesh, f)

    return {"scene_path": scene_path, "navmesh_path": navmesh_path,
            "object_count": len(objects), "profile": resolved.as_dict(),
            "floor_z": navmesh["floor_z"], "counts": navmesh["counts"],
            "resolution": resolution}
