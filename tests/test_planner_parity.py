"""The on-device planner must ask the model exactly what the server asks it.

`LocalTaskPlanner` and `LocalProfiler` duplicate three things from Python: the system prompts,
the profile bounds, and the scene-summary wording. That duplication is deliberate — the phone
cannot import Python — but it is also the kind that rots silently.

Prompt drift is the dangerous one. The distillation dataset (plan Phase 4.3) is generated
against the Python wording, so if the phone's prompt diverges then every few-shot example is
answering a slightly different question than the one being asked, and the failure looks like
"the small model is just bad" rather than a text mismatch.

Like test_grammar_parity.py, these read the Kotlin as text: no Gradle, no emulator, runs in
CI with the rest of the suite.
"""
import re
from pathlib import Path

import pytest

from sarvam.task_engine.groq_provider import PROMPT as GROQ_PROMPT
from sarvam.task_engine.profile_planner import PROMPT as PROFILE_PROMPT
from sarvam.task_engine.schemas import PROFILE_BOUNDS
from twin.profile import profile_for

APP = (Path(__file__).resolve().parents[1] / "MOBILE-APP" / "app" / "src" / "main" /
       "java" / "com" / "scanborn" / "ai" / "ai")
PLANNER = APP / "planner"


def read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"Kotlin source not present at {path}")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def task_planner() -> str:
    return read(PLANNER / "LocalTaskPlanner.kt")


@pytest.fixture(scope="module")
def profiler() -> str:
    return read(PLANNER / "LocalProfiler.kt")


@pytest.fixture(scope="module")
def models() -> str:
    return read(PLANNER / "PlanModels.kt")


SLOT = "\x01"  # stands in for an interpolated value on both sides


def _blank_templates(src: str) -> str:
    """Replace every Kotlin `${...}` span with a sentinel, tracking brace depth.

    Necessary because those spans contain their own string literals — `joinToString(", ")`
    has a quoted argument — so extracting literals first would treat that `", "` as prompt
    text and shred the reconstruction.
    """
    out, i = [], 0
    while i < len(src):
        if src.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(src) and depth:
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            out.append(SLOT)
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def kotlin_prompt(source: str) -> str:
    '''Reconstruct the string a Kotlin `fun prompt(...)` returns.

    Concatenates its literals, both plain and raw (triple-quoted), with interpolations
    collapsed to SLOT, so the result can be compared against the rendered Python template.
    '''
    start = source.index("fun prompt(")
    # The body ends at the blank line after the return expression.
    body = source[start:].split("\n\n", 1)[0]
    body = _blank_templates(body)

    pieces = []
    for raw, escaped in re.findall(r'"""(.*?)"""|"((?:[^"\\]|\\.)*)"', body, re.DOTALL):
        text = raw if raw else escaped.encode().decode("unicode_escape")
        pieces.append(text)
    # Drop the leading fragment before the first literal (signature, `=`, etc).
    return "".join(pieces)


def python_prompt(template: str) -> str:
    """The same string, from the Python side: placeholders to SLOT, braces unescaped."""
    return (re.sub(r"\{[a-z_]+\}", SLOT, template)
            .replace("{{", "{").replace("}}", "}"))


# ------------------------------------------------------------------------- prompt parity

def test_task_prompt_matches_the_server_verbatim(task_planner):
    assert kotlin_prompt(task_planner) == python_prompt(GROQ_PROMPT)


def test_profile_prompt_matches_the_server_verbatim(profiler):
    assert kotlin_prompt(profiler) == python_prompt(PROFILE_PROMPT)


def test_both_prompts_still_demand_json_only(task_planner, profiler):
    """The grammar enforces this, but the prompt has to agree with it — a model told to
    explain itself will spend its token budget on prose the grammar then rejects."""
    assert "Reply with JSON only" in task_planner
    assert "Reply with JSON only" in profiler


@pytest.mark.parametrize("key", ["nodes", "action", "target"])
def test_task_prompt_names_the_wire_keys(task_planner, key):
    assert f'"{key}"' in task_planner


@pytest.mark.parametrize("key", ["environment", "robot_radius", "robot_height", "max_speed"])
def test_profile_prompt_names_the_wire_keys(profiler, key):
    assert f'"{key}"' in profiler


# -------------------------------------------------------------------------- bounds parity

def kotlin_range(source: str, name: str) -> tuple:
    match = re.search(rf"val {name}\s*=\s*([\d.]+)\.\.([\d.]+)", source)
    assert match, f"could not find `val {name} = lo..hi` in PlanModels.kt"
    return float(match.group(1)), float(match.group(2))


@pytest.mark.parametrize("kotlin_name,python_key", [
    ("ROBOT_RADIUS", "robot_radius"),
    ("ROBOT_HEIGHT", "robot_height"),
    ("MAX_SPEED", "max_speed"),
])
def test_profile_bounds_match(models, kotlin_name, python_key):
    assert kotlin_range(models, kotlin_name) == PROFILE_BOUNDS[python_key]


def test_kotlin_declares_every_bound_python_checks(models):
    for key in PROFILE_BOUNDS:
        assert key.upper() in models, f"{key} has no bound on the phone"


# ------------------------------------------------------------------- generic profile parity

def test_generic_profile_matches_the_server(models):
    """`generic` is the pre-profile default and the fallback for a failed inference, so the
    phone and the server must agree on what an unclassified room gets."""
    match = re.search(r'EnvironmentProfile\(\s*"generic"\s*,\s*([\d.]+)\s*,\s*'
                      r'([\d.]+)\s*,\s*([\d.]+)\s*\)', models)
    assert match, "could not find the GENERIC EnvironmentProfile in PlanModels.kt"
    radius, height, speed = (float(g) for g in match.groups())

    generic = profile_for("generic")
    assert radius == generic.robot_radius
    assert height == generic.robot_height
    assert speed == generic.max_speed


# ------------------------------------------------------------------------- summary parity

def test_summary_wording_matches(profiler):
    """summarize() feeds the distillation dataset, so its literal text is part of the
    contract, not cosmetic."""
    for fragment in ["Floor area ", " m2, ceiling height ", " m, ",
                     " objects. Detected: ", "no labelled objects"]:
        assert fragment in profiler, f"summary wording drifted: {fragment!r}"


def test_structure_labels_excluded_from_the_object_count_match(profiler):
    """Python excludes floor/ceiling/wall when counting furniture; so must the phone, or the
    same room reads as differently crowded on each side."""
    match = re.search(r"STRUCTURE\s*=\s*setOf\((.*?)\)", profiler, re.DOTALL)
    assert match, "could not find STRUCTURE in LocalProfiler.kt"
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == {"floor", "ceiling", "wall"}


# ---------------------------------------------------------------- behavioural cross-checks

def test_python_summary_shape_is_what_the_kotlin_reproduces():
    """Pin the Python side too. If this line changes, the Kotlin fragments above must
    change with it — and this test is what makes that visible."""
    from sarvam.task_engine.profile_planner import summarize

    text = summarize([
        {"label": "floor", "bbox3d": [0, 0, 0, 10, 5, 0.1]},
        {"label": "wall", "bbox3d": [0, 0, 0, 0.2, 5, 2.5]},
        {"label": "shelf", "bbox3d": [1, 1, 0, 2, 2, 1.9]},
    ])
    assert text == ("Floor area 50.0 m2, ceiling height 2.5 m, 1 objects. "
                    "Detected: 1 floor, 1 shelf, 1 wall.")
    assert "\n" not in text


def test_empty_scene_summary_says_so_rather_than_reporting_zeros():
    from sarvam.task_engine.profile_planner import summarize

    assert "no labelled objects" in summarize([])
