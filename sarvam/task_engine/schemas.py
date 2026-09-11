"""The output contracts every planner backend must satisfy, and the grammars that force them.

There are three ways a task graph can reach the pipeline — a remote model (Groq, Sarvam),
an on-device model (llama.cpp), or the offline keyword grammar — and all three have to
produce the same shape. Keeping the shape in one place is what stops them drifting.

The GBNF grammars are *generated* from the same constants the validators check, rather
than written out by hand in a .gbnf file. A hand-written grammar is a second source of
truth that silently rots the first time someone adds an action to VOCABULARY; and the
target list cannot be static anyway, because the legal targets are whatever the scene
actually contains.

Grammar-constrained decoding is what makes a 1.5B model viable here. The tasks are
classification and extraction over short fixed lists, not open generation, so the model
does not need to be big — it needs to be unable to emit anything invalid.
"""
from semantic.service.inference import LABEL_ONTOLOGY

from .graph import VOCABULARY

# Environments the profile planner is allowed to name. Kept here rather than in
# twin/profile.py so the grammar and the validator read from one list.
ENVIRONMENT_KINDS = (
    "warehouse", "hospital", "factory", "museum", "home", "office", "generic",
)

# Physical bounds on a profile. A model that hallucinates a 4-metre robot radius would
# blank the navmesh and the failure would surface three stages later as "no traversable
# cell", so the range check happens here where the cause is still obvious.
PROFILE_BOUNDS = {
    "robot_radius": (0.05, 1.50),   # m, footprint used for costmap inflation
    "robot_height": (0.20, 2.50),   # m, top of the band a ground robot sweeps
    "max_speed": (0.05, 2.00),      # m/s, per-tick command ceiling
}


# --------------------------------------------------------------------------- task graph

def task_graph_targets(objects=None) -> list:
    """Legal `target` values: the scene's own labels first, then the ontology.

    Scene labels come first for the same reason FunctionGemmaPlanner prefers them — a
    label this room actually contains is a better guess than a generic ontology entry.
    The empty string is legal because `wait` and `speak` have no target.
    """
    seen, targets = set(), []
    for label in [str(o).lower() for o in (objects or [])] + list(LABEL_ONTOLOGY):
        if label and label not in seen:
            seen.add(label)
            targets.append(label)
    return targets


def _alternates(values) -> str:
    """GBNF alternation over JSON string literals: "a" | "b" | ..."""
    return " | ".join('"\\"%s\\""' % v for v in values)


def task_graph_grammar(objects=None) -> str:
    """GBNF that can only produce a valid task graph for this scene.

    The model cannot emit an unknown action or an off-scene target, so the
    `if n["action"] in VOCABULARY` filter the remote planners need becomes redundant on
    the on-device path — the vocabulary is enforced at sample time instead of cleaned up
    afterwards.
    """
    targets = task_graph_targets(objects) + [""]
    return "\n".join([
        'root   ::= "{" ws "\\"nodes\\"" ws ":" ws "[" ws (node (ws "," ws node)*)? ws "]" ws "}"',
        'node   ::= "{" ws "\\"action\\"" ws ":" ws action ws "," ws '
        '"\\"target\\"" ws ":" ws target ws "}"',
        "action ::= " + _alternates(VOCABULARY),
        "target ::= " + _alternates(targets),
        "ws     ::= [ \\t\\n]*",
        "",
    ])


def validate_task_graph(doc, objects=None) -> list:
    """Return the problems with a decoded task graph; empty list means it is usable.

    Returns problems rather than raising so a caller can decide whether one bad node is
    worth discarding the whole plan. The remote planners drop offending nodes; a strict
    caller can treat any problem as fatal.
    """
    problems = []
    if not isinstance(doc, dict):
        return [f"expected a JSON object, got {type(doc).__name__}"]

    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        return ["'nodes' must be a list"]

    legal = set(task_graph_targets(objects)) | {""}
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.append(f"node {i}: expected an object")
            continue
        action = node.get("action")
        if action not in VOCABULARY:
            problems.append(f"node {i}: action {action!r} is not in the vocabulary")
        target = node.get("target", "")
        if not isinstance(target, str):
            problems.append(f"node {i}: target must be a string")
        elif target.lower() not in legal:
            problems.append(f"node {i}: target {target!r} is not in the scene")
    return problems


# -------------------------------------------------------------------- environment profile

def environment_profile_grammar() -> str:
    """GBNF for the profile object: one environment kind plus three bounded numbers.

    Key order is fixed by the grammar. That is deliberate — a constrained decoder emits
    tokens in grammar order, so pinning the order removes a degree of freedom the model
    would otherwise spend, and makes the output byte-comparable across runs.
    """
    return "\n".join([
        'root ::= "{" ws '
        '"\\"environment\\"" ws ":" ws env ws "," ws '
        '"\\"robot_radius\\"" ws ":" ws number ws "," ws '
        '"\\"robot_height\\"" ws ":" ws number ws "," ws '
        '"\\"max_speed\\"" ws ":" ws number ws "}"',
        "env    ::= " + _alternates(ENVIRONMENT_KINDS),
        'number ::= [0-9]+ ("." [0-9]+)?',
        "ws     ::= [ \\t\\n]*",
        "",
    ])


def validate_environment_profile(doc) -> list:
    """Return the problems with a decoded profile; empty list means it is usable."""
    problems = []
    if not isinstance(doc, dict):
        return [f"expected a JSON object, got {type(doc).__name__}"]

    kind = doc.get("environment")
    if kind not in ENVIRONMENT_KINDS:
        problems.append(f"environment {kind!r} is not a known kind")

    for field, (low, high) in PROFILE_BOUNDS.items():
        if field not in doc:
            problems.append(f"{field} is missing")
            continue
        value = doc[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{field} must be a number, got {type(value).__name__}")
        elif not low <= float(value) <= high:
            problems.append(f"{field} {value} is outside {low}..{high}")
    return problems
