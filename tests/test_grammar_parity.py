"""The phone and the server must agree on what a valid task graph is.

`MOBILE-APP/.../ai/grammar/Grammars.kt` builds the same GBNF the Python side builds, from
its own copies of the same three lists. Duplicated constants drift — someone adds an action
to VOCABULARY, the server accepts it, the phone's grammar cannot emit it, and the failure
surfaces as a robot that silently refuses one kind of instruction.

These tests read the Kotlin source as text and diff the lists. That is deliberately crude:
it needs no Gradle, no Android SDK and no emulator, so it runs in CI on every commit
alongside the rest of the suite.
"""
import re
from pathlib import Path

import pytest

from sarvam.task_engine.graph import VOCABULARY
from sarvam.task_engine.schemas import (
    ENVIRONMENT_KINDS,
    environment_profile_grammar,
    task_graph_grammar,
)
from semantic.service.inference import LABEL_ONTOLOGY

KOTLIN = (Path(__file__).resolve().parents[1] / "MOBILE-APP" / "app" / "src" / "main" /
          "java" / "com" / "scanborn" / "ai" / "ai" / "grammar" / "Grammars.kt")


@pytest.fixture(scope="module")
def kotlin_source() -> str:
    if not KOTLIN.exists():
        pytest.skip(f"Kotlin grammar source not present at {KOTLIN}")
    return KOTLIN.read_text(encoding="utf-8")


def kotlin_list(source: str, name: str) -> list:
    """Pull the string entries out of `val NAME = listOf( ... )`."""
    match = re.search(rf"val {name}\s*=\s*listOf\((.*?)\)", source, re.DOTALL)
    assert match, f"could not find `val {name} = listOf(...)` in Grammars.kt"
    return re.findall(r'"([^"]+)"', match.group(1))


# ------------------------------------------------------------------------- list parity

def test_vocabulary_matches(kotlin_source):
    assert kotlin_list(kotlin_source, "VOCABULARY") == list(VOCABULARY)


def test_label_ontology_matches(kotlin_source):
    assert kotlin_list(kotlin_source, "LABEL_ONTOLOGY") == list(LABEL_ONTOLOGY)


def test_environment_kinds_matches(kotlin_source):
    assert kotlin_list(kotlin_source, "ENVIRONMENT_KINDS") == list(ENVIRONMENT_KINDS)


# --------------------------------------------------------------------- grammar parity

def alternation_for(grammar: str, rule: str) -> str:
    """The right-hand side of one GBNF rule, whitespace-normalised."""
    for line in grammar.splitlines():
        if line.startswith(rule) and "::=" in line:
            return " ".join(line.split("::=", 1)[1].split())
    raise AssertionError(f"rule {rule!r} not found in grammar")


def build_alternation(values) -> str:
    """Reproduce how both sides render a list of JSON-string alternatives."""
    return " | ".join('"\\"%s\\""' % v for v in values)


def test_action_rule_is_reproducible_from_the_kotlin_list(kotlin_source):
    """If Kotlin's VOCABULARY can rebuild Python's `action` rule, the phone and the
    server will accept exactly the same set of actions."""
    expected = build_alternation(kotlin_list(kotlin_source, "VOCABULARY"))
    assert alternation_for(task_graph_grammar(), "action") == expected


def test_target_rule_is_reproducible_from_the_kotlin_list(kotlin_source):
    labels = kotlin_list(kotlin_source, "LABEL_ONTOLOGY")
    expected = build_alternation(labels + [""])
    assert alternation_for(task_graph_grammar(), "target") == expected


def test_env_rule_is_reproducible_from_the_kotlin_list(kotlin_source):
    expected = build_alternation(kotlin_list(kotlin_source, "ENVIRONMENT_KINDS"))
    assert alternation_for(environment_profile_grammar(), "env") == expected


def test_scene_objects_lead_the_target_rule_on_both_sides(kotlin_source):
    """Ordering is part of the contract: a scene label must outrank an ontology label."""
    grammar = task_graph_grammar(["sousaphone"])
    targets = alternation_for(grammar, "target").split(" | ")
    assert targets[0] == '"\\"sousaphone\\""'
    # And the Kotlin helper documents the same rule it implements.
    assert "Scene labels lead" in kotlin_source


# ------------------------------------------------------------- structural expectations

@pytest.mark.parametrize("rule", ["root", "node", "action", "target", "ws"])
def test_task_graph_grammar_declares_every_rule_it_references(rule):
    assert alternation_for(task_graph_grammar(), rule)


@pytest.mark.parametrize("rule", ["root", "env", "number", "ws"])
def test_profile_grammar_declares_every_rule_it_references(rule):
    assert alternation_for(environment_profile_grammar(), rule)


def test_whitespace_rule_is_identical_on_both_sides(kotlin_source):
    python_ws = alternation_for(task_graph_grammar(), "ws")
    assert python_ws == "[ \\t\\n]*"
    assert 'ws     ::= [ \\\\t\\\\n]*' in kotlin_source


def test_kotlin_never_hand_writes_a_quoted_literal(kotlin_source):
    """Escaping GBNF literals by hand is where this file would go wrong, so the helpers
    are the only thing allowed to build them."""
    body = kotlin_source.split("private fun g(", 1)[1]
    assert 'lit(' in body and 'alternates(' in body
    # The three-backslash form that a hand-written literal would need must not appear
    # outside the two helper definitions.
    after_helpers = body.split("private fun alternates(", 1)[1]
    assert '\\\\\\"' not in after_helpers
