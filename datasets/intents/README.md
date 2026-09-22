# Intent evaluation sets

Frozen sets of instructions for comparing task planners (execution plan E1.2). Each version is a
`<version>.jsonl` of cases and a `<version>.scene.json` naming the scene's objects, one entry per
object — v1's two chairs make "the chair" ambiguous on purpose.

**A set is frozen before any provider is compared.** Its SHA-256 (line endings normalised) is in
`sarvam/task_engine/intent_eval.py:FROZEN`, and an edited set refuses to run. To change the cases,
add a new version; never re-freeze an old one after seeing results, and never tune a planner on a
set and then report that set as held out.

Each case's `expected` says what the instruction **means**, not what a given provider can do:
Hindi cases expect a plan even though the local keyword planner cannot read Hindi.

| Category | v1 cases | Expected |
|---|---|---|
| supported | 15 | a plan with exactly these steps |
| ambiguous | 6 | `clarification_needed` (missing target, or a label two objects share) |
| off_scene | 4 | `clarification_needed` (the object is not in this scene) |
| partial | 2 | `clarification_needed` (part of the instruction cannot be understood) |
| unsupported | 5 | `unsupported` (no supported action) |
| manipulation | 1 | a plan (the planner parses it; a rover mission then refuses non-driving steps) |
| multilingual | 5 | Hindi: three plans, one clarification, one unsupported |

Run and score one provider (a remote one needs `--allow-remote` and its key):

```bash
python scripts/evaluate_intents.py                 # -> benchmarks/intents-local-v1.json
```

Outcomes, safety invariant and per-category metrics are defined in `intent_eval.py`. The
invariant — no wrong plan, no plan where a refusal was expected — must hold for every provider.
Semantic success and abstention are reported separately; a schema-valid reply is not counted as a
correct one. Remote providers (Groq, Sarvam) have not been scored: no key was used.
