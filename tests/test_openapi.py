"""docs/api/openapi.yaml must describe the orchestrator that actually exists.

Paths and methods are compared rather than the whole document, so upgrading FastAPI (which
reorders and restyles the generated schema) does not fail this test, while adding or removing
a route without re-running scripts/export_openapi.py does.
"""
import os

import yaml

from orchestrator.service import app

SPEC = os.path.join(os.path.dirname(__file__), "..", "docs", "api", "openapi.yaml")


def _committed() -> dict:
    with open(SPEC, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _operations(spec: dict) -> set:
    return {(path, method) for path, item in spec["paths"].items() for method in item}


def test_the_committed_document_lists_every_route():
    assert _operations(_committed()) == _operations(app.openapi()), \
        "routes changed: run python scripts/export_openapi.py"


def test_the_mission_request_is_documented():
    schema = _committed()["components"]["schemas"]["MissionRequest"]
    assert {"twin_id", "goal", "start", "via", "task_graph_id"} <= set(schema["properties"])
