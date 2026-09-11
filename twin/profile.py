"""What kind of space this is, and what that implies for the robot in it.

The pipeline used to bake one robot into three module-level constants: ROBOT_RADIUS in
policy/evaluate.py, ROBOT_HEIGHT here in twin/, MAX_LINEAR_SPEED in robot/adapters/. That
is fine for a demo with one buggy and wrong for everything else — a warehouse forklift
needs three times the clearance of a home vacuum robot, and a hospital corridor wants a
third of the speed of a warehouse aisle.

A profile is the set of knobs a scan can legitimately choose for you. `generic` reproduces
the old hardcoded constants exactly, so nothing changes until a profile is asked for.

This module holds only data and pure functions — no model, no network. The planner that
*infers* which profile fits a scanned room lives in sarvam/task_engine/profile_planner.py,
so the twin can be built with a profile chosen by hand and never touch a model at all.
"""
from dataclasses import dataclass, field

from semantic.service.inference import LABEL_ONTOLOGY


@dataclass(frozen=True)
class EnvironmentProfile:
    """Everything the space decides about the robot that drives in it."""

    kind: str
    robot_radius: float          # m, obstacles inflate by this before planning
    robot_height: float          # m, top of the band the robot sweeps
    max_speed: float             # m/s, per-tick command ceiling
    extra_labels: tuple = ()     # segmentation classes this space adds
    keep_out: dict = field(default_factory=dict)  # label -> extra clearance, metres

    def labels(self) -> list:
        """The full segmentation vocabulary for this space, ontology first."""
        return list(LABEL_ONTOLOGY) + [c for c in self.extra_labels
                                       if c not in LABEL_ONTOLOGY]

    def clearance_for(self, label: str) -> float:
        """Inflation radius for one label — the robot's own footprint plus any keep-out.

        Additive rather than a max: a museum's one-metre rule around a display case is a
        rule about the case, and the robot still occupies its own width on top of it.
        """
        return self.robot_radius + float(self.keep_out.get(label, 0.0))

    def as_dict(self) -> dict:
        """The wire shape, matching schemas.environment_profile_grammar()."""
        return {
            "environment": self.kind,
            "robot_radius": self.robot_radius,
            "robot_height": self.robot_height,
            "max_speed": self.max_speed,
        }


# Numbers are deliberately conservative: every one of them widens an obstacle or slows a
# robot down relative to `generic`, except the warehouse speed, which is the one place a
# large clear aisle genuinely earns it.
ENVIRONMENTS = {
    # The old constants, unchanged: ROBOT_RADIUS 0.25, ROBOT_HEIGHT 1.0, MAX_SPEED 0.5.
    "generic": EnvironmentProfile("generic", 0.25, 1.00, 0.50),

    "warehouse": EnvironmentProfile(
        "warehouse", 0.45, 1.80, 1.20,
        extra_labels=("pallet", "rack", "forklift", "conveyor", "crate"),
        keep_out={"forklift": 0.60, "conveyor": 0.30},
    ),
    "hospital": EnvironmentProfile(
        "hospital", 0.30, 1.20, 0.40,
        extra_labels=("bed", "wheelchair", "iv_stand", "gurney", "trolley"),
        keep_out={"bed": 0.40, "wheelchair": 0.40, "iv_stand": 0.30, "person": 0.60},
    ),
    "factory": EnvironmentProfile(
        "factory", 0.40, 1.60, 0.80,
        extra_labels=("machine", "conveyor", "toolbox", "pallet", "barrier"),
        keep_out={"machine": 0.80, "conveyor": 0.40},
    ),
    "museum": EnvironmentProfile(
        "museum", 0.25, 1.20, 0.30,
        extra_labels=("pedestal", "display_case", "rope_barrier", "sculpture"),
        keep_out={"display_case": 1.00, "pedestal": 0.80, "sculpture": 0.80},
    ),
    "home": EnvironmentProfile(
        "home", 0.20, 0.80, 0.40,
        extra_labels=("sofa", "bed", "tv", "rug", "counter"),
        keep_out={"person": 0.40},
    ),
    "office": EnvironmentProfile(
        "office", 0.25, 1.10, 0.50,
        extra_labels=("desk", "monitor", "printer", "whiteboard", "partition"),
        keep_out={"person": 0.40},
    ),
}

DEFAULT_KIND = "generic"


def profile_for(kind=None) -> EnvironmentProfile:
    """Look up a profile by name, falling back to `generic` for anything unknown.

    Unknown names fall back rather than raise: the name can come out of a language model,
    and a room that segments fine should still produce a twin when the model invents a
    word. The grammar in schemas.py is what prevents that on the on-device path.
    """
    return ENVIRONMENTS.get(str(kind or DEFAULT_KIND).lower(),
                            ENVIRONMENTS[DEFAULT_KIND])


def from_dict(doc: dict) -> EnvironmentProfile:
    """Rebuild a profile from the wire shape, keeping the table's labels and keep-outs.

    A model returns the four scalar fields; extra_labels and keep_out are ours, not the
    model's, so they are taken from the table for whichever kind it named. That means a
    model can tune the numbers but cannot invent a safety rule.
    """
    base = profile_for(doc.get("environment"))
    return EnvironmentProfile(
        kind=base.kind,
        robot_radius=float(doc.get("robot_radius", base.robot_radius)),
        robot_height=float(doc.get("robot_height", base.robot_height)),
        max_speed=float(doc.get("max_speed", base.max_speed)),
        extra_labels=base.extra_labels,
        keep_out=base.keep_out,
    )
