"""The planner and profiler workloads: provider chain in, judged result and provenance out.

This is what the orchestrator calls. Each provider name becomes a `Provider` (execution.py);
every reply is judged by contract.py; the chain moves on only for a reason it records. A remote
model's target that is not in this scene is a hallucination and triggers fallback. The local
keyword planner only echoes words the operator used, so an off-scene target from it is a real
request the scene cannot satisfy, and the answer is to ask — never to aim somewhere else.
"""
import json
import threading
from dataclasses import dataclass
from typing import Any

from semantic.service.inference import LABEL_ONTOLOGY

from .contract import (PROFILE_SCHEMA, TASK_GRAPH_SCHEMA, Assessment, assess_graph, decode,
                       normalise_graph, profile_problems)
from .execution import Budget, Limits, Provider, ProviderError, Reply, identity, run_chain
from .graph import VOCABULARY, TaskGraph

PLAN_DATA = ("instruction", "scene_object_labels")
PROFILE_DATA = ("scene_summary",)


@dataclass
class PlanResult:
    status: str       # planned, clarification_needed, unsupported, failed or cancelled
    graph: dict | None                # canonical graph when planned; the candidate when asking
    assessment: Assessment | None
    provenance: dict


@dataclass
class _Judged:
    status: str
    graph: dict
    assessment: Assessment


def _targets(scene_labels) -> list:
    if scene_labels is None:
        return list(LABEL_ONTOLOGY)
    seen: list = []
    for label in scene_labels:
        label = str(label).lower()
        if label not in seen:
            seen.append(label)
    return seen


def _planner(name: str, text: str, scene_labels, limits: Limits) -> Provider:
    if name == "local":
        from .fallback import KeywordPlanner

        def local(timeout: float) -> Reply:
            graph, not_understood = KeywordPlanner(scene_labels or []).interpret(text)
            doc = json.loads(graph.to_json())
            if not_understood:
                doc["not_understood"] = not_understood
            return Reply(doc, model="keyword-grammar")

        return Provider("local", "orchestrator", local)

    from .groq_provider import PROMPT

    system = PROMPT.format(actions=", ".join(VOCABULARY),
                           targets=", ".join(_targets(scene_labels)) or "none")
    if name == "groq":
        from .groq_provider import groq_request

        return Provider("groq", "remote", lambda timeout: groq_request(
            system, text, timeout=timeout, max_bytes=limits.max_response_bytes))
    if name == "sarvam":
        from .sarvam_provider import sarvam_request

        return Provider("sarvam", "remote", lambda timeout: sarvam_request(
            system, text, timeout=timeout, max_bytes=limits.max_response_bytes))
    raise ValueError(f"unknown planner {name!r}")


def plan_instruction(text: str, lang: str, scene_labels, chain, *, limits: Limits = Limits(),
                     budget: Budget | None = None, cancel: threading.Event | None = None,
                     request_id: str | None = None) -> PlanResult:
    """Plan `text` against this scene. `scene_labels` has one entry per scene object (None for
    a preview without a scene)."""
    def judge(provider: Provider, reply: Reply):
        doc = reply.value
        if provider.location == "remote":
            doc, why = decode(reply.value, limits.max_response_bytes)
            if why:
                return "fallback", ("invalid_reply", why)
        not_understood = doc.pop("not_understood", []) if isinstance(doc, dict) and \
            provider.location != "remote" else []
        verdict = assess_graph(doc, scene_labels)
        if not_understood and verdict.status in ("planned", "clarification_needed"):
            # Part of the instruction was not understood: planning around it would drive a
            # different mission than the operator asked for.
            verdict = Assessment(
                "clarification_needed",
                verdict.problems + [f"did not understand {clause!r}" for clause in not_understood],
                verdict.unresolved + [{"step": None, "target": clause, "reason": "not_understood"}
                                      for clause in not_understood],
                verdict.choices or _targets(scene_labels))
        if verdict.status == "invalid":
            return "fallback", ("invalid_reply", "; ".join(verdict.problems[:3]))
        if verdict.status == "unsupported":
            return "fallback", ("unsupported", verdict.problems[0])
        invented = [u["target"] for u in verdict.unresolved if u["reason"] == "not_in_scene"]
        if provider.location == "remote" and invented:
            return "fallback", ("invalid_reply", f"target not offered to the model: {invented}")
        graph = normalise_graph(doc)
        return "accept", _Judged(verdict.status, graph, verdict)

    providers = [_planner(name, text, scene_labels, limits) for name in chain]
    outcome = run_chain(
        "planner", providers, judge, limits=limits, budget=budget, cancel=cancel,
        request_id=request_id, output_schema=TASK_GRAPH_SCHEMA, data_sent=PLAN_DATA,
        input_identity=identity({"text": text, "lang": lang,
                                 "scene_labels": sorted(_targets(scene_labels))}))
    attempts = outcome.provenance["attempts"]
    if isinstance(outcome.value, _Judged):
        return PlanResult(outcome.value.status, outcome.value.graph, outcome.value.assessment,
                          outcome.provenance)
    status = outcome.status
    if status == "failed" and attempts and all(a["outcome"] == "unsupported" for a in attempts):
        status = "unsupported"   # every provider answered; none recognised an action
    return PlanResult(status, None, None, outcome.provenance)


def plan_or_raise(provider: str, text: str, lang: str, objects, *, timeout: float) -> TaskGraph:
    """One named planner as a TaskPlanner: the graph when planned, otherwise ProviderError."""
    result = plan_instruction(text, lang, objects or None, [provider],
                              limits=Limits(attempt_timeout_s=timeout, deadline_s=timeout + 5))
    if result.status != "planned" or result.graph is None:
        attempts = result.provenance["attempts"]
        detail = attempts[-1].get("error") if attempts else None
        raise ProviderError(result.status, detail or "")
    return TaskGraph.from_json(json.dumps(result.graph))


# ------------------------------------------------------------------------ profiles

@dataclass
class ProfileResult:
    status: str                       # accepted, failed or cancelled
    profile: Any                      # twin.profile.EnvironmentProfile when accepted
    provenance: dict


def profile_scene(objects, chain, *, limits: Limits = Limits(), budget: Budget | None = None,
                  cancel: threading.Event | None = None,
                  request_id: str | None = None) -> ProfileResult:
    """Pick an environment profile for a segmented scene through the profiler chain."""
    from twin.profile import from_dict

    from .groq_provider import groq_request
    from .profile_planner import PROMPT, HeuristicProfiler, summarize
    from .schemas import ENVIRONMENT_KINDS

    summary = summarize(objects)
    system = PROMPT.format(kinds=", ".join(ENVIRONMENT_KINDS))

    def build(name: str) -> Provider:
        if name == "local":
            return Provider("local", "orchestrator", lambda timeout: Reply(
                HeuristicProfiler().profile(objects), model="geometry-heuristic"))
        if name == "groq":
            return Provider("groq", "remote", lambda timeout: groq_request(
                system, summary, timeout=timeout, max_bytes=limits.max_response_bytes))
        raise ValueError(f"unknown profiler {name!r}")

    def judge(provider: Provider, reply: Reply):
        if provider.location != "remote":
            return "accept", reply.value
        doc, why = decode(reply.value, limits.max_response_bytes)
        if why:
            return "fallback", ("invalid_reply", why)
        problems = profile_problems(doc)
        if problems:
            return "fallback", ("invalid_reply", "; ".join(problems[:3]))
        return "accept", from_dict(doc)

    outcome = run_chain("profiler", [build(name) for name in chain], judge, limits=limits,
                        budget=budget, cancel=cancel, request_id=request_id,
                        output_schema=PROFILE_SCHEMA, data_sent=PROFILE_DATA,
                        input_identity=identity({"summary": summary}))
    status = "accepted" if outcome.value is not None else outcome.status
    return ProfileResult(status, outcome.value, outcome.provenance)
