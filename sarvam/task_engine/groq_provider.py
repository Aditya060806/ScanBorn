"""Remote planner and profiler transport: Groq's OpenAI-compatible endpoint.

Groq is here for one specific reason: it serves *open-weight* models — the Llama, Qwen and
Gemma families — which is the same class of model the on-device path runs under llama.cpp.
So this is a development stand-in rather than a different architecture: build and debug the
pipeline against a hosted Qwen today, then point the same prompts at the phone's local
Qwen with nothing to rewrite. That is not true of a closed vendor model, where switching
to local means re-tuning every prompt.

It is remote, and never selected by a key alone: it runs only when named in
`SCANBORN_PLANNER` or `SCANBORN_PROFILER`. Replies are judged by contract.py — nothing here
repairs or filters a model's answer.
"""
import os

from .execution import ProviderError, Reply, post_json
from .graph import TaskGraph
from .provider import TaskPlanner

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq's catalogue moves; override with GROQ_MODEL rather than editing this.
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_MAX_BYTES = 64 * 1024

# The phone's LocalTaskPlanner sends this same text (tests/test_planner_parity.py).
PROMPT = (
    "Convert the instruction into a JSON task graph for a mobile robot.\n"
    "Use only these actions: {actions}.\n"
    "Targets must come from this list: {targets}.\n"
    "Use an empty target for actions that do not need one.\n"
    'Reply with JSON only, no prose: {{"nodes": [{{"action": "...", "target": "..."}}]}}'
)


def groq_request(system: str, user: str, *, timeout: float, model=None,
                 max_bytes: int = DEFAULT_MAX_BYTES, json_object: bool = True) -> Reply:
    """One chat completion against Groq, as a Reply with the model and usage it reported.

    Raises ProviderError: `missing_credentials` without a key (before any network call), or the
    transport outcome from execution.post_json.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise ProviderError("missing_credentials", "GROQ_API_KEY is not set")
    body: dict = {
        "model": model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL,
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
    content, answered_by, usage = post_json(GROQ_URL, key, body, timeout=timeout,
                                            max_bytes=max_bytes)
    return Reply(content, answered_by or body["model"], usage)


def groq_chat(system: str, user: str, *, model=None, timeout: float = 30.0,
              json_object: bool = True) -> str:
    """The reply text alone, for callers that only need content (ModelProfiler)."""
    return str(groq_request(system, user, timeout=timeout, model=model,
                            json_object=json_object).value)


class GroqPlanner(TaskPlanner):
    """Natural language -> TaskGraph via a Groq-hosted open-weight model, one provider chain."""

    def __init__(self, objects=None, model=None, timeout: float = 30.0):
        self.objects = [str(o) for o in (objects or [])]
        self.model = model
        self.timeout = timeout

    def plan(self, text: str, lang: str = "en") -> TaskGraph:
        from .runner import plan_or_raise

        return plan_or_raise("groq", text, lang, self.objects, timeout=self.timeout)
