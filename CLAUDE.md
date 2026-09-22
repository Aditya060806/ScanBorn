# CLAUDE.md

Guidance for Claude Code in this repository.

## Start here

**Read `docs/superpowers/START-HERE.md` first.** It holds what is built and measured, every open
item, how the owner works, the environment traps on this Windows machine, and the next step. The
master plan is `docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md`; each phase has its
own plan in `docs/superpowers/plans/`, written from the code before the phase is built. Where this
file and START-HERE disagree, START-HERE wins.

## The project

A Galaxy S24 FE scans a room → the laptop builds a twin (a free / blocked / unknown grid sized for
the robot) → the operator picks a start and a goal → the rover's controller is trained in a
simulation of the rover and must pass a gate → only a passing int8 policy drives an ESP32
two-wheel rover, which runs the control loop itself. The phone also runs Qwen2.5-1.5B on its CPU
for chat, voice, OCR and PDF summaries.

The July 2026 hackathon version (Snapdragon, Unity ML-Agents, UNO Q, NPU) is history:
`detailed implementation doc.md` describes it and is not the design.

## Rules that are not in the code

- **Honesty.** Only measured numbers, each with how it was measured; fixtures labelled; unmeasured
  things say so. No NPU, Snapdragon, Unity or RL claims — the phone is Exynos, everything runs on
  CPUs.
- **Public repository.** Commit or push only when the owner asks. Never print or stage `.env`
  values.
- **The phone is the owner's daily device.** Check `dumpsys window | grep mCurrentFocus` before any
  `adb` input; never drive another app; ask the owner to do on-phone steps.
- Ask before downloads.

## Layout

| Directory | What |
|---|---|
| `orchestrator/` | FastAPI app (`service.py`, CRLF — patch it with a script that keeps CRLF), SQLite store with migrations (`db.py`), jobs, rover deployment sessions (`deployments.py`) |
| `capture/` | scan storage (`service/store.py`), Scaniverse import. `capture/android/` is the old capture app, deleted once the Phase 5 phone session passes |
| `reconstruction/` | depth-frame fusion (`fast_path/`), floor estimate |
| `semantic/service/inference.py` | geometric segmentation |
| `twin/` | navmesh grid, environment profiles, scene manifest |
| `policy/` | route planning, diff-drive simulation, controller, behaviour cloning, evaluation gates |
| `robot/` | rover spec (`specs/rover.yaml`), rover protocol, adapters (`sim`, `rover`), fake rover |
| `sarvam/task_engine/` | text → task graph: offline `KeywordPlanner`, optional Groq / Sarvam planners, environment profiler |
| `deployment/` | int8 export; optional Qualcomm AI Hub path |
| `sdk/scanborn/` | Python client for every route |
| `dashboard/` | React + Vite landing page and operator console |
| `MOBILE-APP/` | Android app (Kotlin, Compose, ARCore, vendored llama.cpp) |
| `firmware/scanborn_rover/` | ESP32 rover firmware |
| `scripts/` | fixture recording, OpenAPI export, benchmarks, browser and scan checks |
| `docs/` | OpenAPI, rover protocol, capture marker, hardware notes, plans |
| `experiments/` | the hackathon's Snapdragon RC-buggy experiment, not part of the pipeline |

## Commands

```bash
python -m pip install -e . -r requirements-dev.txt
python -m uvicorn orchestrator.service:app --port 8000        # --host 0.0.0.0 for the phone or a rover
cd dashboard; npm install; npm run dev                         # :5173, console at /dashboard

python -m pytest -q -o addopts=""                              # full suite takes minutes
python -m pytest tests/test_missions.py::test_name -q -o addopts=""
python -m flake8 sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests scripts noxfile.py
python -m mypy sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests
cd dashboard; npm test; npm run build
```

After changing a route: `python scripts/export_openapi.py`. After changing a response shape:
`python scripts/record_dashboard_fixture.py`, then `npm test`. After changing the controller, policy
format or rover spec: `python scripts/gen_rover_golden.py`. Firmware, emulator, Android and browser
checks, with the environment variables they need, are in START-HERE §8 and §10. There is no `make`
on this machine; `noxfile.py` has the same tasks.

## Environment variables

All optional (`.env.example`): `SCANBORN_DATA`, `SCANBORN_DB`, `SCANBORN_TELEMETRY_PORT` (rover
telemetry, UDP 5005), `ORCHESTRATOR_URL` (the dashboard dev server's API proxy). Provider selection
is explicit and local by default — `SCANBORN_PLANNER` (chain of `local`/`groq`/`sarvam`, e.g.
`groq,local`), `SCANBORN_PROFILER` (chain of `local`/`groq`), `SCANBORN_EXPORT` (one of
`local`/`ai-hub`/`qairt`), plus `SCANBORN_PROVIDER_*` limits and `SCANBORN_REMOTE_DAILY_LIMIT`. A key
(`GROQ_API_KEY`, `SARVAM_API_KEY`, `AI_HUB_API_TOKEN`) never selects a provider on its own, and an
unknown name is refused before work. `orchestrator/providers.py` owns selection and limits;
`sarvam/task_engine/contract.py` is the one output contract; `execution.py` runs chains;
`intent_eval.py` + `datasets/intents/` score planners (`python scripts/evaluate_intents.py`).
