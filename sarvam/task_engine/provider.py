"""Planner selection.

Local is the product; remote is an explicit escalation. This used to be the other way
round — whichever cloud key happened to be in the environment won, and the on-device
grammar was the consolation prize. For a project whose claim is that nothing leaves the
device, that polarity is a loaded gun: a stray key in a shell profile was enough to put a
network call in the middle of a demo.

So the rule is now: a provider must be *named* to be used. Having GROQ_API_KEY set does
nothing on its own. One environment variable flips the whole pipeline between fully
offline and remote-assisted, which is also what makes it honest to demo either way.
"""
import os
from abc import ABC, abstractmethod

# Providers that reach the network. Anything not in here runs on-device.
REMOTE_PROVIDERS = ("groq", "sarvam")
DEFAULT_PROVIDER = "local"


class TaskPlanner(ABC):
    """Natural language -> TaskGraph."""

    @abstractmethod
    def plan(self, text: str, lang: str = "en"):
        ...


class TaskProvider(ABC):
    """Older single-shot interface, kept for back-compat with existing callers."""

    @abstractmethod
    def execute(self, task: str, context: dict) -> str:
        ...


def selected_provider(provider=None) -> str:
    """Which backend to use: the argument, else SCANBORN_PLANNER, else local."""
    return (provider or os.environ.get("SCANBORN_PLANNER", DEFAULT_PROVIDER)).lower()


def is_remote(provider=None) -> bool:
    """True when the selected planner will make a network call.

    Exposed so the orchestrator and the dashboard can say so out loud. A user who thinks
    the answer came from the phone deserves to know when it did not.
    """
    return selected_provider(provider) in REMOTE_PROVIDERS


def get_planner(objects=None, provider=None) -> TaskPlanner:
    """The planner for this run. On-device unless a remote provider is named.

    Imports are deferred so that selecting the local planner never pulls in httpx, and so
    the remote modules can import TaskPlanner from here without a cycle.
    """
    choice = selected_provider(provider)

    if choice == "groq":
        from .groq_provider import GroqPlanner
        return GroqPlanner(objects)
    if choice == "sarvam":
        from .sarvam_provider import SarvamPlanner
        return SarvamPlanner(objects)

    from .fallback import FunctionGemmaPlanner
    return FunctionGemmaPlanner(objects)
