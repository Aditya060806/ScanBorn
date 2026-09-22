"""The strict output contract every planner and profiler result must meet before it is used.

One contract, whichever provider produced the result: the local keyword planner, Groq or Sarvam
for task graphs; the heuristic or Groq for environment profiles. A result is judged whole. Nothing
is silently dropped or repaired: an invalid step invalidates the reply, and a request the scene
cannot satisfy asks for clarification instead of guessing.

Graph assessment statuses:

- `planned`               every step is valid and every target names exactly one scene object
- `clarification_needed`  a target is missing, not in this scene, or shared by several objects
- `unsupported`           no supported action was recognised
- `invalid`               the reply breaks the schema (unknown action, wrong types, bad params)
"""
import json
import math
from dataclasses import dataclass, field

from semantic.service.inference import LABEL_ONTOLOGY

from .graph import VOCABULARY
from .schemas import validate_environment_profile

TASK_GRAPH_SCHEMA = "task-graph-v1"
PROFILE_SCHEMA = "environment-profile-v1"
MAX_NODES = 16
MAX_TEXT = 120
MAX_WAIT_S = 600.0
# Actions that act on an object, so they need a target that names one.
TARGETED = ("navigate_to", "pickup", "place", "inspect")
# What each action may carry in `params`, and each value's check.
PARAMS = {
    "wait": {"seconds": lambda v: _number(v) and 0 < v <= MAX_WAIT_S},
    "speak": {"text": lambda v: isinstance(v, str) and 0 < len(v.strip()) <= MAX_TEXT},
}


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def decode(content, max_bytes: int):
    """A reply's text as a JSON object: (doc, None), or (None, why it cannot be used)."""
    if not isinstance(content, str):
        return None, f"reply content is {type(content).__name__}, not text"
    if len(content.encode("utf-8")) > max_bytes:
        return None, f"reply exceeds {max_bytes} bytes"
    try:
        doc = json.loads(content)
    except ValueError:
        return None, "reply is not JSON"
    if not isinstance(doc, dict):
        return None, f"reply is a JSON {type(doc).__name__}, not an object"
    return doc, None


@dataclass
class Assessment:
    status: str
    problems: list = field(default_factory=list)
    # For clarification: what could not be resolved, and what the scene offers instead.
    unresolved: list = field(default_factory=list)
    choices: list = field(default_factory=list)


def _schema_problems(doc) -> list:
    if not isinstance(doc, dict):
        return [f"expected a JSON object, got {type(doc).__name__}"]
    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        return ["'nodes' must be a list"]
    if len(nodes) > MAX_NODES:
        return [f"{len(nodes)} steps; at most {MAX_NODES} are accepted"]
    problems = []
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.append(f"step {i}: expected an object")
            continue
        extra = set(node) - {"id", "action", "target", "params"}
        if extra:
            problems.append(f"step {i}: unexpected fields {sorted(extra)}")
        action = node.get("action")
        if action not in VOCABULARY:
            problems.append(f"step {i}: action {action!r} is not supported")
            continue
        target = node.get("target", "")
        if not isinstance(target, str):
            problems.append(f"step {i}: target must be text")
        elif action not in TARGETED and target.strip():
            problems.append(f"step {i}: {action} takes no target")
        params = node.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            problems.append(f"step {i}: params must be an object")
            continue
        allowed = PARAMS.get(action, {})
        for key, value in params.items():
            check = allowed.get(key)
            if check is None:
                problems.append(f"step {i}: {action} does not take {key!r}")
            elif not check(value):
                problems.append(f"step {i}: {action} {key} {value!r} is out of range")
    return problems


def assess_graph(doc, scene_labels=None) -> Assessment:
    """Judge a decoded task graph against this scene's object labels.

    `scene_labels` lists one label per scene object (repeats mean several objects share a
    label). Without a scene (the landing-page preview) targets are checked against the ontology
    and cannot be ambiguous.
    """
    problems = _schema_problems(doc)
    if problems:
        return Assessment("invalid", problems)
    nodes = doc["nodes"]
    if not nodes:
        return Assessment("unsupported", ["no supported action was recognised"])

    labels = [str(label).lower() for label in (scene_labels or [])]
    known = sorted(set(labels)) if scene_labels is not None else sorted(set(LABEL_ONTOLOGY))
    unresolved, problems = [], []
    for i, node in enumerate(nodes):
        if node["action"] not in TARGETED:
            continue
        target = node.get("target", "").strip().lower()
        if not target:
            problems.append(f"step {i}: {node['action']} needs an object; which one?")
            unresolved.append({"step": i, "target": "", "reason": "missing"})
        elif target not in known:
            problems.append(f"step {i}: there is no {target!r} in this scene")
            unresolved.append({"step": i, "target": target, "reason": "not_in_scene"})
        elif scene_labels is not None and labels.count(target) > 1:
            problems.append(f"step {i}: this scene has {labels.count(target)} {target}s; "
                            "choose one on the map")
            unresolved.append({"step": i, "target": target, "reason": "ambiguous"})
    if unresolved:
        return Assessment("clarification_needed", problems, unresolved, known)
    return Assessment("planned")


def normalise_graph(doc) -> dict:
    """A planned graph in canonical form: sequential ids, lower-case targets, linear edges."""
    nodes = [{"id": f"n{i}", "action": node["action"],
              "target": node.get("target", "").strip().lower(),
              "params": dict(node.get("params") or {})}
             for i, node in enumerate(doc["nodes"])]
    edges = [[a["id"], b["id"]] for a, b in zip(nodes, nodes[1:])]
    return {"nodes": nodes, "edges": edges}


def profile_problems(doc) -> list:
    """Problems with a decoded environment profile; empty means it is usable.

    The range checks in schemas.validate_environment_profile also reject NaN and infinity,
    because neither compares as inside a bound.
    """
    problems = validate_environment_profile(doc)
    if isinstance(doc, dict):
        extra = set(doc) - {"environment", "robot_radius", "robot_height", "max_speed"}
        if extra:
            problems.append(f"unexpected fields {sorted(extra)}")
    return problems
