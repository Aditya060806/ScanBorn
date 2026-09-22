"""Run a workload through its configured provider chain: bounded, cancellable, recorded.

A chain is the ordered list an operator configured (`SCANBORN_PLANNER=groq,local`). Each
provider is tried in order until one returns a result the caller's contract accepts. Around every
remote call:

- a total deadline for the whole chain, and a per-request timeout inside it;
- retries only when the request provably was not processed — the connection never opened, or
  the service answered 429 / 503. A read timeout or a 5xx is an unknown outcome: a paid request
  might have run, so it is never repeated against the same provider;
- a cap on reply size, read before the body is decoded;
- a per-provider concurrency limit;
- a remote-call budget (`Budget.reserve` refuses once the configured ceiling is reached);
- cancellation, checked before each attempt and during every backoff.

Every attempt lands in the provenance record: requested chain, the provider that answered, its
model and location, the input's identity (a hash, never the prompt), attempts with outcomes,
duration, why any fallback happened, and usage only when the provider reported it.
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

PROVENANCE_SCHEMA = "provider-result-v1"
# Outcomes after which the same provider may be asked again: the request was not processed.
RETRYABLE = frozenset({"unreachable", "rate_limited", "unavailable"})


@dataclass(frozen=True)
class Limits:
    deadline_s: float = 20.0
    attempt_timeout_s: float = 10.0
    retries: int = 1
    backoff_s: float = 0.5
    max_response_bytes: int = 64 * 1024
    max_concurrent: int = 2


class ProviderError(Exception):
    """One attempt failed. `outcome` is a fixed vocabulary word; `detail` never holds a secret."""

    def __init__(self, outcome: str, detail: str = "", http_status: int | None = None,
                 retry_after_s: float | None = None):
        super().__init__(f"{outcome}: {detail}" if detail else outcome)
        self.outcome, self.detail = outcome, detail
        self.http_status, self.retry_after_s = http_status, retry_after_s


@dataclass
class Reply:
    """What a provider returned before the contract judged it."""
    value: object
    model: str | None = None
    usage: dict | None = None


@dataclass
class Provider:
    name: str
    location: str                        # "orchestrator" or "remote"
    call: Callable[[float], Reply]       # timeout in seconds -> Reply, or raises ProviderError


@dataclass
class Outcome:
    status: str                          # the accepted verdict, "failed" or "cancelled"
    value: object = None
    provenance: dict = field(default_factory=dict)


class Budget:
    """A ceiling on remote requests. The default counts in memory for one process; the
    orchestrator supplies a persistent one. `reserve` is called once per request sent."""

    def __init__(self, limit: int | None = None):
        self.limit, self.used = limit, 0
        self._lock = threading.Lock()

    def reserve(self, provider: str) -> bool:
        with self._lock:
            if self.limit is not None and self.used >= self.limit:
                return False
            self.used += 1
            return True

    def record(self, provider: str, usage: dict | None) -> None:
        """Called after a reply with the usage the provider reported, if any."""


_SEMAPHORES: dict = {}
_SEMAPHORE_LOCK = threading.Lock()


def _semaphore(name: str, size: int) -> threading.BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        key = (name, size)
        if key not in _SEMAPHORES:
            _SEMAPHORES[key] = threading.BoundedSemaphore(size)
        return _SEMAPHORES[key]


def identity(payload) -> str:
    """A stable hash of a request's input, so provenance can name it without storing it."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_chain(workload: str, providers: list, judge: Callable, *, limits: Limits,
              budget: Budget | None = None, cancel: threading.Event | None = None,
              request_id: str | None = None, input_identity: str | None = None,
              output_schema: str | None = None, data_sent: tuple = (),
              clock: Callable[[], float] = time.monotonic) -> Outcome:
    """Try `providers` in order; return the first reply `judge` accepts.

    `judge(provider, reply)` returns `("accept", value)`, which ends the chain with the status
    taken from the value's own `status` if it has one, or `("fallback", (outcome, reason))`,
    which records why the reply was not used and moves on. The chain never invents a result:
    exhausting it is `failed`.
    """
    budget = budget or Budget()
    cancel = cancel or threading.Event()
    started = clock()
    attempts: list = []
    fallback_reasons: list = []
    provenance: dict = {
        "schema_version": PROVENANCE_SCHEMA, "workload": workload,
        "requested": [p.name for p in providers], "provider": None, "model": None,
        "location": None, "output_schema": output_schema, "input_sha256": input_identity,
        "request_id": request_id, "data_sent": list(data_sent), "attempts": attempts,
        "fallback_reason": None, "usage": None, "duration_ms": None,
    }

    def finish(status: str, value=None) -> Outcome:
        provenance["duration_ms"] = round((clock() - started) * 1000, 1)
        provenance["fallback_reason"] = "; ".join(fallback_reasons) or None
        return Outcome(status, value, provenance)

    for provider in providers:
        tries = 0
        while True:
            if cancel.is_set():
                attempts.append({"provider": provider.name, "outcome": "cancelled"})
                return finish("cancelled")
            remaining = limits.deadline_s - (clock() - started)
            if remaining <= 0:
                attempts.append({"provider": provider.name, "outcome": "deadline_exceeded"})
                fallback_reasons.append(f"{provider.name}: deadline_exceeded")
                return finish("failed")
            attempt: dict = {"provider": provider.name, "outcome": None, "http_status": None,
                             "duration_ms": None, "error": None}
            attempts.append(attempt)
            tries += 1
            began = clock()
            reply = None
            semaphore = None
            retry_after = None
            try:
                if provider.location == "remote":
                    semaphore = _semaphore(provider.name, limits.max_concurrent)
                    if not semaphore.acquire(timeout=max(0.0, min(remaining, 5.0))):
                        semaphore = None
                        raise ProviderError("busy", "concurrency limit reached")
                    if not budget.reserve(provider.name):
                        raise ProviderError("budget_exhausted", "remote request ceiling reached")
                reply = provider.call(min(limits.attempt_timeout_s, remaining))
            except ProviderError as error:
                attempt.update(outcome=error.outcome, http_status=error.http_status,
                               error=error.detail[:300] or None)
                retry_after = error.retry_after_s
            except Exception as exc:  # noqa: BLE001 - a provider bug is recorded, not hidden
                attempt.update(outcome="provider_error", error=type(exc).__name__)
            finally:
                if semaphore is not None:
                    semaphore.release()
                attempt["duration_ms"] = round((clock() - began) * 1000, 1)

            if reply is not None:
                if provider.location == "remote":
                    budget.record(provider.name, reply.usage)
                verdict, value = judge(provider, reply)
                if verdict == "accept":
                    attempt["outcome"] = "accepted"
                    provenance.update(provider=provider.name, model=reply.model,
                                      location=provider.location, usage=reply.usage)
                    status = getattr(value, "status", None) or "accepted"
                    return finish(status, value)
                outcome, reason = value
                attempt.update(outcome=outcome, error=str(reason)[:300])
                fallback_reasons.append(f"{provider.name}: {outcome} ({reason})")
                break

            if attempt["outcome"] in RETRYABLE and tries <= limits.retries:
                delay = max(limits.backoff_s * (2 ** (tries - 1)), retry_after or 0.0)
                if clock() - started + delay >= limits.deadline_s:
                    fallback_reasons.append(f"{provider.name}: {attempt['outcome']}, no time "
                                            "left to retry")
                    break
                if cancel.wait(delay):
                    attempts.append({"provider": provider.name, "outcome": "cancelled"})
                    return finish("cancelled")
                continue
            fallback_reasons.append(f"{provider.name}: {attempt['outcome']}")
            break
    return finish("failed")


# ------------------------------------------------------------------------ remote transport

def post_json(url: str, key: str, body: dict, *, timeout: float, max_bytes: int) -> tuple:
    """POST to an OpenAI-compatible chat endpoint; return (content, model, usage).

    The reply size is enforced while streaming, before anything is decoded. Errors are
    classified into ProviderError outcomes; the key never appears in an error.
    """
    import httpx

    try:
        with httpx.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=body,
                          timeout=timeout) as response:
            if response.status_code >= 400:
                retry_after = response.headers.get("retry-after")
                raise _status_error(response.status_code, retry_after)
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderError("oversized_reply", f"reply exceeds {max_bytes} bytes")
                chunks.append(chunk)
    except httpx.ConnectError as exc:
        raise ProviderError("unreachable", type(exc).__name__) from exc
    except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise ProviderError("unreachable", type(exc).__name__) from exc
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", type(exc).__name__) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("transport_error", type(exc).__name__) from exc
    try:
        doc = json.loads(b"".join(chunks))
        message = doc["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("invalid_reply", "not a chat completion") from exc
    usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else None
    model = doc.get("model") if isinstance(doc.get("model"), str) else None
    return message, model, usage


def _status_error(status: int, retry_after) -> ProviderError:
    try:
        seconds = float(retry_after) if retry_after is not None else None
    except ValueError:
        seconds = None
    if status == 429:
        return ProviderError("rate_limited", "HTTP 429", status, seconds)
    if status == 503:
        return ProviderError("unavailable", "HTTP 503", status, seconds)
    if status in (401, 403):
        return ProviderError("unauthorized", f"HTTP {status}", status)
    if status >= 500:
        # The request reached the provider; whether it ran (and was billed) is unknown.
        return ProviderError("server_error", f"HTTP {status}", status)
    return ProviderError("rejected", f"HTTP {status}", status)
