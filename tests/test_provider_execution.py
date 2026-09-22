"""The provider chain runner: fallback, retries, deadline, cancellation, budget, provenance.

Providers here are plain functions, so every failure the plan names can be produced on demand:
timeout, rate limit, 5xx, missing credentials, malformed / non-JSON / oversized replies,
hallucinated targets, unsupported actions, invalid numbers, cancellation and exhaustion.
"""
import json
import threading

import pytest

from sarvam.task_engine import execution, runner
from sarvam.task_engine.execution import (Budget, Limits, Provider, ProviderError, Reply,
                                          run_chain)

SCENE = ["floor", "table", "chair", "shelf"]
LIMITS = Limits(deadline_s=30, attempt_timeout_s=5, retries=1, backoff_s=0.0)


def graph(*nodes) -> str:
    return json.dumps({"nodes": [{"action": a, "target": t, **extra} for a, t, extra in nodes]})


def accept_all(provider, reply):
    return "accept", reply.value


class Script:
    """A provider that plays back a list of outcomes: a Reply's value or a ProviderError."""

    def __init__(self, *steps):
        self.steps, self.calls = list(steps), 0

    def __call__(self, timeout):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, ProviderError):
            raise step
        return Reply(step, model="stub-model", usage={"prompt_tokens": 7})


def remote(name, script):
    return Provider(name, "remote", script)


# ------------------------------------------------------------------------ run_chain

def test_the_first_accepted_reply_wins_and_is_recorded():
    outcome = run_chain("planner", [remote("groq", Script("ok"))], accept_all, limits=LIMITS,
                        request_id="r1", input_identity="abc", data_sent=("instruction",))
    assert outcome.status == "accepted" and outcome.value == "ok"
    record = outcome.provenance
    assert record["provider"] == "groq" and record["location"] == "remote"
    assert record["model"] == "stub-model" and record["usage"] == {"prompt_tokens": 7}
    assert record["request_id"] == "r1" and record["input_sha256"] == "abc"
    assert record["data_sent"] == ["instruction"] and record["fallback_reason"] is None
    assert [a["outcome"] for a in record["attempts"]] == ["accepted"]


@pytest.mark.parametrize("error", [
    ProviderError("unreachable", "ConnectError"),
    ProviderError("rate_limited", "HTTP 429", 429),
    ProviderError("unavailable", "HTTP 503", 503),
])
def test_a_request_that_was_not_processed_is_retried_once(error):
    script = Script(error, "ok")
    outcome = run_chain("planner", [remote("groq", script)], accept_all, limits=LIMITS)
    assert outcome.status == "accepted" and script.calls == 2
    assert [a["outcome"] for a in outcome.provenance["attempts"]] == [error.outcome, "accepted"]


@pytest.mark.parametrize("error", [
    ProviderError("timeout", "ReadTimeout"),
    ProviderError("server_error", "HTTP 500", 500),
    ProviderError("unauthorized", "HTTP 401", 401),
    ProviderError("missing_credentials", "GROQ_API_KEY is not set"),
])
def test_an_unknown_outcome_is_never_repeated_against_the_same_provider(error):
    """A read timeout or 5xx may have run (and billed) the request; the chain moves on."""
    groq, local = Script(error, "never"), Script("fallback answer")
    outcome = run_chain("planner", [remote("groq", groq),
                                    Provider("local", "orchestrator", local)],
                        accept_all, limits=LIMITS)
    assert groq.calls == 1 and outcome.value == "fallback answer"
    assert outcome.provenance["provider"] == "local"
    assert outcome.provenance["fallback_reason"] == f"groq: {error.outcome}"


def test_exhaustion_is_a_failure_not_an_invented_result():
    outcome = run_chain("planner", [remote("groq", Script(ProviderError("timeout")))],
                        accept_all, limits=LIMITS)
    assert outcome.status == "failed" and outcome.value is None
    assert outcome.provenance["provider"] is None


def test_retry_after_is_respected_and_limited_by_the_deadline():
    now = [0.0]
    limits = Limits(deadline_s=10, attempt_timeout_s=5, retries=1, backoff_s=0.1)
    script = Script(ProviderError("rate_limited", "HTTP 429", 429, retry_after_s=60), "late")
    outcome = run_chain("planner", [remote("groq", script)], accept_all, limits=limits,
                        clock=lambda: now[0])
    # Waiting 60 s would overrun the 10 s deadline, so it does not wait and does not retry.
    assert outcome.status == "failed" and script.calls == 1
    assert "no time left to retry" in outcome.provenance["fallback_reason"]


def test_the_deadline_stops_the_chain_before_the_next_provider():
    now = [0.0]

    def slow(timeout):
        now[0] += 35  # the first provider overruns the 30 s deadline
        raise ProviderError("timeout")

    outcome = run_chain("planner", [remote("groq", slow),
                                    Provider("local", "orchestrator", Script("late"))],
                        accept_all, limits=LIMITS, clock=lambda: now[0])
    assert outcome.status == "failed"
    assert outcome.provenance["attempts"][-1]["outcome"] == "deadline_exceeded"


def test_each_request_gets_at_most_the_time_left():
    now, seen = [0.0], []

    def record(timeout):
        seen.append(timeout)
        now[0] += 18
        raise ProviderError("timeout")

    run_chain("planner", [remote("groq", record), remote("sarvam", record)], accept_all,
              limits=Limits(deadline_s=20, attempt_timeout_s=10, retries=0),
              clock=lambda: now[0])
    assert seen == [10, 2]


def test_cancellation_is_checked_before_each_attempt():
    cancel = threading.Event()

    def first(timeout):
        cancel.set()  # the operator cancels while the first provider is answering
        raise ProviderError("unreachable")

    outcome = run_chain("planner", [remote("groq", first),
                                    Provider("local", "orchestrator", Script("never"))],
                        accept_all, limits=LIMITS, cancel=cancel)
    assert outcome.status == "cancelled"
    assert outcome.provenance["attempts"][-1]["outcome"] == "cancelled"


def test_the_budget_refuses_remote_requests_but_not_local_ones():
    budget = Budget(limit=1)
    first = run_chain("planner", [remote("groq", Script("ok"))], accept_all, limits=LIMITS,
                      budget=budget)
    assert first.status == "accepted" and budget.used == 1
    second = run_chain("planner", [remote("groq", Script("unreached")),
                                   Provider("local", "orchestrator", Script("local"))],
                       accept_all, limits=LIMITS, budget=budget)
    assert second.provenance["attempts"][0]["outcome"] == "budget_exhausted"
    assert second.value == "local"


def test_the_concurrency_limit_turns_excess_requests_away(monkeypatch):
    monkeypatch.setattr(execution, "_SEMAPHORES", {})
    limits = Limits(deadline_s=30, attempt_timeout_s=5, retries=0, max_concurrent=1)
    gate, entered = threading.Event(), threading.Event()

    def hold(timeout):
        entered.set()
        gate.wait(5)
        return Reply("first")

    worker = threading.Thread(target=run_chain, args=("planner", [remote("groq", hold)],
                                                      accept_all), kwargs={"limits": limits})
    worker.start()
    assert entered.wait(5)
    try:
        # The only slot is taken; this request waits at most min(time left, 5 s), then moves on.
        outcome = run_chain("planner", [remote("groq", Script("second")),
                                        Provider("local", "orchestrator", Script("local"))],
                            accept_all, limits=Limits(deadline_s=0.3, attempt_timeout_s=0.3,
                                                      retries=0, max_concurrent=1))
    finally:
        gate.set()
        worker.join(5)
    assert outcome.provenance["attempts"][0]["outcome"] == "busy"


def test_a_provider_bug_is_recorded_and_the_chain_continues():
    def broken(timeout):
        raise ZeroDivisionError

    outcome = run_chain("planner", [Provider("local", "orchestrator", broken),
                                    remote("groq", Script("ok"))], accept_all, limits=LIMITS)
    assert outcome.value == "ok"
    assert outcome.provenance["attempts"][0] == {**outcome.provenance["attempts"][0],
                                                 "outcome": "provider_error",
                                                 "error": "ZeroDivisionError"}


# ------------------------------------------------------------------------ planner judgement

def planner_chain(monkeypatch, *replies, local=False):
    """A groq stand-in answering `replies` in order, optionally followed by the local planner."""
    script = Script(*replies)
    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request",
                        lambda system, text, timeout, max_bytes: script(timeout))
    return ["groq", "local"] if local else ["groq"], script


@pytest.mark.parametrize("reply,reason", [
    ("here is your plan!", "reply is not JSON"),
    ("[1, 2]", "not an object"),
    ('{"nodes": "table"}', "'nodes' must be a list"),
    (graph(("navigate_to", "sousaphone", {})), "target not offered to the model"),
    (graph(("teleport", "table", {})), "not supported"),
    (graph(("wait", "", {"params": {"seconds": "soon"}})), "out of range"),
    (graph(("wait", "", {"params": {"seconds": 1e9}})), "out of range"),
    (graph(("navigate_to", "table", {"params": {"speed": 3}})), "does not take"),
])
def test_an_invalid_remote_reply_falls_back_with_its_reason(monkeypatch, reply, reason):
    chain, _ = planner_chain(monkeypatch, reply, local=True)
    result = runner.plan_instruction("go to the table", "en", SCENE, chain, limits=LIMITS)
    assert result.status == "planned" and result.provenance["provider"] == "local"
    assert result.provenance["attempts"][0]["outcome"] == "invalid_reply"
    assert reason in result.provenance["fallback_reason"]


def test_an_oversized_remote_reply_is_refused(monkeypatch):
    chain, _ = planner_chain(monkeypatch, "x" * 70_000)
    result = runner.plan_instruction("go to the table", "en", SCENE, chain,
                                     limits=Limits(max_response_bytes=65_536))
    assert result.status == "failed" and "exceeds" in result.provenance["fallback_reason"]


def test_a_remote_plan_is_accepted_with_its_provenance(monkeypatch):
    chain, _ = planner_chain(monkeypatch, graph(("navigate_to", "Table", {})), local=True)
    result = runner.plan_instruction("go to the table", "en", SCENE, chain, limits=LIMITS,
                                     request_id="req-7")
    assert result.status == "planned"
    assert result.graph == {"nodes": [{"id": "n0", "action": "navigate_to", "target": "table",
                                       "params": {}}], "edges": []}
    assert result.provenance["provider"] == "groq" and result.provenance["model"] == "stub-model"
    assert result.provenance["output_schema"] == "task-graph-v1"
    assert result.provenance["data_sent"] == ["instruction", "scene_object_labels"]
    assert len(result.provenance["input_sha256"]) == 64


def test_a_remote_request_for_clarification_is_an_answer_not_a_failure(monkeypatch):
    chain, script = planner_chain(monkeypatch, graph(("navigate_to", "", {})), local=True)
    result = runner.plan_instruction("go over there", "en", SCENE, chain, limits=LIMITS)
    assert result.status == "clarification_needed" and script.calls == 1
    assert result.provenance["provider"] == "groq"


def test_the_local_planner_asks_rather_than_guessing():
    for text, reason in [("go to the kitchen", "not_in_scene"), ("go there", "missing")]:
        result = runner.plan_instruction(text, "en", SCENE, ["local"], limits=LIMITS)
        assert result.status == "clarification_needed" and result.assessment is not None
        assert result.assessment.unresolved[0]["reason"] == reason
    shared = runner.plan_instruction("go to the chair", "en", SCENE + ["chair"], ["local"],
                                     limits=LIMITS)
    assert shared.status == "clarification_needed" and shared.assessment is not None
    assert shared.assessment.unresolved[0]["reason"] == "ambiguous"


def test_an_instruction_nobody_understands_is_unsupported(monkeypatch):
    local_only = runner.plan_instruction("dance a little", "en", SCENE, ["local"], limits=LIMITS)
    assert local_only.status == "unsupported" and local_only.graph is None
    # Local first, then a remote provider: the remote one gets its chance.
    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request",
                        lambda system, text, timeout, max_bytes: Reply(
                            graph(("navigate_to", "table", {})), model="m"))
    escalated = runner.plan_instruction("मेज़ पर जाओ", "hi", SCENE, ["local", "groq"],
                                        limits=LIMITS)
    assert escalated.status == "planned" and escalated.provenance["provider"] == "groq"
    assert escalated.provenance["fallback_reason"].startswith("local: unsupported")


def test_the_input_identity_hashes_the_request_not_its_order():
    first = runner.plan_instruction("go to the table", "en", ["table", "chair"], ["local"])
    second = runner.plan_instruction("go to the table", "en", ["chair", "table"], ["local"])
    assert first.provenance["input_sha256"] == second.provenance["input_sha256"]


# ------------------------------------------------------------------------ profiler judgement

def room():
    return [{"label": "floor", "bbox3d": [0, 0, 0, 6, 5, 0.05]},
            {"label": "table", "bbox3d": [2, 2, 0, 3, 3, 0.75]}]


@pytest.mark.parametrize("reply", [
    "not json",
    json.dumps({"environment": "spaceship", "robot_radius": 0.2, "robot_height": 0.5,
                "max_speed": 0.4}),
    json.dumps({"environment": "home", "robot_radius": 9.0, "robot_height": 0.5,
                "max_speed": 0.4}),
    json.dumps({"environment": "home", "robot_radius": 0.2, "robot_height": 0.5,
                "max_speed": 0.4, "keep_out_m": 0}),
])
def test_a_bad_remote_profile_falls_back_visibly(monkeypatch, reply):
    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request",
                        lambda system, text, timeout, max_bytes: Reply(reply, model="m"))
    result = runner.profile_scene(room(), ["groq", "local"], limits=LIMITS)
    assert result.status == "accepted" and result.provenance["provider"] == "local"
    assert result.provenance["attempts"][0]["outcome"] == "invalid_reply"


def test_a_remote_profiler_alone_fails_rather_than_guessing(monkeypatch):
    def unreachable(system, text, timeout, max_bytes):
        raise ProviderError("unreachable", "ConnectError")

    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request", unreachable)
    result = runner.profile_scene(room(), ["groq"], limits=Limits(retries=0))
    assert result.status == "failed" and result.profile is None


def test_a_valid_remote_profile_is_used(monkeypatch):
    reply = json.dumps({"environment": "office", "robot_radius": 0.25, "robot_height": 0.6,
                        "max_speed": 0.5})
    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request",
                        lambda system, text, timeout, max_bytes: Reply(reply, model="m"))
    result = runner.profile_scene(room(), ["groq", "local"], limits=LIMITS)
    assert result.profile.kind == "office" and result.provenance["provider"] == "groq"
    assert result.provenance["output_schema"] == "environment-profile-v1"
