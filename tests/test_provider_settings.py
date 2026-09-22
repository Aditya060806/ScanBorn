"""Operator choices persist without calling external providers or exposing credentials."""
import json

import pytest

from orchestrator import providers
from tests.test_providers import client  # noqa: F401


def local():
    return {"workloads": {name: ["local"] for name in providers.WORKLOADS}, "remote_consent": {}}


def test_explicit_selection_persists_and_overrides_environment(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GROQ_API_KEY", "secret-never-returned")
    body = local()
    body["workloads"]["planner"] = ["groq", "local"]
    assert client.post("/providers/settings", json=body).status_code == 400
    assert not providers._settings_path().exists()
    body["remote_consent"] = {"planner": providers.DATA_SENT["planner"]}
    response = client.post("/providers/settings", json=body)
    assert response.status_code == 200, response.text
    assert "secret-never-returned" not in response.text
    assert "secret-never-returned" not in providers._settings_path().read_text()
    monkeypatch.setenv("SCANBORN_PLANNER", "sarvam")
    assert providers.chain("planner") == ["groq", "local"]
    assert client.get("/providers").json()["configuration_source"] == "saved_settings"
    assert client.post("/providers/settings", json=local()).status_code == 200
    assert providers.chain("planner") == ["local"]


@pytest.mark.parametrize("chain", [["unknown"], ["local", "local"], [], "local", [3]])
def test_invalid_change_preserves_previous_settings(client, chain):  # noqa: F811
    assert client.post("/providers/settings", json=local()).status_code == 200
    before = providers._settings_path().read_bytes()
    bad = local()
    bad["workloads"]["planner"] = chain
    assert client.post("/providers/settings", json=bad).status_code == 400
    assert providers._settings_path().read_bytes() == before


def test_missing_credential_and_corrupt_settings_fail_closed(client):  # noqa: F811
    body = local()
    body["workloads"]["planner"] = ["groq", "local"]
    body["remote_consent"] = {"planner": providers.DATA_SENT["planner"]}
    assert client.post("/providers/settings", json=body).status_code == 503
    path = providers._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 99}))
    assert client.get("/providers").status_code == 503
    # An explicit complete local configuration can repair a damaged saved file.
    assert client.post("/providers/settings", json=local()).status_code == 200


def test_failed_publication_keeps_old_selection(client, monkeypatch):  # noqa: F811
    client.post("/providers/settings", json=local())
    before = providers._settings_path().read_bytes()

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(providers, "atomic_write", fail)
    with pytest.raises(OSError, match="disk full"):
        providers.save_settings(local())
    assert providers._settings_path().read_bytes() == before
