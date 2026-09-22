"""QAIRT conversion experiment: the policy as ONNX, converted by `qairt-converter` to a DLC.

Runs only when `SCANBORN_EXPORT=qairt` selects it. The output is recorded beside the deployable
npz-int8 bundle and is never deployable: nothing in this repository executes a DLC, and the
file's format is reported from what was actually written. Untested against the real converter —
it is not installed here; the tests use stand-ins for it.
"""
import os
import shutil
import subprocess

from deployment import artifact_format
from deployment.aihub_export.export_script import NOT_DEPLOYABLE
from policy.finetune.train_bc import LinearPolicy


def _write_onnx(policy: LinearPolicy, out_dir: str) -> str:
    """The linear policy as a one-layer ONNX model — an input qairt-converter accepts."""
    import torch

    os.makedirs(out_dir, exist_ok=True)
    module = torch.nn.Linear(policy.obs_dim, policy.act_dim)
    with torch.no_grad():
        module.weight.copy_(torch.tensor(policy.W, dtype=torch.float32))
        module.bias.copy_(torch.tensor(policy.b, dtype=torch.float32))
    path = os.path.join(out_dir, "policy.onnx")
    torch.onnx.export(module, (torch.zeros(1, policy.obs_dim),), path,
                      input_names=["obs"], output_names=["action"])
    return path


def convert_to_qairt(policy_path: str, out_dir: str | None = None) -> dict:
    """Convert the policy and return an experiment record; failures are recorded, not raised."""
    out_dir = out_dir or os.path.join(os.path.dirname(policy_path) or ".", "qairt")
    record: dict = {"provider": "qairt", "status": "unavailable", "deployable": False,
                    "reason": NOT_DEPLOYABLE, "declared_format": "dlc", "artifact_path": None,
                    "format": None, "source_model": None, "error": None}
    converter = shutil.which("qairt-converter")
    if converter is None:
        record["error"] = "qairt-converter is not on PATH"
        return record
    try:
        record["source_model"] = _write_onnx(LinearPolicy.load(policy_path), out_dir)
    except ImportError:
        record["error"] = ("torch and onnx are needed to write the ONNX model: "
                           "pip install -r requirements-npu.txt")
        return record
    except Exception as exc:  # noqa: BLE001 - the exporter raises many types; each is recorded
        record.update(status="failed",
                      error=f"ONNX export failed: {type(exc).__name__}: {exc}"[:500])
        return record

    dlc_path = os.path.join(out_dir, "policy.dlc")
    try:
        subprocess.run([converter, "--input_network", record["source_model"],
                        "--output_path", dlc_path],
                       check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-400:]
        record.update(status="failed", error=f"qairt-converter exited {exc.returncode}: {detail}")
        return record
    except (subprocess.TimeoutExpired, OSError) as exc:
        record.update(status="failed", error=f"qairt-converter did not complete: {exc}"[:500])
        return record
    if not os.path.isfile(dlc_path):
        record.update(status="failed", error="qairt-converter reported success but wrote no DLC")
        return record
    # A DLC has no signature this module can verify, so an honest read is usually "unknown".
    record.update(status="converted", artifact_path=dlc_path,
                  format=artifact_format.detect(dlc_path))
    return record
