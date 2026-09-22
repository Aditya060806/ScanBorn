"""Export: the deployable int8 bundle, its format read from bytes, and opt-in experiments.

AI Hub and QAIRT are not available here (no token, no Snapdragon target, no converter), so their
paths run against stand-ins. What is under test is ScanBorn's side: nothing runs unless selected,
every failure is recorded with its reason, and no experiment output becomes deployable.
"""
import json
import subprocess
import sys
import types
from unittest import mock

import numpy as np
import pytest

from deployment import artifact_format
from deployment.aihub_export import export_script
from deployment.aihub_export.export_script import ExportConfigurationError, export_model
from deployment.qairt import convert
from deployment.qairt.convert import convert_to_qairt
from policy.finetune.train_bc import LinearPolicy

MANIFEST_FIELDS = {
    "artifact_path", "format", "backend", "runtime", "executors", "device_label", "precision",
    "op_coverage", "est_latency_ms", "latency_source", "obs_dim", "act_dim", "export_provider",
    "experiments",
}
TFLITE_BYTES = b"\x1c\x00\x00\x00TFL3" + bytes(24)


@pytest.fixture
def policy_path(tmp_path):
    rng = np.random.default_rng(0)
    policy = LinearPolicy(rng.uniform(-1, 1, (2, 2)), rng.uniform(-1, 1, 2))
    return policy.save(str(tmp_path / "policy.npz"))


class Tripwire(types.ModuleType):
    """A stand-in module that fails the test if any code so much as touches it."""

    def __getattr__(self, name):
        raise AssertionError(f"{self.__name__}.{name} was used; nothing should reach it")


# ------------------------------------------------------------------------ the bundle

def test_export_writes_a_complete_manifest(policy_path):
    manifest = export_model(policy_path, "bench laptop")
    assert MANIFEST_FIELDS <= set(manifest)
    assert manifest["precision"] == "int8" and manifest["est_latency_ms"] > 0
    assert manifest["format"] == artifact_format.NPZ_INT8
    assert manifest["runtime"] == artifact_format.RUNTIME
    assert manifest["backend"] == "local" and manifest["export_provider"] == "local"
    assert manifest["experiments"] == []
    with open(manifest["manifest_path"]) as f:
        assert json.load(f)["device_label"] == "bench laptop"


def test_a_local_bundle_names_the_host_it_was_timed_on(policy_path):
    # It used to say "Snapdragon X Elite CRD" for a numpy bundle timed on the laptop.
    manifest = export_model(policy_path)
    assert manifest["device_label"] == "host-cpu"
    assert manifest["op_coverage"] is None and manifest["latency_source"] == "host-cpu"


def test_exported_weights_reload_and_dequantize(policy_path):
    manifest = export_model(policy_path)
    original = LinearPolicy.load(policy_path)
    with np.load(manifest["artifact_path"]) as npz:
        restored_W = npz["W"].astype(np.float64) * npz["W_scale"]
        restored_b = npz["b"].astype(np.float64) * npz["b_scale"]
    assert np.abs(restored_W - original.W).max() < 0.05
    assert np.abs(restored_b - original.b).max() < 0.05


def test_the_manifest_says_what_the_policy_reads_and_commands(policy_path, tmp_path):
    from policy.controller import ACTION, OBSERVATION, policy_manifest
    from robot.spec import load_spec

    spec = load_spec("rover")
    manifest = export_model(policy_path, out_dir=str(tmp_path / "bundle"),
                            meta=policy_manifest(spec))
    with open(manifest["manifest_path"]) as f:
        written = json.load(f)
    assert written["observation"] == list(OBSERVATION)
    assert written["action"] == list(ACTION)
    assert written["controller"]["lookahead_m"] > 0
    assert written["robot_spec"] == {"name": spec.name, "source": "placeholder"}
    # Without the description, the manifest has no observation: the orchestrator refuses it.
    assert "observation" not in export_model(policy_path, out_dir=str(tmp_path / "old"))


# ------------------------------------------------------------------------ selection

def test_a_stray_ai_hub_token_makes_no_call(policy_path, monkeypatch):
    """The C0 regression: a token plus an installed package used to upload the policy."""
    monkeypatch.setenv("AI_HUB_API_TOKEN", "stray-token")
    monkeypatch.setitem(sys.modules, "qai_hub", Tripwire("qai_hub"))
    monkeypatch.setitem(sys.modules, "torch", Tripwire("torch"))
    manifest = export_model(policy_path)
    assert manifest["backend"] == "local" and manifest["experiments"] == []


@pytest.mark.parametrize("provider,setup,match", [
    ("aihub", {}, "unknown export provider"),
    ("ai-hub", {}, "AI_HUB_API_TOKEN"),
    ("ai-hub", {"token": True, "missing": ("qai_hub",)}, "qai_hub"),
    ("qairt", {"converter": False}, "qairt-converter"),
    ("qairt", {"converter": True, "missing": ("onnx",)}, "onnx"),
])
def test_a_selected_exporter_that_cannot_run_is_refused_before_any_file(
        policy_path, tmp_path, monkeypatch, provider, setup, match):
    if setup.get("token"):
        monkeypatch.setenv("AI_HUB_API_TOKEN", "test-token")
    missing = setup.get("missing", ())
    monkeypatch.setattr(export_script, "_installed", lambda name: name not in missing)
    monkeypatch.setattr(export_script.shutil, "which",
                        lambda name: "/opt/qairt/bin/qairt-converter" if setup.get("converter")
                        else None)
    out = tmp_path / "never-written"
    with pytest.raises(ExportConfigurationError, match=match):
        export_model(policy_path, out_dir=str(out), provider=provider)
    assert not out.exists()


# ------------------------------------------------------------------------ AI Hub experiment

@pytest.fixture
def ai_hub(monkeypatch):
    """A selected, configured AI Hub whose compile writes TFLite bytes and profiles 2.5 ms."""
    hub = mock.MagicMock()

    def download(path):
        with open(path, "wb") as f:
            f.write(hub.returned_bytes)

    hub.returned_bytes = TFLITE_BYTES
    hub.submit_compile_job.return_value.get_target_model.return_value.download.side_effect = \
        download
    hub.submit_profile_job.return_value.download_profile.return_value = {
        "execution_summary": {"estimated_inference_time": 2500}}
    monkeypatch.setitem(sys.modules, "qai_hub", hub)
    monkeypatch.setitem(sys.modules, "torch", mock.MagicMock())
    monkeypatch.setenv("AI_HUB_API_TOKEN", "test-token")
    return hub


def test_ai_hub_is_an_experiment_beside_the_deployable_bundle(policy_path, tmp_path, ai_hub):
    manifest = export_model(policy_path, out_dir=str(tmp_path / "hub"), provider="ai-hub")
    # The artifact is still the local, deployable int8 bundle with host timing.
    assert manifest["format"] == artifact_format.NPZ_INT8 and manifest["backend"] == "local"
    assert manifest["latency_source"] == "host-cpu" and manifest["device_label"] == "host-cpu"
    assert manifest["export_provider"] == "ai-hub"
    [experiment] = manifest["experiments"]
    assert experiment["status"] == "profiled" and experiment["deployable"] is False
    assert experiment["format"] == artifact_format.TFLITE
    assert experiment["device_label"] == "Snapdragon X Elite CRD"
    assert experiment["latency_source"] == "ai-hub-profile"
    assert experiment["est_latency_ms"] == 2.5
    # No NPU ratio in the profile means coverage was not reported, not that it was 100 %.
    assert experiment["op_coverage"] is None

    ai_hub.submit_profile_job.return_value.download_profile.return_value = {
        "execution_summary": {"estimated_inference_time": 2500,
                              "compute_unit_ratio": {"NPU": 0.75}}}
    hub2 = export_model(policy_path, out_dir=str(tmp_path / "hub2"), provider="ai-hub")
    assert hub2["experiments"][0]["op_coverage"] == 75.0


@pytest.mark.parametrize("profile", [
    {}, {"execution_summary": {}}, {"execution_summary": {"estimated_inference_time": "fast"}},
    {"execution_summary": {"estimated_inference_time": float("nan")}}, ["not", "a", "dict"],
])
def test_a_malformed_ai_hub_profile_is_recorded_not_invented(policy_path, tmp_path, ai_hub,
                                                             profile):
    ai_hub.submit_profile_job.return_value.download_profile.return_value = profile
    [experiment] = export_model(policy_path, out_dir=str(tmp_path / "hub"),
                                provider="ai-hub")["experiments"]
    assert experiment["status"] == "profile_malformed"
    assert experiment["est_latency_ms"] is None and experiment["latency_source"] is None


@pytest.mark.parametrize("returned,found", [(b"garbage", "unknown"), (b"", "unknown")])
def test_ai_hub_bytes_that_are_not_tflite_are_reported_as_what_they_are(
        policy_path, tmp_path, ai_hub, returned, found):
    ai_hub.returned_bytes = returned
    [experiment] = export_model(policy_path, out_dir=str(tmp_path / "hub"),
                                provider="ai-hub")["experiments"]
    assert experiment["status"] == "failed" and experiment["format"] == found
    assert "not the TFLite" in experiment["error"]
    ai_hub.submit_profile_job.assert_not_called()


def test_an_ai_hub_failure_is_recorded_and_the_bundle_survives(policy_path, tmp_path, ai_hub):
    ai_hub.submit_compile_job.side_effect = ConnectionError("AI Hub unreachable")
    manifest = export_model(policy_path, out_dir=str(tmp_path / "hub"), provider="ai-hub")
    [experiment] = manifest["experiments"]
    assert experiment["status"] == "failed" and "AI Hub unreachable" in experiment["error"]
    assert artifact_format.detect(manifest["artifact_path"]) == artifact_format.NPZ_INT8


# ------------------------------------------------------------------------ QAIRT experiment

@pytest.fixture
def converter(monkeypatch):
    """qairt-converter on PATH, and an ONNX writer that needs neither torch nor onnx."""
    monkeypatch.setattr(convert.shutil, "which", lambda name: "/opt/qairt/bin/qairt-converter")

    def write_onnx(policy, out_dir):
        import os
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "policy.onnx")
        with open(path, "wb") as f:
            f.write(b"onnx stand-in")
        return path

    monkeypatch.setattr(convert, "_write_onnx", write_onnx)


def test_qairt_is_unavailable_without_its_converter(policy_path, monkeypatch):
    """PATH alone never promotes a label; without the converter nothing is claimed."""
    monkeypatch.setattr(convert.shutil, "which", lambda name: None)
    record = convert_to_qairt(policy_path)
    assert record["status"] == "unavailable" and record["artifact_path"] is None
    assert record["deployable"] is False


def test_a_converter_failure_is_recorded_with_its_output(policy_path, converter, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, args[0], stderr="unsupported operator Gemm")

    monkeypatch.setattr(convert.subprocess, "run", fail)
    record = convert_to_qairt(policy_path)
    assert record["status"] == "failed"
    assert "exited 2" in record["error"] and "unsupported operator" in record["error"]


def test_a_converter_that_writes_nothing_is_a_failure(policy_path, converter, monkeypatch):
    monkeypatch.setattr(convert.subprocess, "run", lambda *a, **k: None)
    record = convert_to_qairt(policy_path)
    assert record["status"] == "failed" and "wrote no DLC" in record["error"]


def test_a_conversion_is_an_experiment_labelled_from_its_bytes(policy_path, tmp_path, converter,
                                                               monkeypatch):
    def run(command, **kwargs):
        with open(command[command.index("--output_path") + 1], "wb") as f:
            f.write(b"DLC stand-in bytes")

    monkeypatch.setattr(convert.subprocess, "run", run)
    monkeypatch.setattr(export_script, "_installed", lambda name: True)
    monkeypatch.setattr(export_script.shutil, "which", lambda name: "/opt/qairt/converter")

    manifest = export_model(policy_path, out_dir=str(tmp_path / "out"), provider="qairt")
    # The artifact is the int8 bundle; it used to be this bundle relabelled "qairt".
    assert manifest["format"] == artifact_format.NPZ_INT8 and manifest["backend"] == "local"
    [experiment] = manifest["experiments"]
    assert experiment["status"] == "converted" and experiment["deployable"] is False
    assert experiment["declared_format"] == "dlc"
    assert experiment["format"] == artifact_format.UNKNOWN  # no DLC signature can be verified
    assert experiment["source_model"].endswith("policy.onnx")


def test_the_real_onnx_writer_produces_a_model_the_converter_can_read(policy_path, tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("onnx")
    import onnx

    path = convert._write_onnx(LinearPolicy.load(policy_path), str(tmp_path))
    model = onnx.load(path)
    onnx.checker.check_model(model)
    assert [i.name for i in model.graph.input] == ["obs"]


# ------------------------------------------------------------------------ format detection

def test_formats_are_read_from_the_bytes(policy_path, tmp_path):
    bundle = export_model(policy_path)["artifact_path"]
    assert artifact_format.detect(bundle) == artifact_format.NPZ_INT8
    assert artifact_format.detect(policy_path) == artifact_format.NPZ_FLOAT
    other = tmp_path / "other.npz"
    np.savez(other, x=np.zeros(2))
    assert artifact_format.detect(str(other)) == artifact_format.NPZ_OTHER
    tflite = tmp_path / "policy.tflite"
    tflite.write_bytes(TFLITE_BYTES)
    assert artifact_format.detect(str(tflite)) == artifact_format.TFLITE
    junk = tmp_path / "policy_int8.npz"
    junk.write_bytes(b"PK\x03\x04 not really a zip")
    assert artifact_format.detect(str(junk)) == artifact_format.UNKNOWN
    assert artifact_format.detect(str(tmp_path / "missing.npz")) == artifact_format.UNKNOWN


def test_only_the_int8_bundle_runs_on_scanborns_runtime(policy_path, tmp_path):
    assert artifact_format.runtime_refusal(export_model(policy_path)["artifact_path"]) is None
    refusal = artifact_format.runtime_refusal(policy_path) or ""
    assert "npz-float" in refusal and "npz-int8" in refusal
    tflite = tmp_path / "policy.tflite"
    tflite.write_bytes(TFLITE_BYTES)
    assert "tflite" in (artifact_format.runtime_refusal(str(tflite)) or "")
