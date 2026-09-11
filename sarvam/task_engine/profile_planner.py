"""Infer which environment profile fits a scanned room.

Two backends, same contract. `HeuristicProfiler` reads the scene's geometry and needs no
model at all, so a twin can always be built. `ModelProfiler` asks a language model, which
is the first job in this pipeline a small model does better than a rule — reasoning over a
scene summary to pick a configuration is not something a regex can do, whereas turning
"take the box to the table" into a task graph already is.

The model only ever chooses the *kind* and the three scalars. Extra labels and keep-out
distances come from the table in twin/profile.py, so a model can tune how much clearance a
robot gets but cannot invent or remove a safety rule.
"""
import json
import os
from abc import ABC, abstractmethod

from twin.profile import DEFAULT_KIND, ENVIRONMENTS, EnvironmentProfile, from_dict, profile_for

from .schemas import ENVIRONMENT_KINDS, validate_environment_profile

PROMPT = (
    "You classify indoor spaces for a mobile robot from a geometric summary.\n"
    "Pick exactly one environment from: {kinds}.\n"
    "Then choose the robot's footprint radius (m), sweep height (m) and top speed (m/s) "
    "appropriate for that space. Larger spaces allow bigger, faster robots; clinical and "
    "exhibition spaces need slower, more careful ones.\n"
    'Reply with JSON only: {{"environment": "...", "robot_radius": 0.0, '
    '"robot_height": 0.0, "max_speed": 0.0}}'
)


def scene_stats(objects) -> dict:
    """Compact geometric facts about a segmented scene.

    Everything here is derived from bounding boxes the segmenter already produced, so this
    costs nothing and needs no access to the point cloud.
    """
    labels: dict = {}
    for obj in objects or []:
        label = str(obj.get("label", "")).lower()
        labels[label] = labels.get(label, 0) + 1

    boxes = [o["bbox3d"] for o in (objects or []) if o.get("bbox3d")]
    if not boxes:
        return {"labels": labels, "area_m2": 0.0, "ceiling_m": 0.0, "objects": 0}
    # ponytail: assumes 6-element boxes, which is what _bbox() emits. A short box would
    # IndexError here rather than being skipped.

    floors = [o["bbox3d"] for o in objects
              if str(o.get("label", "")).lower() == "floor" and o.get("bbox3d")]
    extent = floors or boxes
    width = max(b[3] for b in extent) - min(b[0] for b in extent)
    depth = max(b[4] for b in extent) - min(b[1] for b in extent)

    # float() before round() on purpose. round() preserves its argument's type, so an int
    # bbox — which is exactly what a JSON round-trip produces from "0" — yields int 50 and
    # the summary reads "50 m2", while a float bbox from the segmenter yields 50.0 and reads
    # "50.0 m2". The same room must summarise identically either way, because this text is
    # the model's whole input and the distillation examples are matched against it.
    return {
        "labels": labels,
        "area_m2": round(float(max(0.0, width) * max(0.0, depth)), 2),
        "ceiling_m": round(
            float(max(b[5] for b in boxes) - min(b[2] for b in boxes)), 2),
        # Structure is not furniture; counting it would call every room crowded.
        "objects": sum(n for label, n in labels.items()
                       if label not in ("floor", "ceiling", "wall")),
    }


def summarize(objects) -> str:
    """The scene as one short line of text — the model's entire input.

    Deliberately tiny. The on-device model has a small context and the phone pays for
    every prefill token, and a histogram plus three numbers is genuinely all the signal
    there is in a bbox-level scene description.
    """
    stats = scene_stats(objects)
    counts = ", ".join(f"{n} {label}" for label, n in sorted(stats["labels"].items())) \
        or "no labelled objects"
    return (f"Floor area {stats['area_m2']} m2, ceiling height {stats['ceiling_m']} m, "
            f"{stats['objects']} objects. Detected: {counts}.")


class ProfilePlanner(ABC):
    """Segmented scene -> EnvironmentProfile."""

    @abstractmethod
    def profile(self, objects) -> EnvironmentProfile:
        ...


class HeuristicProfiler(ProfilePlanner):
    """Rule-based classification from geometry. No model, never fails.

    The rules lean on floor area and ceiling height rather than labels, because the
    geometric segmenter only ever emits floor/wall/chair/table/shelf — it has no way to
    report a pallet or a hospital bed. Once open-vocabulary labels are running (the
    `labels()` list a profile provides is exactly what YOLO-World would be given), the
    label signal becomes the stronger one and these thresholds should be revisited.

    ponytail: thresholds are hand-set from the four example scenarios, not fitted to data.
    """

    # (kind, min_area_m2, min_ceiling_m) — first match wins, so order is the priority.
    RULES = (
        ("warehouse", 60.0, 3.5),
        ("factory", 40.0, 3.0),
        ("museum", 80.0, 0.0),
        ("office", 25.0, 0.0),
        ("home", 0.0, 0.0),
    )

    def profile(self, objects) -> EnvironmentProfile:
        stats = scene_stats(objects)
        if not stats["objects"] and not stats["area_m2"]:
            return profile_for(DEFAULT_KIND)

        area, ceiling = stats["area_m2"], stats["ceiling_m"]
        shelves = stats["labels"].get("shelf", 0)

        # A tall, big room full of shelving is a warehouse; the same room without shelving
        # is a factory floor. Shelf count is the one label signal worth trusting here.
        for kind, min_area, min_ceiling in self.RULES:
            if area >= min_area and ceiling >= min_ceiling:
                if kind == "warehouse" and shelves < 2:
                    continue
                return profile_for(kind)
        return profile_for(DEFAULT_KIND)


class ModelProfiler(ProfilePlanner):
    """Asks a language model, and falls back to the heuristic on any bad answer.

    `chat` is injected rather than imported so the same class serves the Groq path and the
    on-device llama.cpp path — the phone will pass a callable that runs the local model
    under the grammar from schemas.environment_profile_grammar().
    """

    def __init__(self, chat, fallback: ProfilePlanner | None = None):
        self.chat = chat
        self.fallback = fallback or HeuristicProfiler()

    def profile(self, objects) -> EnvironmentProfile:
        system = PROMPT.format(kinds=", ".join(ENVIRONMENT_KINDS))
        try:
            raw = self.chat(system, summarize(objects))
            doc = json.loads(raw)
        except (RuntimeError, ValueError, KeyError, TypeError):
            # No key, unreachable host, or unparseable reply — the twin still gets built.
            return self.fallback.profile(objects)

        if validate_environment_profile(doc):
            return self.fallback.profile(objects)
        return from_dict(doc)


def get_profiler(provider=None) -> ProfilePlanner:
    """Local-first: the heuristic unless a remote provider is named explicitly.

    Mirrors get_planner. Naming a provider is the only way to reach the network — the
    presence of an API key is not enough, on purpose.
    """
    choice = (provider or os.environ.get("SCANBORN_PROFILER", "local")).lower()
    if choice == "groq":
        from .groq_provider import groq_chat
        return ModelProfiler(lambda s, u: groq_chat(s, u))
    return HeuristicProfiler()


# Re-exported so callers can pick a profile by hand without importing twin/ directly.
KNOWN_KINDS = tuple(ENVIRONMENTS)
