"""Endpoints the dashboard needs beyond the pipeline stages."""
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, service
from orchestrator.service import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scanborn.db"))
    monkeypatch.delenv("SCANBORN_PLANNER", raising=False)
    return TestClient(app)


def test_plan_preview_needs_no_twin_and_stores_nothing(client, tmp_path):
    response = client.post("/plan/preview", json={"text": "go to the table", "lang": "en"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "local"
    assert body["on_device"] is True
    nodes = json.loads(body["graph_json"])["nodes"]
    assert nodes[0]["action"] == "navigate_to"
    assert nodes[0]["target"] == "table"
    # A preview has no twin to belong to, so it must not create the database at all.
    assert not (tmp_path / "scanborn.db").exists()


def test_plan_preview_prefers_the_labels_it_is_given(client):
    body = client.post("/plan/preview", json={
        "text": "go to the pallet", "labels": ["pallet"]}).json()
    assert json.loads(body["graph_json"])["nodes"][0]["target"] == "pallet"


@pytest.mark.parametrize("payload", [{"text": "   "}, {}, {"text": "go", "labels": "table"}])
def test_plan_preview_rejects_bad_input(client, payload):
    assert client.post("/plan/preview", json=payload).status_code == 400


def test_benchmarks_includes_phone_measurements(client, tmp_path, monkeypatch):
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "phone.json").write_text(json.dumps({"runs": [{"prefill_tok_per_s": 69.0}]}))
    monkeypatch.setattr(service, "BENCHMARK_DIR", str(bench))

    body = client.get("/benchmarks").json()
    # No AI Hub profile on this hardware: empty means "not profiled", never zero.
    assert body["profiled"] is False
    assert body["models"] == []
    assert body["phone"]["runs"][0]["prefill_tok_per_s"] == 69.0


def test_benchmarks_with_nothing_measured(client, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "BENCHMARK_DIR", str(tmp_path / "missing"))
    assert client.get("/benchmarks").json() == {"models": [], "profiled": False, "phone": None}
