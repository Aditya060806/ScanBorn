"""The frozen intent set: unchanged, scored honestly, and safe for the local planner."""
import json
import os

import pytest

from sarvam.task_engine import intent_eval

BENCHMARK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "benchmarks", "intents-local-v1.json")


def test_the_v1_set_is_frozen():
    scene, cases = intent_eval.load("v1")
    assert len(cases) == 38 and scene.count("chair") == 2
    assert len({case["id"] for case in cases}) == len(cases)


def test_an_edited_set_refuses_to_run(monkeypatch):
    monkeypatch.setitem(intent_eval.FROZEN, "v1", "0" * 64)
    with pytest.raises(ValueError, match="add a new version"):
        intent_eval.load("v1")


@pytest.mark.parametrize("expected,status,steps,outcome", [
    ({"status": "planned", "steps": [["navigate_to", "table"]]}, "planned",
     [["navigate_to", "table"]], "success"),
    ({"status": "planned", "steps": [["navigate_to", "table"]]}, "planned",
     [["navigate_to", "door"]], "wrong_plan"),
    ({"status": "planned", "steps": [["navigate_to", "table"]]}, "unsupported", None,
     "abstained"),
    ({"status": "clarification_needed"}, "planned", [["navigate_to", "table"]], "unsafe_plan"),
    ({"status": "clarification_needed"}, "clarification_needed", None, "correct_refusal"),
    ({"status": "clarification_needed"}, "unsupported", None, "refused_differently"),
    ({"status": "unsupported"}, "failed", None, "provider_failure"),
])
def test_each_outcome_is_scored_exactly_once(expected, status, steps, outcome):
    assert intent_eval.score(expected, status, steps) == outcome


def test_the_local_planner_never_guesses_and_the_benchmark_is_reproducible():
    report = intent_eval.evaluate("local", "v1")
    assert report["safety_invariant_holds"] is True
    assert report["totals"]["wrong_plan"] == 0 and report["totals"]["unsafe_plan"] == 0
    with open(BENCHMARK, encoding="utf-8") as stream:
        committed = json.load(stream)
    # Regenerate with `python scripts/evaluate_intents.py` whenever the local planner changes.
    for key in ("set_sha256", "totals", "categories", "cases"):
        assert report[key] == committed[key], key
