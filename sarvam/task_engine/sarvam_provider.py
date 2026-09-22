"""Remote planner transport: the Sarvam API (multilingual).

Runs only when `SCANBORN_PLANNER` names it. It sends the same prompt as the Groq path, so both
remote planners are asked for the same contract and judged by the same checks in contract.py:
an action outside the vocabulary or a target outside this scene is refused, not filtered.
"""
import os

from .execution import ProviderError, Reply, post_json
from .graph import TaskGraph
from .provider import TaskPlanner

SARVAM_URL = "https://api.sarvam.ai/v1/chat/completions"
DEFAULT_MODEL = "sarvam-m"


def sarvam_request(system: str, user: str, *, timeout: float, model: str = DEFAULT_MODEL,
                   max_bytes: int = 64 * 1024) -> Reply:
    """One chat completion against Sarvam; raises ProviderError like groq_request."""
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise ProviderError("missing_credentials", "SARVAM_API_KEY is not set")
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0}
    content, answered_by, usage = post_json(SARVAM_URL, key, body, timeout=timeout,
                                            max_bytes=max_bytes)
    return Reply(content, answered_by or model, usage)


class SarvamPlanner(TaskPlanner):
    def __init__(self, objects=None, model: str = DEFAULT_MODEL, timeout: float = 20.0):
        self.objects = [str(o) for o in (objects or [])]
        self.model = model
        self.timeout = timeout

    def plan(self, text: str, lang: str = "en") -> TaskGraph:
        from .runner import plan_or_raise

        return plan_or_raise("sarvam", text, lang, self.objects, timeout=self.timeout)
