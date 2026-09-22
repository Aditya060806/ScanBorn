"""Which providers each workload uses, in what order, within what limits — checked before work.

Three workloads can use something other than this machine: task planning (`SCANBORN_PLANNER`),
environment profiling (`SCANBORN_PROFILER`) and export experiments (`SCANBORN_EXPORT`). Each
defaults to `local`. A provider runs only when it is named; an API key alone never selects one.

Planner and profiler settings are ordered chains: `groq,local` asks Groq first and falls back to
the local provider, recording why (sarvam_provider/runner). Export takes one provider. An
unsupported name, a repeated name, a missing credential, a missing package or an invalid limit is
refused before the stage creates a job or writes a file. `GET /providers` reports the same checks,
the limits and today's remote usage, never credential values.

Limits (all optional; defaults in brackets): SCANBORN_PROVIDER_DEADLINE_S [20] whole chain,
SCANBORN_PROVIDER_TIMEOUT_S [10] one request, SCANBORN_PROVIDER_RETRIES [1] extra tries when a
request provably was not processed, SCANBORN_PROVIDER_MAX_BYTES [65536] reply size,
SCANBORN_PROVIDER_CONCURRENCY [2] concurrent requests per remote provider, and
SCANBORN_REMOTE_DAILY_LIMIT [200] remote requests per UTC day across providers, persisted so a
restart does not reset it. Usage cost is not known; the ceiling counts requests.
"""
import datetime
import json
import logging
import os
import threading
from pathlib import Path
from contextlib import closing, contextmanager
from typing import Any

from fastapi import HTTPException

from deployment.aihub_export.export_script import EXPORT_PROVIDERS
from deployment.aihub_export.export_script import requirements as export_requirements
from orchestrator import db
from capture.service.store import atomic_write, data_root
from sarvam.task_engine.execution import Budget, Limits
from sarvam.task_engine.profile_planner import PROFILER_PROVIDERS
from sarvam.task_engine.provider import PLANNER_PROVIDERS

WORKLOADS = {
    "planner": ("SCANBORN_PLANNER", PLANNER_PROVIDERS),
    "profiler": ("SCANBORN_PROFILER", PROFILER_PROVIDERS),
    "export": ("SCANBORN_EXPORT", EXPORT_PROVIDERS),
}
CHAINED = ("planner", "profiler")
# Where each provider executes. `local` and `qairt` run on the orchestrator's own machine; the
# phone's on-device model is a separate client capability and is not selected here.
LOCATIONS = {"local": "orchestrator", "qairt": "orchestrator", "groq": "remote",
             "sarvam": "remote", "ai-hub": "remote"}
CREDENTIALS = {"groq": "GROQ_API_KEY", "sarvam": "SARVAM_API_KEY", "ai-hub": "AI_HUB_API_TOKEN"}
# What a remote provider receives, per workload. Scans, depth frames and telemetry never leave.
DATA_SENT = {"planner": ["instruction", "scene_object_labels"],
             "profiler": ["scene_summary"],
             "export": ["policy_weights"]}
# (setting, Limits field or None for the budget, type, minimum, maximum, default)
LIMIT_SETTINGS = (
    ("SCANBORN_PROVIDER_DEADLINE_S", "deadline_s", float, 1.0, 120.0, 20.0),
    ("SCANBORN_PROVIDER_TIMEOUT_S", "attempt_timeout_s", float, 1.0, 60.0, 10.0),
    ("SCANBORN_PROVIDER_RETRIES", "retries", int, 0, 3, 1),
    ("SCANBORN_PROVIDER_MAX_BYTES", "max_response_bytes", int, 1024, 1_048_576, 65_536),
    ("SCANBORN_PROVIDER_CONCURRENCY", "max_concurrent", int, 1, 8, 2),
    ("SCANBORN_REMOTE_DAILY_LIMIT", None, int, 0, 100_000, 200),
)

log = logging.getLogger("scanborn.providers")
_SETTINGS_LOCK = threading.RLock()


def _settings_path() -> Path:
    return Path(data_root()) / "provider-settings.json"


def _saved_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError("settings exceed size limit")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("version") != 1:
            raise ValueError("unsupported version")
        _validate_settings(result["workloads"], result["remote_consent"], readiness=False)
        return result
    except (ValueError, KeyError, TypeError, AttributeError, OSError, HTTPException) as exc:
        raise HTTPException(503, "Saved provider settings are invalid; explicitly save a valid "
                            "configuration to recover") from exc


def _validate_settings(workloads, consent, readiness=True):
    if not isinstance(workloads, dict) or set(workloads) != set(WORKLOADS):
        raise HTTPException(400, "Provide planner, profiler and export provider chains")
    if not isinstance(consent, dict) or set(consent) - set(WORKLOADS):
        raise HTTPException(400, "Invalid remote consent mapping")
    if any(scope != DATA_SENT[workload] for workload, scope in consent.items()):
        raise HTTPException(400, "Consent must name the exact published data scope")
    for workload, names in workloads.items():
        if (not isinstance(names, list) or not 1 <= len(names) <= 3 or
                any(not isinstance(n, str) or n not in WORKLOADS[workload][1] for n in names) or
                len(set(names)) != len(names) or (workload == "export" and len(names) != 1)):
            raise HTTPException(400, f"Invalid {workload} provider chain")
        remote = any(LOCATIONS[n] == "remote" for n in names)
        if remote and consent.get(workload) != DATA_SENT[workload]:
            raise HTTPException(400, f"Explicit consent required for {workload}: "
                                f"{', '.join(DATA_SENT[workload])}")
        if readiness:
            reason = problem(workload, names)
            if reason:
                raise _refusal(workload, names, reason)


def save_settings(body: dict) -> dict:
    """An explicit, atomic operator selection. Contains no credentials or remote requests."""
    if set(body) != {"workloads", "remote_consent"}:
        raise HTTPException(400, "Provide workloads and remote_consent only")
    _validate_settings(body["workloads"], body["remote_consent"])
    with _SETTINGS_LOCK:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(path), json.dumps({"version": 1, **body}, allow_nan=False).encode())
    return report()


def configured(workload: str) -> list:
    """The providers named for `workload`, in order; unset or blank means `["local"]`."""
    env, _ = WORKLOADS[workload]
    saved = _saved_settings()
    if saved:
        return saved["workloads"][workload]
    names = [name.strip().lower() for name in (os.environ.get(env) or "local").split(",")]
    return [name for name in names if name] or ["local"]


def _limits() -> tuple:
    """(Limits, daily ceiling, problem or None) from the environment."""
    values: dict[str, Any] = {}
    problems: list = []
    ceiling = 200
    for setting, name, kind, low, high, default in LIMIT_SETTINGS:
        raw = os.environ.get(setting)
        value: Any = default
        if raw:
            try:
                value = kind(raw)
                if not low <= value <= high:
                    raise ValueError
            except ValueError:
                problems.append(f"{setting}={raw!r} must be a {kind.__name__} in {low}..{high}")
                value = default
        if name is None:
            ceiling = int(value)
        else:
            values[name] = value
    return Limits(**values), ceiling, "; ".join(problems) or None


def limits() -> Limits:
    return _limits()[0]


def problem(workload: str, names: list) -> str | None:
    """Why `names` cannot serve `workload` on this machine, or None when they can."""
    env, allowed = WORKLOADS[workload]
    if workload not in CHAINED and len(names) > 1:
        return f"{env} takes one provider; got {','.join(names)}"
    if len(set(names)) != len(names):
        return f"{env}={','.join(names)} names a provider twice"
    for name in names:
        if name not in allowed:
            return f"{env}={name!r} is not supported; use one of {', '.join(allowed)}"
        if workload == "export":
            reason = export_requirements(name)
            if reason:
                return reason
            continue
        credential = CREDENTIALS.get(name)
        if credential and not os.environ.get(credential):
            return f"{env}={name} needs {credential}; name only local providers to stay offline"
    if workload in CHAINED:
        return _limits()[2]
    return None


def _refusal(workload: str, names: list, reason: str) -> HTTPException:
    env, _ = WORKLOADS[workload]
    return HTTPException(503, {"code": "provider_configuration", "workload": workload,
                               "setting": env, "provider": ",".join(names), "error": reason})


def chain(workload: str) -> list:
    """The provider chain to run, or a 503 naming what to fix. Call before creating a job."""
    names = configured(workload)
    reason = problem(workload, names)
    if reason:
        raise _refusal(workload, names, reason)
    return names


def require(workload: str) -> str:
    """The single provider for a workload without chains (export), checked like `chain`."""
    return chain(workload)[0]


# ------------------------------------------------------------------------ budget

class PersistentBudget(Budget):
    """Counts remote requests per UTC day in SQLite, so a restart cannot reset the ceiling."""

    def __init__(self, limit: int):
        super().__init__(limit)

    @staticmethod
    def _day() -> str:
        return datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    def reserve(self, provider: str) -> bool:
        with self._lock, closing(db.connect()) as conn:
            db.init_db(conn)
            with conn:
                used = conn.execute("SELECT COALESCE(SUM(calls), 0) FROM provider_usage "
                                    "WHERE day = ?", (self._day(),)).fetchone()[0]
                if self.limit is not None and used >= self.limit:
                    return False
                conn.execute(
                    "INSERT INTO provider_usage(id, day, provider, calls) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(id) DO UPDATE SET calls = calls + 1",
                    (f"{self._day()}:{provider}", self._day(), provider))
            return True

    def record(self, provider: str, usage: dict | None) -> None:
        if not usage:
            return
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        tokens = [value if isinstance(value, int) and not isinstance(value, bool) else None
                  for value in (prompt, completion)]
        if tokens == [None, None]:
            return
        with self._lock, closing(db.connect()) as conn:
            db.init_db(conn)
            with conn:
                conn.execute(
                    "UPDATE provider_usage SET prompt_tokens = COALESCE(prompt_tokens, 0) + ?, "
                    "completion_tokens = COALESCE(completion_tokens, 0) + ? WHERE id = ?",
                    (tokens[0] or 0, tokens[1] or 0, f"{self._day()}:{provider}"))


def budget() -> PersistentBudget:
    return PersistentBudget(_limits()[1])


def usage_today() -> list:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        rows = conn.execute("SELECT provider, calls, prompt_tokens, completion_tokens "
                            "FROM provider_usage WHERE day = ? ORDER BY provider",
                            (PersistentBudget._day(),)).fetchall()
    return [dict(row) for row in rows]


# ------------------------------------------------------------------------ cancellation

_INFLIGHT: dict = {}
_INFLIGHT_LOCK = threading.Lock()


@contextmanager
def track(request_id: str | None):
    """Register a chain run so `cancel(request_id)` can stop it between attempts."""
    event = threading.Event()
    if request_id:
        with _INFLIGHT_LOCK:
            if request_id in _INFLIGHT:
                raise HTTPException(409, {"code": "request_running",
                                          "error": "This request_id is already running"})
            _INFLIGHT[request_id] = event
    try:
        yield event
    finally:
        if request_id:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(request_id, None)


def cancel(request_id: str) -> bool:
    with _INFLIGHT_LOCK:
        event = _INFLIGHT.get(request_id)
    if event is None:
        return False
    event.set()
    return True


# ------------------------------------------------------------------------ report

def report() -> dict:
    """Every workload's chain and readiness, the limits and today's usage; no secrets."""
    current, ceiling, limit_problem = _limits()
    workloads = {}
    for workload, (env, allowed) in WORKLOADS.items():
        names = configured(workload)
        reason = problem(workload, names)
        workloads[workload] = {
            "setting": env, "selected": ",".join(names), "chain": names,
            "allowed": list(allowed),
            "location": ("remote" if any(LOCATIONS.get(n) == "remote" for n in names)
                         else "orchestrator") if all(n in allowed for n in names) else None,
            "credential": [{"setting": CREDENTIALS[n], "configured": bool(os.environ.get(
                CREDENTIALS[n]))} for n in names if n in CREDENTIALS] or None,
            "data_sent_remotely": DATA_SENT[workload] if any(
                LOCATIONS.get(n) == "remote" for n in names) else [],
            "ready": reason is None, "error": reason,
        }
    return {"default": "local", "keys_alone_select_nothing": True, "workloads": workloads,
            "configuration_source": "saved_settings" if _saved_settings() else "environment",
            "remote_data_scopes": DATA_SENT,
            "provider_locations": LOCATIONS,
            "limits": {**current.__dict__, "remote_daily_limit": ceiling,
                       "error": limit_problem},
            "remote_usage_today": usage_today()}


def log_configuration() -> None:
    """At startup: say which workloads are remote, and which settings will be refused."""
    try:
        workloads = report()["workloads"]
    except HTTPException as exc:
        log.warning("Provider configuration unavailable: %s", exc.detail)
        return  # Keep local status/stop and the explicit configuration repair endpoint available.
    for workload, entry in workloads.items():
        if entry["error"]:
            log.warning("%s: %s — its stage will refuse until this is fixed", workload,
                        entry["error"])
        elif entry["location"] == "remote":
            log.info("%s uses %s; its requests leave this machine", workload, entry["selected"])
