"""Suite-wide guards: every test starts local-only and cannot reach the internet.

Provider settings from the developer's shell (a GROQ_API_KEY, an SCANBORN_EXPORT) must not change
what a test exercises, so they are cleared before each test; a test that needs one sets it.

Any HTTP request to a host other than this machine fails the test. The fake rovers, the
TestClient and local servers are unaffected; remote providers are tested against stand-ins, so a
request that would leave the machine is a bug in the test or in the code under test.
"""
import httpx
import pytest

PROVIDER_SETTINGS = ("SCANBORN_PLANNER", "SCANBORN_PROFILER", "SCANBORN_EXPORT", "GROQ_API_KEY",
                     "GROQ_MODEL", "SARVAM_API_KEY", "AI_HUB_API_TOKEN")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


class ExternalRequestBlocked(AssertionError):
    """A test tried to reach a host outside this machine."""


def _refuse_remote(request: httpx.Request) -> None:
    if request.url.host not in LOCAL_HOSTS:
        raise ExternalRequestBlocked(f"test attempted a network request to {request.url.host}")


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    for setting in PROVIDER_SETTINGS:
        monkeypatch.delenv(setting, raising=False)

    sync_send = httpx.HTTPTransport.handle_request
    async_send = httpx.AsyncHTTPTransport.handle_async_request

    def guarded(self, request):
        _refuse_remote(request)
        return sync_send(self, request)

    async def guarded_async(self, request):
        _refuse_remote(request)
        return await async_send(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
