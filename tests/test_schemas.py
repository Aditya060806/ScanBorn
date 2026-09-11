import pytest

from sarvam.task_engine.graph import VOCABULARY
from sarvam.task_engine.schemas import (
    ENVIRONMENT_KINDS,
    PROFILE_BOUNDS,
    environment_profile_grammar,
    task_graph_grammar,
    task_graph_targets,
    validate_environment_profile,
    validate_task_graph,
)
from semantic.service.inference import LABEL_ONTOLOGY


# ------------------------------------------------------------------------------- targets

def test_scene_objects_come_before_the_ontology():
    targets = task_graph_targets(["widget"])
    assert targets[0] == "widget"
    assert set(LABEL_ONTOLOGY).issubset(set(targets))


def test_targets_are_lowercased_and_deduplicated():
    targets = task_graph_targets(["Table", "table", "TABLE"])
    assert targets.count("table") == 1


def test_targets_without_a_scene_are_just_the_ontology():
    assert task_graph_targets() == list(LABEL_ONTOLOGY)


# ------------------------------------------------------------------------------- grammars

def test_task_graph_grammar_offers_every_action_and_the_scene_objects():
    grammar = task_graph_grammar(["pallet"])
    assert grammar.startswith("root")
    for action in VOCABULARY:
        assert f'"\\"{action}\\""' in grammar
    assert '"\\"pallet\\""' in grammar
    # wait/speak need a target-less option, or the grammar cannot express them.
    assert '"\\"\\""' in grammar


def test_task_graph_grammar_cannot_express_an_off_scene_target():
    assert '"\\"forklift\\""' not in task_graph_grammar(["pallet"])


def test_profile_grammar_offers_every_environment_kind():
    grammar = environment_profile_grammar()
    for kind in ENVIRONMENT_KINDS:
        assert f'"\\"{kind}\\""' in grammar
    for field in PROFILE_BOUNDS:
        assert field in grammar


# ---------------------------------------------------------------------- task graph checks

def test_a_good_graph_has_no_problems():
    doc = {"nodes": [{"action": "navigate_to", "target": "table"},
                     {"action": "wait", "target": ""}]}
    assert validate_task_graph(doc) == []


def test_unknown_action_is_reported():
    doc = {"nodes": [{"action": "teleport", "target": "table"}]}
    assert any("vocabulary" in p for p in validate_task_graph(doc))


def test_off_scene_target_is_reported():
    doc = {"nodes": [{"action": "pickup", "target": "sousaphone"}]}
    assert any("not in the scene" in p for p in validate_task_graph(doc))


def test_scene_objects_make_their_own_labels_legal():
    doc = {"nodes": [{"action": "pickup", "target": "sousaphone"}]}
    assert validate_task_graph(doc, objects=["sousaphone"]) == []


def test_empty_graph_is_valid():
    assert validate_task_graph({"nodes": []}) == []


@pytest.mark.parametrize("doc", [None, [], "nodes", 3])
def test_non_object_payloads_are_rejected(doc):
    assert validate_task_graph(doc)


def test_nodes_must_be_a_list():
    assert validate_task_graph({"nodes": {"action": "wait"}}) == ["'nodes' must be a list"]


# ------------------------------------------------------------------------- profile checks

def good_profile(**over):
    doc = {"environment": "warehouse", "robot_radius": 0.45,
           "robot_height": 1.8, "max_speed": 1.2}
    doc.update(over)
    return doc


def test_a_good_profile_has_no_problems():
    assert validate_environment_profile(good_profile()) == []


def test_unknown_environment_is_reported():
    assert any("not a known kind" in p
               for p in validate_environment_profile(good_profile(environment="spaceship")))


@pytest.mark.parametrize("field,value", [
    ("robot_radius", 4.0),      # would inflate the navmesh to nothing
    ("robot_radius", 0.0),
    ("robot_height", 9.0),
    ("max_speed", 50.0),
])
def test_out_of_range_numbers_are_reported(field, value):
    problems = validate_environment_profile(good_profile(**{field: value}))
    assert any(field in p and "outside" in p for p in problems)


def test_missing_field_is_reported():
    doc = good_profile()
    del doc["max_speed"]
    assert "max_speed is missing" in validate_environment_profile(doc)


def test_booleans_are_not_accepted_as_numbers():
    """bool is a subclass of int in Python, so this needs an explicit guard."""
    problems = validate_environment_profile(good_profile(robot_radius=True))
    assert any("robot_radius must be a number" in p for p in problems)
