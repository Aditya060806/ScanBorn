"""Policy -> the deployable int8 bundle, plus optional exporter experiments.

The artifact is always the local `npz-int8` bundle: the only format ScanBorn's rover, fake rover
and simulator execute (deployment/artifact_format.py). Its format is read back from the bytes
written, not assumed from the code path.

An optional exporter runs only when named: `provider="ai-hub"` (Qualcomm AI Hub compile and
profile) or `provider="qairt"` (a local QAIRT conversion). Each adds an experiment record beside
the bundle — its own format, device and latency source — that is never deployable, because no
runtime here executes those bytes. An `AI_HUB_API_TOKEN` in the environment does nothing on its
own; the orchestrator passes the provider from `SCANBORN_EXPORT` (default `local`).
"""
import importlib.util
import json
import math
import os
import shutil
import sys
import time

import numpy as np

from deployment import artifact_format
from policy.finetune.train_bc import LinearPolicy

EXPORT_PROVIDERS = ("local", "ai-hub", "qairt")
# A local bundle is timed where it was built, so that is the device it names.
LOCAL_DEVICE = "host-cpu"
# The AI Hub experiment compiles for a named device in Qualcomm's cloud.
AI_HUB_DEFAULT_DEVICE = "Snapdragon X Elite CRD"
NOT_DEPLOYABLE = ("Experiment only: no ScanBorn runtime executes this output. The rover, fake "
                  "rover and simulator run the npz-int8 bundle, which is the deployable artifact.")


class ExportConfigurationError(ValueError):
    """The selected exporter cannot run here. Raised before any file is written."""


def _installed(name: str) -> bool:
    """Whether `name` can be imported, without importing it (torch takes seconds to load)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A module already in sys.modules without a spec (a stub, a vendored copy).
        return name in sys.modules


def requirements(provider: str) -> str | None:
    """What stops `provider` from running on this machine, or None when it can run."""
    if provider not in EXPORT_PROVIDERS:
        return (f"unknown export provider {provider!r}; "
                f"expected one of {', '.join(EXPORT_PROVIDERS)}")
    if provider == "ai-hub":
        if not os.environ.get("AI_HUB_API_TOKEN"):
            return ("SCANBORN_EXPORT=ai-hub needs AI_HUB_API_TOKEN "
                    "(a token from https://app.aihub.qualcomm.com)")
        missing = [name for name in ("qai_hub", "torch") if not _installed(name)]
        if missing:
            return (f"SCANBORN_EXPORT=ai-hub needs {' and '.join(missing)}: "
                    "pip install -r requirements-npu.txt")
    if provider == "qairt":
        if shutil.which("qairt-converter") is None:
            return ("SCANBORN_EXPORT=qairt needs qairt-converter on PATH "
                    "(Qualcomm AI Runtime SDK)")
        missing = [name for name in ("torch", "onnx") if not _installed(name)]
        if missing:
            return (f"SCANBORN_EXPORT=qairt needs {' and '.join(missing)} to write the ONNX model "
                    "the converter reads: pip install -r requirements-npu.txt")
    return None


def _measure_latency_ms(policy: LinearPolicy, iterations: int = 2000) -> float:
    """Time a real forward pass on this machine's CPU."""
    obs = np.zeros(policy.obs_dim)
    start = time.perf_counter()
    for _ in range(iterations):
        policy.act(obs)
    return (time.perf_counter() - start) / iterations * 1000.0


def _save_manifest(manifest: dict, out_dir: str) -> dict:
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    manifest["manifest_path"] = manifest_path
    return manifest


def _write_bundle(policy: LinearPolicy, out_dir: str, device_label: str,
                  meta: dict | None = None) -> dict:
    """Quantize and write the deployable bundle; its manifest describes the bytes written.

    `op_coverage` is None: a locally quantized bundle never ran on an accelerator, so there is
    no coverage to report. `meta` says what the numbers mean — observation, action, controller
    constants, robot spec (policy/controller.py:policy_manifest); an artifact without it
    predates the differential-drive simulator and is refused at deploy time.
    """
    os.makedirs(out_dir, exist_ok=True)
    quantized = policy.quantize_int8()
    weights_path = os.path.join(out_dir, "policy_int8.npz")
    np.savez(
        weights_path,
        W=quantized["W"]["data"], W_scale=quantized["W"]["scale"],
        b=quantized["b"]["data"], b_scale=quantized["b"]["scale"],
    )
    found = artifact_format.detect(weights_path)
    if found != artifact_format.NPZ_INT8:
        raise RuntimeError(f"the int8 bundle was written as {found!r}; refusing to label it")

    manifest = {
        "artifact_path": weights_path,
        "format": found,
        "backend": "local",
        "runtime": artifact_format.RUNTIME,
        "executors": list(artifact_format.RUNTIME_EXECUTORS),
        "device_label": device_label,
        "precision": "int8",
        "op_coverage": None,
        "est_latency_ms": round(_measure_latency_ms(policy), 4),
        # NumPy timing on the machine that exported it; not a rover or accelerator number.
        "latency_source": "host-cpu",
        "obs_dim": policy.obs_dim,
        "act_dim": policy.act_dim,
    }
    manifest.update(meta or {})
    return manifest


def export_model(policy_path: str, device_label: str | None = None,
                 out_dir: str | None = None, meta: dict | None = None,
                 provider: str = "local") -> dict:
    """Quantize and package a saved LinearPolicy; optionally run one exporter experiment.

    `device_label` names the exporter's target: the bundle itself for `local` (default
    LOCAL_DEVICE), or the AI Hub device for `ai-hub` (default AI_HUB_DEFAULT_DEVICE), in which
    case the bundle keeps LOCAL_DEVICE. `meta` is written into the manifest; pass
    policy/controller.py:policy_manifest(spec). Raises ExportConfigurationError before any work
    when `provider` is unknown or cannot run here.
    """
    problem = requirements(provider)
    if problem:
        raise ExportConfigurationError(problem)
    policy = LinearPolicy.load(policy_path)
    out_dir = out_dir or os.path.join(os.path.dirname(policy_path) or ".", "artifact")

    bundle_label = device_label if provider == "local" and device_label else LOCAL_DEVICE
    manifest = _write_bundle(policy, out_dir, bundle_label, meta)
    experiments = []
    if provider == "ai-hub":
        experiments.append(_ai_hub_experiment(policy, os.path.join(out_dir, "ai-hub"),
                                              device_label or AI_HUB_DEFAULT_DEVICE))
    elif provider == "qairt":
        from deployment.qairt.convert import convert_to_qairt
        experiments.append(convert_to_qairt(policy_path, os.path.join(out_dir, "qairt")))
    manifest.update(export_provider=provider, experiments=experiments)
    return _save_manifest(manifest, out_dir)


def _ai_hub_experiment(policy: LinearPolicy, out_dir: str, device_label: str) -> dict:
    """Compile and profile the policy on Qualcomm AI Hub; record what came back.

    Untested against the live service: no token or Snapdragon hardware is available here, and
    the tests use stand-ins for `qai_hub` and `torch`. Every failure is recorded, not raised —
    the deployable bundle already exists and is unaffected.
    """
    record: dict = {"provider": "ai-hub", "status": "failed", "deployable": False,
                    "reason": NOT_DEPLOYABLE, "device_label": device_label,
                    "artifact_path": None, "format": None, "est_latency_ms": None,
                    "latency_source": None, "op_coverage": None, "error": None}
    try:
        import qai_hub as hub
        import torch

        module = torch.nn.Linear(policy.obs_dim, policy.act_dim)
        with torch.no_grad():
            module.weight.copy_(torch.tensor(policy.W, dtype=torch.float32))
            module.bias.copy_(torch.tensor(policy.b, dtype=torch.float32))
        traced = torch.jit.trace(module, torch.zeros(1, policy.obs_dim))

        device = hub.Device(device_label)
        compile_job = hub.submit_compile_job(
            model=traced, device=device, input_specs={"obs": (1, policy.obs_dim)},
            options="--quantize_full_type int8")
        target_model = compile_job.get_target_model()
        if target_model is None:
            raise RuntimeError("AI Hub compilation produced no model")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "policy.tflite")
        target_model.download(path)
        found = artifact_format.detect(path)
        record.update(artifact_path=path, format=found)
        if found != artifact_format.TFLITE:
            record["error"] = f"AI Hub returned {found!r} bytes, not the TFLite that was requested"
            return record

        profile = hub.submit_profile_job(model=target_model, device=device).download_profile()
        summary = profile.get("execution_summary") if isinstance(profile, dict) else None
        if not isinstance(summary, dict):
            summary = {}
        estimated = summary.get("estimated_inference_time")
        if (isinstance(estimated, bool) or not isinstance(estimated, (int, float))
                or not math.isfinite(estimated) or estimated < 0):
            record.update(status="profile_malformed",
                          error="AI Hub profile had no numeric "
                                "execution_summary.estimated_inference_time")
            return record
        ratios = summary.get("compute_unit_ratio")
        npu = ratios.get("NPU") if isinstance(ratios, dict) else None
        # No NPU ratio means coverage was not reported, not that it was 100 %.
        coverage = (100.0 * npu if isinstance(npu, (int, float)) and not isinstance(npu, bool)
                    and 0 <= npu <= 1 else None)
        record.update(status="profiled", error=None, est_latency_ms=estimated / 1000.0,
                      latency_source="ai-hub-profile", op_coverage=coverage)
    except Exception as exc:  # noqa: BLE001 - the SDK raises many types; each is recorded
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
    return record
