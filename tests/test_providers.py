"""Provider selection through the orchestrator: explicit, checked before work, and truthful.

Every remote provider here is a stand-in; tests/conftest.py fails any request that would leave
this machine. What is under test is that a setting is refused before a job or a file exists,
that keys alone select nothing, and that only the int8 bundle can drive.
"""
import json
import os
import sys
import types
from contextlib import closing
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, jobs
from orchestrator.service import app
from policy.finetune.train_bc import LinearPolicy
from tests.test_missions import GOAL, START, mission, twin_for
from tests.test_orchestrator import upload_scan

TFLITE_BYTES = b"\x1c\x00\x00\x00TFL3" + bytes(24)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    return TestClient(app)


def trained(client) -> tuple:
    _, twin = twin_for(client)
    planned = mission(client, twin["twin_id"], start=[*START, 0.0], goal=GOAL).json()
    policy = client.post("/train", json={"twin_id": twin["twin_id"],
                                         "mission_id": planned["mission_id"]})
    assert policy.status_code == 200, policy.text
    return twin, planned, policy.json()


def count(table: str) -> int:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608


def artifact_folder(policy_id: str) -> str:
    return os.path.join(os.environ["SCANBORN_DATA"], "artifacts", policy_id)


class Tripwire(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError(f"{self.__name__}.{name} was used; a key alone must select nothing")


# ------------------------------------------------------------------------ report

def test_the_report_is_local_by_default_and_never_prints_a_secret(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-value")
    monkeypatch.setenv("AI_HUB_API_TOKEN", "hub-secret-value")
    response = client.get("/providers")
    assert response.status_code == 200
    report = response.json()
    assert report["default"] == "local" and report["keys_alone_select_nothing"] is True
    for workload in ("planner", "profiler", "export"):
        entry = report["workloads"][workload]
        assert entry["selected"] == "local" and entry["location"] == "orchestrator"
        assert entry["ready"] is True and entry["error"] is None
    assert "secret-value" not in response.text


def test_the_report_names_a_bad_setting_and_a_remote_one(client, monkeypatch):
    monkeypatch.setenv("SCANBORN_PLANNER", "grok")
    monkeypatch.setenv("SCANBORN_PROFILER", "groq")
    workloads = client.get("/providers").json()["workloads"]
    assert workloads["planner"]["ready"] is False and "grok" in workloads["planner"]["error"]
    assert workloads["planner"]["location"] is None
    assert workloads["profiler"]["location"] == "remote"
    assert workloads["profiler"]["credential"] == [{"setting": "GROQ_API_KEY",
                                                    "configured": False}]
    assert "needs GROQ_API_KEY" in workloads["profiler"]["error"]


# ------------------------------------------------------------------------ planner / profiler

@pytest.mark.parametrize("setting,needle", [("grok", "not supported"),
                                            ("groq", "needs GROQ_API_KEY"),
                                            ("sarvam", "needs SARVAM_API_KEY")])
def test_the_planner_is_refused_before_any_job(client, monkeypatch, setting, needle):
    _, twin = twin_for(client)
    monkeypatch.setenv("SCANBORN_PLANNER", setting)
    jobs_before, graphs_before = len(jobs.all_jobs()), count("task_graphs")

    response = client.post("/plan", json={"twin_id": twin["twin_id"], "text": "go to the table"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "provider_configuration" and detail["workload"] == "planner"
    assert needle in detail["error"]
    assert len(jobs.all_jobs()) == jobs_before and count("task_graphs") == graphs_before

    preview = client.post("/plan/preview", json={"text": "go to the table"})
    assert preview.status_code == 503 and preview.json()["detail"]["setting"] == "SCANBORN_PLANNER"


def test_a_bad_profiler_blocks_only_twins_that_would_use_it(client, monkeypatch):
    scan_id = upload_scan(client)
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    client.post("/segment", json={"mesh_id": mesh["mesh_id"]})
    monkeypatch.setenv("SCANBORN_PROFILER", "heuristic")
    jobs_before = len(jobs.all_jobs())

    inferred = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"]})
    assert inferred.status_code == 503
    assert inferred.json()["detail"]["workload"] == "profiler"
    assert len(jobs.all_jobs()) == jobs_before
    # A named environment never consults the profiler, so its setting cannot block it.
    named = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"],
                                                "environment": "home"})
    assert named.status_code == 200, named.text


# ------------------------------------------------------------------------ export

def test_a_stray_ai_hub_token_exports_locally_and_touches_nothing(client, monkeypatch):
    _, _, policy = trained(client)
    monkeypatch.setenv("AI_HUB_API_TOKEN", "stray-token")
    monkeypatch.setitem(sys.modules, "qai_hub", Tripwire("qai_hub"))
    response = client.post("/optimize", json={"policy_id": policy["policy_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "local" and body["export_provider"] == "local"
    assert body["format"] == "npz-int8" and body["experiments"] == []
    assert body["runtime"] == "scanborn-int8-linear-v1"


def test_a_selected_exporter_that_cannot_run_is_refused_before_any_file(client, monkeypatch):
    _, _, policy = trained(client)
    monkeypatch.setenv("SCANBORN_EXPORT", "ai-hub")  # and no AI_HUB_API_TOKEN
    jobs_before, artifacts_before = len(jobs.all_jobs()), count("artifacts")

    response = client.post("/optimize", json={"policy_id": policy["policy_id"]})
    assert response.status_code == 503
    assert response.json()["detail"]["workload"] == "export"
    assert "AI_HUB_API_TOKEN" in response.json()["detail"]["error"]
    assert len(jobs.all_jobs()) == jobs_before and count("artifacts") == artifacts_before
    assert not os.path.exists(artifact_folder(policy["policy_id"]))

    monkeypatch.setenv("SCANBORN_EXPORT", "tflite")
    refused = client.post("/optimize", json={"policy_id": policy["policy_id"]})
    assert refused.status_code == 503 and "not supported" in refused.json()["detail"]["error"]


def test_a_refused_export_leaves_no_artifact_folder(client):
    """The snapshot check used to run after the export had already written its bundle."""
    _, _, policy = trained(client)
    with closing(db.connect()) as conn:
        db.init_db(conn)
        checkpoint = db.get(conn, "policies", policy["policy_id"])["finetuned_ckpt_url"]
    os.remove(os.path.join(os.path.dirname(checkpoint), "policy_meta.json"))
    response = client.post("/optimize", json={"policy_id": policy["policy_id"]})
    assert response.status_code == 409
    assert not os.path.exists(artifact_folder(policy["policy_id"]))


def test_an_ai_hub_experiment_rides_beside_a_bundle_that_still_deploys(client, monkeypatch):
    _, _, policy = trained(client)
    hub = mock.MagicMock()

    def download(path):
        with open(path, "wb") as f:
            f.write(TFLITE_BYTES)

    hub.submit_compile_job.return_value.get_target_model.return_value.download.side_effect = \
        download
    hub.submit_profile_job.return_value.download_profile.return_value = {
        "execution_summary": {"estimated_inference_time": 1800}}
    monkeypatch.setitem(sys.modules, "qai_hub", hub)
    monkeypatch.setitem(sys.modules, "torch", mock.MagicMock())
    monkeypatch.setenv("AI_HUB_API_TOKEN", "test-token")
    monkeypatch.setenv("SCANBORN_EXPORT", "ai-hub")

    response = client.post("/optimize", json={"policy_id": policy["policy_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["export_provider"] == "ai-hub" and body["format"] == "npz-int8"
    assert body["latency_source"] == "host-cpu"
    [experiment] = body["experiments"]
    assert experiment["status"] == "profiled" and experiment["deployable"] is False
    assert experiment["format"] == "tflite" and experiment["est_latency_ms"] == 1.8

    deployed = client.post("/deploy", json={"artifact_id": body["artifact_id"], "kind": "sim"})
    assert deployed.status_code == 200, deployed.text


# ------------------------------------------------------------------------ deploy

def test_only_int8_bytes_can_drive(client):
    _, _, policy = trained(client)
    artifact = client.post("/optimize", json={"policy_id": policy["policy_id"]}).json()
    with closing(db.connect()) as conn:
        db.init_db(conn)
        path = db.get(conn, "artifacts", artifact["artifact_id"])["artifact_url"]
    # The float checkpoint in the bundle's place: the old deploy path drove it silently.
    LinearPolicy.load(policy_checkpoint(policy["policy_id"])).save(path)
    response = client.post("/deploy", json={"artifact_id": artifact["artifact_id"], "kind": "sim"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_runtime" and "npz-float" in detail["error"]


def policy_checkpoint(policy_id: str) -> str:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return db.get(conn, "policies", policy_id)["finetuned_ckpt_url"]


# ------------------------------------------------------------------------ E1.2 planner chain

def stub_groq(monkeypatch, *steps):
    """Groq replaced by a script of Reply values and ProviderErrors; returns its call log."""
    from sarvam.task_engine.execution import ProviderError, Reply

    calls = []

    def fake(system, text, timeout, max_bytes):
        calls.append(text)
        step = steps[min(len(calls), len(steps)) - 1]
        if isinstance(step, ProviderError):
            raise step
        return Reply(step, model="groq-stub", usage={"prompt_tokens": 40,
                                                     "completion_tokens": 9})

    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request", fake)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    return calls


def graph_row(twin_id: str) -> dict:
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return dict(conn.execute("SELECT * FROM task_graphs WHERE twin_id = ?",
                                 (twin_id,)).fetchone())


def test_a_stored_plan_carries_its_provenance(client):
    _, twin = twin_for(client)
    response = client.post("/plan", json={"twin_id": twin["twin_id"], "text": "go to the table"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "planned" and body["provider"] == "local"
    assert body["location"] == "orchestrator" and body["provenance"]["model"] == "keyword-grammar"
    stored = json.loads(graph_row(twin["twin_id"])["provenance_json"])
    assert stored["provider"] == "local" and stored["output_schema"] == "task-graph-v1"
    assert stored["input_sha256"] == body["provenance"]["input_sha256"]


@pytest.mark.parametrize("text,code", [("go to the kitchen", "clarification_needed"),
                                       ("go there", "clarification_needed"),
                                       ("dance a little", "unsupported_instruction")])
def test_a_plan_that_needs_clarification_is_refused_and_not_stored(client, text, code):
    _, twin = twin_for(client)
    graphs_before = count("task_graphs")
    response = client.post("/plan", json={"twin_id": twin["twin_id"], "text": text})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["error"]
    assert count("task_graphs") == graphs_before
    if code == "clarification_needed":
        assert "table" in detail["choices"] and detail["unresolved"]


def test_remote_failure_falls_back_to_local_and_says_why(client, monkeypatch):
    from sarvam.task_engine.execution import ProviderError

    _, twin = twin_for(client)
    calls = stub_groq(monkeypatch, ProviderError("timeout", "ReadTimeout"))
    monkeypatch.setenv("SCANBORN_PLANNER", "groq,local")
    response = client.post("/plan", json={"twin_id": twin["twin_id"], "text": "go to the table"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "local" and len(calls) == 1  # a timeout is never retried
    assert body["provenance"]["requested"] == ["groq", "local"]
    assert body["provenance"]["fallback_reason"] == "groq: timeout"
    assert graph_row(twin["twin_id"])["provider"] == "local"


def test_a_remote_plan_reports_where_it_ran(client, monkeypatch):
    _, twin = twin_for(client)
    stub_groq(monkeypatch, json.dumps({"nodes": [{"action": "navigate_to", "target": "table"}]}))
    monkeypatch.setenv("SCANBORN_PLANNER", "groq")
    body = client.post("/plan", json={"twin_id": twin["twin_id"],
                                      "text": "go to the table"}).json()
    assert body["provider"] == "groq" and body["location"] == "remote"
    assert body["on_device"] is False and body["provenance"]["model"] == "groq-stub"
    assert body["provenance"]["usage"] == {"prompt_tokens": 40, "completion_tokens": 9}


def test_an_exhausted_chain_is_a_502_with_every_attempt(client, monkeypatch):
    from sarvam.task_engine.execution import ProviderError

    _, twin = twin_for(client)
    stub_groq(monkeypatch, ProviderError("server_error", "HTTP 500", 500))
    monkeypatch.setenv("SCANBORN_PLANNER", "groq")
    response = client.post("/plan", json={"twin_id": twin["twin_id"], "text": "go to the table"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "provider_exhausted"
    assert [a["outcome"] for a in detail["provenance"]["attempts"]] == ["server_error"]


def test_a_repeated_request_id_returns_the_stored_plan_without_calling_again(client, monkeypatch):
    _, twin = twin_for(client)
    calls = stub_groq(monkeypatch,
                      json.dumps({"nodes": [{"action": "navigate_to", "target": "table"}]}))
    monkeypatch.setenv("SCANBORN_PLANNER", "groq")
    request = {"twin_id": twin["twin_id"], "text": "go to the table", "request_id": "plan-once"}
    first = client.post("/plan", json=request).json()
    second = client.post("/plan", json=request).json()
    assert first == second and len(calls) == 1


def test_the_preview_answers_with_a_status_even_when_it_cannot_plan(client):
    asked = client.post("/plan/preview", json={"text": "go to the chair",
                                               "labels": ["chair", "chair", "table"]}).json()
    assert asked["status"] == "clarification_needed" and asked["problems"]
    unknown = client.post("/plan/preview", json={"text": "dance a little"}).json()
    assert unknown["status"] == "unsupported"
    assert json.loads(unknown["graph_json"]) == {"nodes": [], "edges": []}
    planned = client.post("/plan/preview", json={"text": "say hello then go to the table"})
    nodes = json.loads(planned.json()["graph_json"])["nodes"]
    assert [(n["action"], n["target"], n["params"]) for n in nodes] == [
        ("speak", "", {"text": "hello"}), ("navigate_to", "table", {})]


def test_cancelling_a_running_chain_stops_it_between_attempts(client, monkeypatch):
    from orchestrator import providers as registry
    from sarvam.task_engine.execution import ProviderError

    def cancel_then_fail(system, text, timeout, max_bytes):
        assert registry.cancel("cancel-me")  # the operator cancels while Groq is answering
        raise ProviderError("unreachable", "ConnectError")

    monkeypatch.setattr("sarvam.task_engine.groq_provider.groq_request", cancel_then_fail)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("SCANBORN_PLANNER", "groq,local")
    response = client.post("/plan/preview", json={"text": "go to the table",
                                                  "request_id": "cancel-me"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "cancelled"
    assert client.post("/providers/requests/cancel-me/cancel").status_code == 404


def test_the_daily_budget_is_persistent_and_counted(client, monkeypatch):
    stub_groq(monkeypatch, json.dumps({"nodes": [{"action": "navigate_to", "target": "table"}]}))
    monkeypatch.setenv("SCANBORN_PLANNER", "groq,local")
    monkeypatch.setenv("SCANBORN_REMOTE_DAILY_LIMIT", "1")
    first = client.post("/plan/preview", json={"text": "go to the table"}).json()
    second = client.post("/plan/preview", json={"text": "go to the table"}).json()
    assert first["provider"] == "groq"
    assert second["provider"] == "local"
    assert second["provenance"]["attempts"][0]["outcome"] == "budget_exhausted"
    usage = client.get("/providers").json()["remote_usage_today"]
    assert usage == [{"provider": "groq", "calls": 1, "prompt_tokens": 40,
                      "completion_tokens": 9}]


def test_an_invalid_limit_is_refused_before_work(client, monkeypatch):
    monkeypatch.setenv("SCANBORN_PROVIDER_RETRIES", "9")
    response = client.post("/plan/preview", json={"text": "go to the table"})
    assert response.status_code == 503
    assert "SCANBORN_PROVIDER_RETRIES" in response.json()["detail"]["error"]
    assert client.get("/providers").json()["limits"]["error"]


@pytest.mark.parametrize("setting", ["groq,groq", "local,grok"])
def test_a_malformed_chain_is_refused(client, monkeypatch, setting):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("SCANBORN_PLANNER", setting)
    assert client.post("/plan/preview", json={"text": "go to the table"}).status_code == 503


def test_export_takes_one_provider(client, monkeypatch):
    monkeypatch.setenv("SCANBORN_EXPORT", "local,ai-hub")
    report = client.get("/providers").json()["workloads"]["export"]
    assert report["ready"] is False and "takes one provider" in report["error"]


# ------------------------------------------------------------------------ E1.2 profiler chain

def test_the_profiler_chain_is_recorded_with_the_twin(client, monkeypatch):
    scan_id = upload_scan(client)
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    client.post("/segment", json={"mesh_id": mesh["mesh_id"]})
    stub_groq(monkeypatch, "not json")
    monkeypatch.setenv("SCANBORN_PROFILER", "groq,local")
    response = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile_source"] == "profiler"
    assert body["profile_provenance"]["provider"] == "local"
    assert body["profile_provenance"]["attempts"][0]["outcome"] == "invalid_reply"
    with closing(db.connect()) as conn:
        db.init_db(conn)
        stored = db.get(conn, "twins", body["twin_id"])["profile_provenance_json"]
    assert json.loads(stored)["provider"] == "local"


def test_a_remote_profiler_without_fallback_refuses_rather_than_guessing(client, monkeypatch):
    from sarvam.task_engine.execution import ProviderError

    scan_id = upload_scan(client)
    mesh = client.post("/reconstruct", json={"scan_id": scan_id}).json()
    client.post("/segment", json={"mesh_id": mesh["mesh_id"]})
    stub_groq(monkeypatch, ProviderError("unreachable", "ConnectError"))
    monkeypatch.setenv("SCANBORN_PROFILER", "groq")
    twins_before = count("twins")
    response = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"]})
    assert response.status_code == 502
    assert response.json()["detail"]["workload"] == "profiler"
    assert count("twins") == twins_before
    assert all(job["status"] != "running" for job in jobs.all_jobs())
    named = client.post("/generate-twin", json={"mesh_id": mesh["mesh_id"],
                                                "environment": "home"}).json()
    assert named["profile_source"] == "named" and named["profile_provenance"] is None


# ------------------------------------------------------------------------ plans into missions

def legacy_graph(twin_id: str, nodes: list) -> str:
    """A task graph stored before E1.2's checks existed, straight into the database."""
    with closing(db.connect()) as conn:
        db.init_db(conn)
        return db.insert(conn, "task_graphs", twin_id=twin_id, source_text="legacy",
                         lang="en", provider="local",
                         graph_json=json.dumps({"nodes": nodes, "edges": []}))


def node(action: str, target: str = "") -> dict:
    return {"id": "n0", "action": action, "target": target, "params": {}}


def test_a_plan_with_several_stops_becomes_via_points(client):
    _, twin = twin_for(client)
    planned = client.post("/plan", json={"twin_id": twin["twin_id"],
                                         "text": "go to the chair then go to the table"})
    assert planned.status_code == 200, planned.text
    route = mission(client, twin["twin_id"], start=[*START, 0.0],
                    task_graph_id=planned.json()["task_graph_id"])
    assert route.status_code == 200, route.text
    assert len(route.json()["via"]) == 1  # the chair; the table is the goal


def test_a_plan_the_rover_cannot_fully_perform_is_refused_as_a_mission(client):
    _, twin = twin_for(client)
    planned = client.post("/plan", json={"twin_id": twin["twin_id"],
                                         "text": "go to the table then wait"}).json()
    response = mission(client, twin["twin_id"], start=[*START, 0.0],
                       task_graph_id=planned["task_graph_id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_step"
    assert response.json()["detail"]["actions"] == ["wait"]


def test_a_legacy_plan_is_never_aimed_somewhere_else(client):
    """It used to drive to the middle of the room when the target was not in the scan."""
    _, twin = twin_for(client)
    graph_id = legacy_graph(twin["twin_id"], [node("navigate_to", "kitchen")])
    route = mission(client, twin["twin_id"], start=[*START, 0.0], task_graph_id=graph_id)
    assert route.status_code == 422
    assert route.json()["detail"]["code"] == "target_not_in_scene"
    trained = client.post("/train", json={"twin_id": twin["twin_id"], "task_graph_id": graph_id})
    assert trained.status_code == 422
    assert trained.json()["detail"]["code"] == "target_not_in_scene"


def test_a_label_shared_by_two_objects_asks_which_one(client):
    from tests.test_missions import with_box

    _, twin = twin_for(client, frame=with_box(1.3, 1.7, 1.3, 1.7, 0.45))
    graph_id = legacy_graph(twin["twin_id"], [node("navigate_to", "chair")])
    response = mission(client, twin["twin_id"], start=[*START, 0.0], task_graph_id=graph_id)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "ambiguous_target" and len(detail["candidates"]) == 2
