import pytest

from sarvam.task_engine.fallback import KeywordPlanner
from sarvam.task_engine.graph import VOCABULARY, TaskGraph, TaskNode
from sarvam.task_engine.groq_provider import GroqPlanner
from sarvam.task_engine.execution import ProviderError
from sarvam.task_engine.provider import ProviderSelectionError, get_planner, is_remote
from sarvam.task_engine.sarvam_provider import SarvamPlanner


def plan(text, objects=None):
    return KeywordPlanner(objects or []).plan(text)


def test_two_step_command_becomes_two_sequential_nodes():
    graph = plan("go to the shelf then pick up the box", objects=["shelf", "box"])
    assert [(n.action, n.target) for n in graph.nodes] == [
        ("navigate_to", "shelf"),
        ("pickup", "box"),
    ]
    assert graph.edges == [(graph.nodes[0].id, graph.nodes[1].id)]


@pytest.mark.parametrize("text,action", [
    ("go to the table", "navigate_to"),
    ("drive to the door", "navigate_to"),
    ("grab the chair", "pickup"),
    ("put down the box", "place"),
    ("inspect the shelf", "inspect"),
    ("wait", "wait"),
    ("say hello", "speak"),
])
def test_verbs_map_into_the_vocabulary(text, action):
    node = plan(text, objects=["box"]).nodes[0]
    assert node.action == action and node.action in VOCABULARY


def test_splits_on_commas_and_and():
    graph = plan("go to the door, inspect the window and wait")
    assert [n.action for n in graph.nodes] == ["navigate_to", "inspect", "wait"]


def test_unknown_target_falls_back_to_the_trailing_noun():
    assert plan("go to the sousaphone").nodes[0].target == "sousaphone"


def test_scene_objects_are_preferred_targets():
    assert plan("pick up the red widget", objects=["widget"]).nodes[0].target == "widget"


def test_a_verbless_clause_naming_an_object_repeats_the_previous_action():
    """ "then the door" used to vanish: the graph drove to the table only."""
    graph, not_understood = KeywordPlanner(["table", "door"]).interpret(
        "go to the table, then the door")
    assert [(n.action, n.target) for n in graph.nodes] == [
        ("navigate_to", "table"), ("navigate_to", "door")]
    assert not_understood == []


def test_a_clause_it_cannot_interpret_is_reported_not_dropped():
    graph, not_understood = KeywordPlanner(["table"]).interpret("go to the table and dance")
    assert [n.action for n in graph.nodes] == ["navigate_to"]
    assert not_understood == ["dance"]
    assert KeywordPlanner(["shelf"]).interpret("please go to the shelf now")[1] == []


def test_speak_carries_its_words_in_params_and_wait_has_no_target():
    graph = plan("say hello then wait")
    assert [(n.action, n.target, n.params) for n in graph.nodes] == [
        ("speak", "", {"text": "hello"}), ("wait", "", {})]


def test_unparseable_text_yields_an_empty_graph():
    assert plan("").nodes == []


def test_graph_json_round_trip():
    graph = TaskGraph(nodes=[TaskNode("n0", "wait", "", {"seconds": 3})], edges=[])
    assert TaskGraph.from_json(graph.to_json()) == graph


def test_factory_defaults_to_the_offline_planner(monkeypatch):
    monkeypatch.delenv("SCANBORN_PLANNER", raising=False)
    assert isinstance(get_planner(["table"]), KeywordPlanner)


def test_api_keys_alone_never_reach_the_network(monkeypatch):
    """The regression that matters: a stray key must not silently go remote.

    This asserted the opposite before — whichever cloud key was in the environment won.
    A key in a shell profile was then enough to put a network call inside a demo whose
    whole claim is that nothing leaves the device.
    """
    monkeypatch.delenv("SCANBORN_PLANNER", raising=False)
    monkeypatch.setenv("SARVAM_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert isinstance(get_planner([]), KeywordPlanner)
    assert is_remote() is False


@pytest.mark.parametrize("provider,expected", [
    ("local", KeywordPlanner),
    ("sarvam", SarvamPlanner),
    ("groq", GroqPlanner),
])
def test_naming_a_provider_selects_it(monkeypatch, provider, expected):
    monkeypatch.setenv("SCANBORN_PLANNER", provider)
    assert isinstance(get_planner([]), expected)


@pytest.mark.parametrize("name", ["does-not-exist", "grok", "ollama"])
def test_an_unknown_provider_is_refused_rather_than_quietly_local(monkeypatch, name):
    # It used to fall back to local, so a typo looked like a working remote configuration.
    monkeypatch.setenv("SCANBORN_PLANNER", name)
    with pytest.raises(ProviderSelectionError, match="local, groq, sarvam"):
        get_planner([])
    with pytest.raises(ProviderSelectionError):
        is_remote()


def test_a_blank_or_differently_cased_setting_is_normalised(monkeypatch):
    monkeypatch.setenv("SCANBORN_PLANNER", "  GROQ ")
    assert isinstance(get_planner([]), GroqPlanner)
    monkeypatch.setenv("SCANBORN_PLANNER", "")
    assert isinstance(get_planner([]), KeywordPlanner)


@pytest.mark.parametrize("provider,remote", [
    ("local", False), ("groq", True), ("sarvam", True),
])
def test_is_remote_reports_whether_the_network_is_used(monkeypatch, provider, remote):
    monkeypatch.setenv("SCANBORN_PLANNER", provider)
    assert is_remote() is remote


def test_sarvam_planner_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="SARVAM_API_KEY"):
        SarvamPlanner().plan("go to the table")


def test_groq_planner_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GROQ_API_KEY"):
        GroqPlanner().plan("go to the table")
