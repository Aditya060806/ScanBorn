"""Groq transport and the strict judgement of its replies.

The endpoint is a stand-in (tests/conftest.py forbids real network): `httpx.stream` is swapped for
a recorder that returns a canned chat completion. A reply is judged whole by contract.py — the
old behaviour of dropping a hallucinated node and driving the rest is gone.
"""
import json

import httpx
import pytest

from sarvam.task_engine.execution import ProviderError
from sarvam.task_engine.groq_provider import DEFAULT_MODEL, GroqPlanner, groq_chat, groq_request


class FakeStream:
    def __init__(self, status: int, payload: bytes, headers=None):
        self.status_code, self.payload, self.headers = status, payload, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        for start in range(0, len(self.payload), 1024):
            yield self.payload[start:start + 1024]


def completion(content, model="served-model", usage=None) -> bytes:
    doc: dict = {"model": model, "choices": [{"message": {"content": content}}]}
    if usage is not None:
        doc["usage"] = usage
    return json.dumps(doc).encode()


def endpoint(monkeypatch, content='{"nodes": []}', status=200, payload=None, headers=None,
             **reply):
    """Swap httpx.stream for a recorder; returns the dict the call was made with."""
    seen: dict = {}
    body = payload if payload is not None else completion(content, **reply)
    response_headers = headers

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        seen.update(method=method, url=url, headers=headers, body=json, timeout=timeout)
        return FakeStream(status, body, response_headers)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return seen


# ------------------------------------------------------------------------ request shaping

def test_request_is_deterministic_and_asks_for_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen = endpoint(monkeypatch, content=json.dumps(
        {"nodes": [{"action": "navigate_to", "target": "pallet"}]}))

    GroqPlanner(["pallet"]).plan("go to the pallet")

    assert seen["headers"]["Authorization"] == "Bearer gsk-test"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == DEFAULT_MODEL


def test_model_is_overridable_by_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GROQ_MODEL", "qwen-2.5-32b")
    seen = endpoint(monkeypatch)
    groq_chat("system", "user")
    assert seen["body"]["model"] == "qwen-2.5-32b"


def test_the_scene_objects_reach_the_prompt(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen = endpoint(monkeypatch, content=json.dumps(
        {"nodes": [{"action": "pickup", "target": "sousaphone"}]}))
    GroqPlanner(["sousaphone"]).plan("grab it")
    system = seen["body"]["messages"][0]["content"]
    assert "sousaphone" in system and "navigate_to" in system


def test_the_reply_carries_the_serving_model_and_reported_usage(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, usage={"prompt_tokens": 90, "completion_tokens": 12})
    reply = groq_request("system", "user", timeout=5)
    assert reply.model == "served-model"
    assert reply.usage == {"prompt_tokens": 90, "completion_tokens": 12}


def test_no_key_raises_before_any_network_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("httpx.stream must not be reached without a key")

    monkeypatch.setattr(httpx, "stream", explode)
    with pytest.raises(ProviderError, match="GROQ_API_KEY") as excinfo:
        groq_chat("system", "user")
    assert excinfo.value.outcome == "missing_credentials"


# ------------------------------------------------------------------------ transport outcomes

@pytest.mark.parametrize("status,outcome", [(429, "rate_limited"), (503, "unavailable"),
                                            (500, "server_error"), (502, "server_error"),
                                            (401, "unauthorized"), (400, "rejected")])
def test_http_errors_are_classified(monkeypatch, status, outcome):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, status=status, payload=b"{}", headers={"retry-after": "2"})
    with pytest.raises(ProviderError) as excinfo:
        groq_request("system", "user", timeout=5)
    assert excinfo.value.outcome == outcome and excinfo.value.http_status == status
    if status in (429, 503):
        assert excinfo.value.retry_after_s == 2.0


@pytest.mark.parametrize("error,outcome", [
    (httpx.ConnectError("refused"), "unreachable"),
    (httpx.ConnectTimeout("slow connect"), "unreachable"),
    (httpx.ReadTimeout("slow body"), "timeout"),
    (httpx.RemoteProtocolError("reset"), "transport_error"),
])
def test_transport_failures_are_classified(monkeypatch, error, outcome):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(httpx, "stream", fail)
    with pytest.raises(ProviderError) as excinfo:
        groq_request("system", "user", timeout=5)
    assert excinfo.value.outcome == outcome


def test_an_oversized_reply_is_refused_while_streaming(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, payload=b"x" * 5000)
    with pytest.raises(ProviderError) as excinfo:
        groq_request("system", "user", timeout=5, max_bytes=4096)
    assert excinfo.value.outcome == "oversized_reply"


@pytest.mark.parametrize("payload", [b"not json", b'{"choices": []}', b'{"model": "m"}'])
def test_a_reply_that_is_not_a_chat_completion_is_invalid(monkeypatch, payload):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, payload=payload)
    with pytest.raises(ProviderError) as excinfo:
        groq_request("system", "user", timeout=5)
    assert excinfo.value.outcome == "invalid_reply"


# ------------------------------------------------------------------------ strict judgement

def test_plan_end_to_end_against_a_stubbed_endpoint(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    shelf = {"nodes": [{"action": "navigate_to", "target": "shelf"}]}
    endpoint(monkeypatch, content=json.dumps(shelf))
    graph = GroqPlanner().plan("go to the shelf")
    assert [(n.action, n.target) for n in graph.nodes] == [("navigate_to", "shelf")]


@pytest.mark.parametrize("nodes", [
    [{"action": "navigate_to", "target": "table"}, {"action": "fly", "target": "table"}],
    [{"action": "navigate_to", "target": "sousaphone"}],
    [{"action": "wait", "target": "", "params": {"seconds": -3}}],
    [{"action": "wait", "target": "", "params": {"seconds": float("inf")}}],
    [{"action": "navigate_to", "target": "table", "params": {"speed": 9}}],
])
def test_one_bad_step_refuses_the_whole_reply(monkeypatch, nodes):
    """It used to keep the good steps and drive them: a different mission than was asked for."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, content=json.dumps({"nodes": nodes}))
    with pytest.raises(ProviderError) as excinfo:
        GroqPlanner(["table"]).plan("do the thing")
    assert excinfo.value.outcome == "failed"


def test_targets_are_matched_case_insensitively(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    endpoint(monkeypatch, content=json.dumps({"nodes": [{"action": "pickup", "target": "TABLE"}]}))
    assert GroqPlanner(["table"]).plan("pick up the table").nodes[0].target == "table"
