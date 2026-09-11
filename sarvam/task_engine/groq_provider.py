"""Remote planner backed by Groq's OpenAI-compatible endpoint.

Groq is here for one specific reason: it serves *open-weight* models — the Llama, Qwen and
Gemma families — which is the same class of model the on-device path runs under llama.cpp.
So this is a development stand-in rather than a different architecture: build and debug the
pipeline against a hosted Qwen today, then point the same prompts at the phone's local
Qwen with nothing to rewrite. That is not true of a closed vendor model, where switching
to local means re-tuning every prompt.

It is deliberately not the default. `get_planner` selects local unless a provider is named
explicitly, because a project whose pitch is "nothing leaves the device" must never make a
network call by accident.
"""
import json
import os

from .graph import VOCABULARY, TaskGraph, TaskNode
from .provider import TaskPlanner
from .schemas import task_graph_targets

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq's catalogue moves; override with GROQ_MODEL rather than editing this.
DEFAULT_MODEL = "llama-3.3-70b-versatile"

PROMPT = (
    "Convert the instruction into a JSON task graph for a mobile robot.\n"
    "Use only these actions: {actions}.\n"
    "Targets must come from this list: {targets}.\n"
    "Use an empty target for actions that do not need one.\n"
    'Reply with JSON only, no prose: {{"nodes": [{{"action": "...", "target": "..."}}]}}'
)


def groq_chat(system: str, user: str, *, model=None, timeout: float = 30.0,
              json_object: bool = True) -> str:
    """One chat completion against Groq. Returns the assistant's raw content.

    Shared with the profile planner so both remote paths agree on auth, timeout and JSON
    mode. Raises RuntimeError without a key, matching SarvamPlanner: the caller decides
    whether to fall back, not this function.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set; the local planner needs no key")

    import httpx  # lazy, so importing this module never requires the dependency

    body = {
        "model": model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # Temperature 0: the same instruction must plan the same way twice, or a failed
        # demo cannot be reproduced afterwards.
        "temperature": 0,
    }
    if json_object:
        body["response_format"] = {"type": "json_object"}

    response = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


class GroqPlanner(TaskPlanner):
    """Natural language -> TaskGraph via a Groq-hosted open-weight model."""

    def __init__(self, objects=None, model=None, timeout: float = 30.0):
        self.objects = [str(o) for o in (objects or [])]
        self.model = model
        self.timeout = timeout

    def plan(self, text: str, lang: str = "en") -> TaskGraph:
        targets = task_graph_targets(self.objects)
        system = PROMPT.format(actions=", ".join(VOCABULARY), targets=", ".join(targets))
        content = groq_chat(system, text, model=self.model, timeout=self.timeout)
        return self.parse(content, self.objects)

    @staticmethod
    def parse(content: str, objects=None) -> TaskGraph:
        """Decode and filter a model reply into a graph.

        Invalid nodes are dropped rather than raising. A model that returns three good
        steps and one hallucinated target should still move the robot three steps; the
        on-device path does not need this at all, because the GBNF grammar makes an
        invalid node unrepresentable.
        """
        doc = json.loads(content)
        if not isinstance(doc, dict):
            return TaskGraph()

        legal = set(task_graph_targets(objects)) | {""}
        nodes = []
        for raw in (doc.get("nodes") or []):
            if not isinstance(raw, dict):
                continue
            action, target = raw.get("action"), str(raw.get("target", "")).lower()
            # Never trust a model with the vocabulary, or with this room's contents.
            if action in VOCABULARY and target in legal:
                nodes.append(TaskNode(f"n{len(nodes)}", action, target,
                                      raw.get("params") or {}))
        return TaskGraph(nodes=nodes,
                         edges=[(a.id, b.id) for a, b in zip(nodes, nodes[1:])])
