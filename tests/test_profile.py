import pytest

from policy.evaluate import ROBOT_RADIUS
from robot.adapters.base import MAX_LINEAR_SPEED
from sarvam.task_engine.profile_planner import (
    HeuristicProfiler,
    ModelProfiler,
    get_profiler,
    scene_stats,
    summarize,
)
from semantic.service.inference import LABEL_ONTOLOGY
from twin.generator import ROBOT_HEIGHT
from twin.profile import ENVIRONMENTS, from_dict, profile_for


def box(label, x0, y0, z0, x1, y1, z1):
    return {"id": label, "label": label, "bbox3d": [x0, y0, z0, x1, y1, z1],
            "confidence": 0.9}


def room(width, depth, height, extras=()):
    """A floor, one wall tall enough to set the ceiling, plus whatever furniture."""
    objects = [box("floor", 0, 0, 0, width, depth, 0.1),
               box("wall", 0, 0, 0, 0.2, depth, height)]
    objects.extend(extras)
    return objects


# ------------------------------------------------------------------ the default is unchanged

def test_generic_profile_reproduces_the_old_hardcoded_constants():
    """The refactor must not move the default robot. These three constants were the
    pipeline's only notion of a robot before profiles existed."""
    generic = profile_for("generic")
    assert generic.robot_radius == ROBOT_RADIUS
    assert generic.robot_height == ROBOT_HEIGHT
    assert generic.max_speed == MAX_LINEAR_SPEED


def test_unknown_kind_falls_back_to_generic():
    assert profile_for("spaceship").kind == "generic"
    assert profile_for(None).kind == "generic"


def test_every_table_entry_names_itself_consistently():
    for key, profile in ENVIRONMENTS.items():
        assert profile.kind == key


# ---------------------------------------------------------------------------- profile shape

def test_labels_extend_the_ontology_without_duplicating_it():
    labels = profile_for("hospital").labels()
    assert labels[:len(LABEL_ONTOLOGY)] == list(LABEL_ONTOLOGY)
    assert "wheelchair" in labels
    assert len(labels) == len(set(labels))


def test_clearance_adds_keep_out_to_the_robot_footprint():
    museum = profile_for("museum")
    assert museum.clearance_for("display_case") == pytest.approx(0.25 + 1.00)
    # A label with no rule is just the robot's own width.
    assert museum.clearance_for("floor") == pytest.approx(museum.robot_radius)


def test_as_dict_matches_the_wire_shape():
    assert set(profile_for("warehouse").as_dict()) == {
        "environment", "robot_radius", "robot_height", "max_speed"}


def test_from_dict_keeps_our_safety_rules_not_the_models():
    """A model may tune the numbers; it must not be able to invent or delete a keep-out."""
    restored = from_dict({"environment": "museum", "robot_radius": 0.4,
                          "robot_height": 1.1, "max_speed": 0.2})
    assert restored.robot_radius == 0.4          # the model's number was taken
    assert restored.keep_out == profile_for("museum").keep_out   # ours was not replaced
    assert restored.extra_labels == profile_for("museum").extra_labels


def test_from_dict_falls_back_per_field():
    restored = from_dict({"environment": "home"})
    assert restored.robot_radius == profile_for("home").robot_radius


# ------------------------------------------------------------------------------ scene stats

def test_scene_stats_measures_the_floor_not_the_furniture():
    stats = scene_stats(room(10, 5, 2.5, [box("chair", 1, 1, 0, 1.5, 1.5, 0.9)]))
    assert stats["area_m2"] == pytest.approx(50.0)
    assert stats["ceiling_m"] == pytest.approx(2.5)


def test_structure_does_not_count_as_objects():
    """Counting walls and floors would make every empty room look crowded."""
    assert scene_stats(room(10, 5, 2.5))["objects"] == 0
    assert scene_stats(room(10, 5, 2.5, [box("table", 1, 1, 0, 2, 2, 0.8)]))["objects"] == 1


def test_scene_stats_on_an_empty_scene_is_all_zeros():
    stats = scene_stats([])
    assert stats == {"labels": {}, "area_m2": 0.0, "ceiling_m": 0.0, "objects": 0}


def test_summary_is_one_short_line_naming_the_labels():
    text = summarize(room(10, 5, 2.5, [box("shelf", 1, 1, 0, 2, 2, 1.9)]))
    assert "\n" not in text
    assert "shelf" in text and "50" in text


# ------------------------------------------------------------------------------- heuristics

@pytest.mark.parametrize("width,depth,height,extras,expected", [
    # Tall, big, and full of shelving.
    (10, 10, 4.0, [box("shelf", 1, 1, 0, 2, 3, 1.9)] * 3, "warehouse"),
    # Same shell, no shelving — a factory floor, not a warehouse.
    (10, 10, 4.0, [box("table", 1, 1, 0, 2, 3, 0.8)] * 2, "factory"),
    # Big but low: an exhibition hall.
    (10, 10, 2.8, [box("table", 1, 1, 0, 2, 3, 0.8)], "museum"),
    (10, 5, 2.5, [box("chair", 1, 1, 0, 1.5, 1.5, 0.9)] * 4, "office"),
    (5, 4, 2.4, [box("chair", 1, 1, 0, 1.5, 1.5, 0.9)], "home"),
])
def test_heuristic_classifies_rooms_from_geometry(width, depth, height, extras, expected):
    assert HeuristicProfiler().profile(room(width, depth, height, extras)).kind == expected


def test_warehouse_requires_shelving_not_just_volume():
    tall_empty = room(10, 10, 4.0)
    assert HeuristicProfiler().profile(tall_empty).kind != "warehouse"


def test_heuristic_on_an_empty_scene_returns_generic():
    assert HeuristicProfiler().profile([]).kind == "generic"


# --------------------------------------------------------------------------- model profiler

def test_model_reply_is_used_when_it_validates():
    reply = ('{"environment": "hospital", "robot_radius": 0.3, '
             '"robot_height": 1.2, "max_speed": 0.4}')
    profiler = ModelProfiler(lambda system, user: reply)
    assert profiler.profile(room(10, 5, 2.5)).kind == "hospital"


@pytest.mark.parametrize("reply", [
    "not json at all",
    '{"environment": "spaceship", "robot_radius": 0.3, "robot_height": 1.2, '
    '"max_speed": 0.4}',
    '{"environment": "hospital", "robot_radius": 99, "robot_height": 1.2, '
    '"max_speed": 0.4}',
    '{"environment": "hospital"}',
])
def test_bad_model_replies_fall_back_to_the_heuristic(reply):
    profiler = ModelProfiler(lambda system, user: reply)
    # The heuristic reads this room as an office; the point is it produced *something*.
    assert profiler.profile(room(10, 5, 2.5)).kind == "office"


def test_a_missing_key_falls_back_rather_than_failing_the_twin():
    def no_key(system, user):
        raise RuntimeError("GROQ_API_KEY is not set")

    assert ModelProfiler(no_key).profile(room(5, 4, 2.4)).kind == "home"


def test_profiler_factory_defaults_to_local(monkeypatch):
    monkeypatch.delenv("SCANBORN_PROFILER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert isinstance(get_profiler(), HeuristicProfiler)


def test_profiler_factory_honours_an_explicit_provider():
    assert isinstance(get_profiler("groq"), ModelProfiler)


# ------------------------------------------------------- the profile must change behaviour

def open_room():
    """A 6x6 room with one waist-high table, as a cloud plus its bounding boxes."""
    import numpy as np

    objects = [box("floor", 0, 0, 0, 6, 6, 0.05),
               box("table", 2.5, 2.5, 0, 3.5, 3.5, 0.75)]
    # Floor points plus the table's top surface.
    floor = [(x / 10, y / 10, 0.0) for x in range(0, 61, 2) for y in range(0, 61, 2)]
    table = [(2.5 + x / 20, 2.5 + y / 20, 0.75)
             for x in range(0, 21, 2) for y in range(0, 21, 2)]
    return objects, np.array(floor + table, dtype=float)


def test_profile_travels_with_the_navmesh(tmp_path):
    from twin.generator import generate_twin

    objects, points = open_room()
    result = generate_twin(objects, str(tmp_path), points=points,
                           profile=profile_for("warehouse"))
    import json
    navmesh = json.load(open(result["navmesh_path"]))
    assert navmesh["profile"]["robot_radius"] == profile_for("warehouse").robot_radius
    assert result["profile"]["environment"] == "warehouse"


def test_omitting_the_profile_reproduces_the_generic_robot(tmp_path):
    from twin.generator import generate_twin

    objects, points = open_room()
    result = generate_twin(objects, str(tmp_path), points=points)
    import json
    assert json.load(open(result["navmesh_path"]))["profile"]["environment"] == "generic"


def test_a_bigger_robot_gets_less_walkable_floor(tmp_path):
    """The visible consequence: same scan, same room, different traversable area.

    This is the whole point of a profile. If a forklift and a home robot plan on identical
    grids then the profile is decoration.
    """
    import json

    from policy.evaluate import free_cells, inflate
    from twin.generator import generate_twin

    objects, points = open_room()

    areas = {}
    for kind in ("home", "warehouse"):
        out = tmp_path / kind
        result = generate_twin(objects, str(out), points=points, profile=profile_for(kind))
        navmesh = json.load(open(result["navmesh_path"]))
        areas[kind] = len(free_cells(inflate(navmesh)))

    assert areas["warehouse"] < areas["home"], areas
    assert areas["home"] > 0


def test_sweep_height_decides_whether_an_overhead_shelf_blocks_the_floor(tmp_path):
    """A low robot drives under a high shelf; a tall one does not."""
    import json

    import numpy as np

    from policy.evaluate import free_cells
    from twin.generator import generate_twin

    objects = [box("floor", 0, 0, 0, 6, 6, 0.05),
               box("shelf", 2.0, 2.0, 1.30, 4.0, 4.0, 1.60)]
    floor = [(x / 10, y / 10, 0.0) for x in range(0, 61, 2) for y in range(0, 61, 2)]
    # A shelf suspended at 1.4 m — above a home robot's 0.8 m sweep, inside a
    # warehouse robot's 1.8 m one.
    shelf = [(2.0 + x / 20, 2.0 + y / 20, 1.40)
             for x in range(0, 41, 2) for y in range(0, 41, 2)]
    points = np.array(floor + shelf, dtype=float)

    free = {}
    for kind in ("home", "warehouse"):
        out = tmp_path / f"sweep-{kind}"
        result = generate_twin(objects, str(out), points=points, profile=profile_for(kind))
        navmesh = json.load(open(result["navmesh_path"]))
        # Raw grid, not inflated, so only the sweep band is being measured here.
        free[kind] = len(free_cells(navmesh))

    assert free["home"] > free["warehouse"], free
