# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Details and reasons for each phase are in
`docs/superpowers/plans/`.

## [Unreleased]

### E0 — recovery, reconciliation and migration safety (2026-09-18)

#### Changed

- Calibration reconciliation always has a way forward, newest session first and from live
  read-back only: a newer applied state supersedes an older uncertain proposal (it used to refuse
  forever while the older one blocked the newer), a session that never reached the rover adopts
  the newest older live proposal, and settled sessions are recorded as `superseded`, never cleared.
  A 409 `calibration_reconciliation` now names every pending session (`pending`).
- A calibration row left `running` by a dead monitor is recorded `interrupted` when the next
  calibration starts, instead of blocking the robot until a restart.
- The "validated for another robot" deploy refusal carries `code: artifact_validation`.

#### Added

- The orchestrator copies a database that holds data to `<db>.v<old version>.bak` before its first
  migration. `docs/api/database.md` lists schema versions and which older code can open a v5 file.
- Recovery tests: an unexpected failure in any stage ends its job and closes its connections; a
  restart interrupts unfinished work and resumes none; populated v1–v4 databases upgrade intact;
  crossed lineage is refused at every stage; two deploys racing for one rover start one drive.

### E1.2 — shared validation, bounded fallback and provenance (2026-09-18)

#### Changed

- One output contract for every planner and profiler (`sarvam/task_engine/contract.py`). A reply is
  judged whole: an invalid step refuses the reply (Groq used to drop it and drive the rest), Sarvam
  targets are now checked against the scene, and parameters must be finite and in range.
- `/plan` stores only plans whose every target names exactly one scanned object. A missing, absent
  or shared target is a 422 `clarification_needed` with the scene's choices; an instruction with no
  supported action is a 422 `unsupported_instruction`. `/plan/preview` answers both with a `status`.
- Missions from a plan use its last `navigate_to` target as the goal and earlier ones as via
  points; a plan with non-driving steps is refused (`unsupported_step`); an absent or shared target
  is refused (`target_not_in_scene`, `ambiguous_target` with candidates). `/train` and `/deploy`
  without a mission no longer aim at the room centre when a plan's target is not in the scan.
- The keyword planner no longer drops a clause silently: a verbless clause naming an object repeats
  the previous action ("go to the table, then the door"), anything else asks for clarification;
  `speak` carries its words in `params.text`.
- `SCANBORN_PLANNER` / `SCANBORN_PROFILER` take ordered chains (`groq,local`). Remote requests have
  a total deadline, per-request timeout, reply-size cap, per-provider concurrency limit, a daily
  request ceiling persisted in SQLite (DB v5) and cancellation. A request that may already have run
  (read timeout, 5xx) is never repeated against the same provider. A remote profiler without a local
  fallback now fails instead of silently using the heuristic.
- `/plan` and `/plan/preview` are idempotent by `request_id`.

#### Added

- Provenance on every plan and inferred profile: requested chain, answering provider and model,
  location, input hash, attempts, fallback reason, reported usage (stored in `task_graphs` and
  `twins`). `POST /providers/requests/{id}/cancel`; SDK `plan(request_id=)` and
  `cancel_provider_request`.
- A frozen intent evaluation set (`datasets/intents/v1`) and `scripts/evaluate_intents.py`. Local
  planner: 16/16 English plans correct, every English ambiguous/off-scene/unsupported instruction
  refused, Hindi abstained, zero wrong or unsafe plans (`benchmarks/intents-local-v1.json`).

### E1.1 — explicit providers and truthful export metadata (2026-09-18)

#### Changed

- Provider selection is validated per workload: `SCANBORN_PLANNER` (`local`/`groq`/`sarvam`),
  `SCANBORN_PROFILER` (`local`/`groq`) and the new `SCANBORN_EXPORT` (`local`/`ai-hub`/`qairt`).
  An unknown name, a missing credential or a missing package is refused with HTTP 503
  (`provider_configuration`) before a job or file exists; it used to fall back to local silently.
- **Breaking for AI Hub users:** `AI_HUB_API_TOKEN` alone no longer uploads anything. Set
  `SCANBORN_EXPORT=ai-hub` to opt in.
- `/optimize` always returns the local `npz-int8` bundle as the artifact, with `format` read from
  its bytes, `runtime` and `export_provider`. AI Hub and QAIRT outputs are `experiments` beside it,
  labelled from their own bytes and never deployable. QAIRT no longer relabels the NPZ bundle
  `qairt`, and converts an ONNX export instead of the NPZ checkpoint it could not read.
- `/deploy` refuses bytes that are not `npz-int8` (`unsupported_runtime`) and always drives the
  int8 bundle; the float-checkpoint fallback is gone.
- `/optimize` checks a stale mission and a missing spec snapshot before exporting, so a refusal
  writes no files.

#### Added

- `GET /providers` and SDK `providers()`: each workload's setting, location and readiness, never
  credential values. The orchestrator logs remote and misconfigured workloads at startup.
- `tests/conftest.py`: every test starts with provider settings cleared and fails on any HTTP
  request to a host outside this machine.

### Phase 7 — honesty pass (2026-09-16)

#### Changed

- README, `CLAUDE.md`, `MOBILE-APP/README.md`, the landing page and the licences list rewritten from
  what the code does and what was measured.
- `twins.unity_scene_url` → `scene_url` in the API and the database (migration v3). No Unity project
  exists; `scene.json` is a generic manifest.
- `FunctionGemmaPlanner` → `KeywordPlanner`: it never loaded a model.
- A local int8 bundle's device label is `host-cpu` (it said "Snapdragon X Elite CRD"); the AI Hub
  path labels its latency `ai-hub-profile` and reports op coverage only when the profile has it.
- `POST /capture` records an unnamed device as `unknown` (it said `onePlus15`).
- The app's About screen reads its version and build type from the installed package and names the
  real engine and model.
- `docker/`: one orchestrator image that copies every package it imports, checked by
  `tests/test_docker.py`.
- `requirements.txt` holds runtime dependencies only.

#### Removed

- The fidelity reconstruction stub (`reconstruction/fidelity_path/`); `mode: "fidelity"` answers 501.
- The Arduino UNO Q adapter: there was no firmware for it and it could not drive.
- The standalone capture and semantic services and their Dockerfiles: nothing called them.
- `k8s/`, `configs/default.yaml`, `examples/*/scenario.yaml`, `docs/papers/references.md`: read by
  nothing or pointing at documents that do not exist.
- `MOBILE-APP/setup_llama.ps1`: it could not reproduce the vendored llama.cpp.

#### Fixed

- `/reconstruct` returned 500 when COLMAP was on PATH (fidelity mode) or for an unknown mode.
- A deploy to a robot row whose adapter no longer exists returns 400 instead of 500.
- The vendored llama.cpp now carries its MIT licence file.

#### Moved

- The hackathon's Snapdragon RC-buggy experiment from `twin/` to `experiments/rc_buggy_snapdragon/`,
  with a README; importing its script no longer opens a serial port.

### Phases 1–5 — scan to drive (2026-09-11 to 2026-09-15)

#### Added

- **Phone (Step 0):** llama.cpp built with dotprod, i8mm and weight repacking, measured on the
  Galaxy S24 FE (`benchmarks/phone.json`).
- **Dashboard (Phase 1):** a real API client, one response mapper per stage, a fixture recorded from
  the real orchestrator, measured telemetry only.
- **Missions (Phase 2):** robust floor estimate; free / blocked / unknown navmesh at 5 or 10 cm;
  A* route planning with clearance and named refusals; database v2 (`missions`, `runs`); stale
  missions after a rescan; the mission map.
- **Rover model (Phase 3):** rover spec, differential-drive simulation with limits and assumed
  noise, pure-pursuit controller, mission gate (27 of 30 noisy runs within 10 cm) and no-mission gate.
- **Rover (Phase 4):** rover protocol v1, fake rover, rover adapter, live deployments over WebSocket
  with E-STOP; ESP32 firmware with a host test and an emulator self-test; live 3D rover in the console.
- **Capture (Phase 5):** fusion keeps surfaces seen by at least two frames; capture metadata and the
  home pose; the axis check; the app's scan screen, orchestrator setting and upload queue (not yet
  run on the phone).

### Hackathon build (July 2026)

#### Added

- SQLite metadata store; chunked, resumable capture uploads; depth-frame fusion with PLY export.
- Geometry-based segmentation (floor slab, voxel clusters, shape labels).
- Twin generator: scene manifest and navmesh.
- Task engine: `TaskGraph` schema, offline keyword planner, optional remote planners.
- Linear behaviour-cloning policy, simulated robot, simulation gate, int8 export.
- Orchestrator REST API with a job registry; Python SDK; operator console.

#### Fixed

- `build-backend` in `pyproject.toml` pointed at a nonexistent module, which made the package
  impossible to install.
- `/ws/status` returned 404 under uvicorn: no websocket library was installed. Added `websockets`.

## [0.1.0] - 2026-07-18

### Added

- Initial project setup: repository structure, linting, formatting, testing and CI configuration,
  security policy.
