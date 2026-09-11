import json

import httpx
import pytest

from sarvam.task_engine.groq_provider import DEFAULT_MODEL, GroqPlanner, groq_chat


class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def capture(monkeypatch, content='{"nodes": []}'):
    """Swap httpx.post for a recorder; returns the dict the call was made with."""
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json, timeout=timeout)
        return FakeResponse(content)

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


# ------------------------------------------------------------------------------ parse only

def test_parse_builds_sequential_nodes_and_edges():
    reply = json.dumps({"nodes": [
        {"action": "navigate_to", "target": "table"},
        {"action": "pickup", "target": "chair"},
    ]})
    graph = GroqPlanner.parse(reply)
    assert [(n.action, n.target) for n in graph.nodes] == [
        ("navigate_to", "table"), ("pickup", "chair")]
    assert graph.edges == [(graph.nodes[0].id, graph.nodes[1].id)]


def test_parse_drops_actions_outside_the_vocabulary():
    reply = json.dumps({"nodes": [
        {"action": "teleport", "target": "table"},
        {"action": "wait", "target": ""},
    ]})
    assert [n.action for n in GroqPlanner.parse(reply).nodes] == ["wait"]


def test_parse_drops_targets_that_are_not_in_the_scene():
    reply = json.dumps({"nodes": [{"action": "pickup", "target": "sousaphone"}]})
    assert GroqPlanner.parse(reply).nodes == []


def test_parse_accepts_scene_objects_the_ontology_does_not_know():
    reply = json.dumps({"nodes": [{"action": "pickup", "target": "sousaphone"}]})
    graph = GroqPlanner.parse(reply, objects=["sousaphone"])
    assert [n.target for n in graph.nodes] == ["sousaphone"]


def test_parse_keeps_the_good_nodes_when_one_is_bad():
    """Three good steps and one hallucination should still move the robot three steps."""
    reply = json.dumps({"nodes": [
        {"action": "navigate_to", "target": "table"},
        {"action": "fly", "target": "table"},
        {"action": "place", "target": "table"},
    ]})
    graph = GroqPlanner.parse(reply)
    assert [n.action for n in graph.nodes] == ["navigate_to", "place"]
    # Ids stay contiguous after a drop, so the edge chain is still valid.
    assert [n.id for n in graph.nodes] == ["n0", "n1"]
    assert graph.edges == [("n0", "n1")]


def test_parse_is_case_insensitive_about_targets():
    reply = json.dumps({"nodes": [{"action": "pickup", "target": "TABLE"}]})
    assert GroqPlanner.parse(reply).nodes[0].target == "table"


@pytest.mark.parametrize("reply", ['{"nodes": []}', '{}', '{"nodes": [null, 3]}'])
def test_parse_tolerates_empty_and_junk_node_lists(reply):
    assert GroqPlanner.parse(reply).nodes == []


def test_parse_raises_on_content_that_is_not_json():
    with pytest.raises(ValueError):
        GroqPlanner.parse("here is your plan!")


# ------------------------------------------------------------------------- request shaping

def test_request_is_deterministic_and_asks_for_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    seen = capture(monkeypatch)

    GroqPlanner(["pallet"]).plan("go to the pallet")

    assert seen["headers"]["Authorization"] == "Bearer gsk-test"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == DEFAULT_MODEL


def test_model_is_overridable_by_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GROQ_MODEL", "qwen-2.5-32b")
    seen = capture(monkeypatch)

    groq_chat("system", "user")
    assert seen["body"]["model"] == "qwen-2.5-32b"


def test_the_scene_objects_reach_the_prompt(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen = capture(monkeypatch)

    GroqPlanner(["sousaphone"]).plan("grab it")
    system = seen["body"]["messages"][0]["content"]
    assert "sousaphone" in system
    assert "navigate_to" in system


def test_plan_end_to_end_against_a_stubbed_endpoint(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    capture(monkeypatch, content=json.dumps(
        {"nodes": [{"action": "navigate_to", "target": "shelf"}]}))

    graph = GroqPlanner().plan("go to the shelf")
    assert [(n.action, n.target) for n in graph.nodes] == [("navigate_to", "shelf")]


def test_no_key_raises_before_any_network_call(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    def explode(*a, **k):
        raise AssertionError("httpx.post must not be reached without a key")

    monkeypatch.setattr(httpx, "post", explode)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        groq_chat("system", "user")
