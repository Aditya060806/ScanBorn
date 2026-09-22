# Phase 7 — Honesty pass (implementation plan)

Detailed plan for Phase 7 of `2026-09-13-scanborn-scan-to-drive-plan.md` (§7), written 2026-09-16
after reading every file it changes. Needs no hardware: the phone session (Phase 5 step 5.5) and the
rover (Phase 6) are untouched. The master plan's eight items are here, plus the claims the survey
found that the master plan did not list (§1.2).

**Outcome:** nothing in the repository (README, docs, config, API field names, UI copy, app About
screen) describes something that does not exist or was not measured. Anything that was removed is
listed in §2 with the reason, so nothing disappears silently.

---

## 1. What exists (read before changing)

### 1.1 The master plan's items

| Piece | Where | State |
|---|---|---|
| README | `README.md` (1,486 lines) | Describes the July hackathon architecture as done: Unity ML-Agents PPO, `unity/` tree, `mlagents-learn configs/ppo.yaml`, Whisper, Hexagon NPU inference, Arduino UNO Q, a OnePlus/Snapdragon phone, `app/backend/main.py`, `twin_generator/`, `tests/unit`. Benchmark tables list unmeasured ranges for hardware the project does not have ("Snapdragon NPU inference 2–5 W", "Policy inference 5–15 ms, Hexagon NPU"). Roadmap says "Completed: Unity integration, Reinforcement Learning, Qualcomm AI Hub optimization, Edge deployment". Screenshots `assets/1–4.png` (2026-09-13) show the landing page before Phases 2–5 |
| Fidelity path | `reconstruction/fidelity_path/pipeline.py`, `reconstruction/reconstruct.py:190` | Without COLMAP: `RuntimeError` → `/reconstruct` 400. With COLMAP on PATH: returns `{"status": "colmap_dense", ...}`, then `service.py:279` reads `result["ply_path"]` → `KeyError` → 500. An unknown mode (`"foo"`) silently runs the fast path, then the `meshes.mode` CHECK raises → 500. The console offers "fidelity — COLMAP, needs the binary" (`Console.jsx:550`) |
| Docker | `docker/docker-compose.yml`, `docker/Dockerfile.*` | Three services. `CAPTURE_URL` / `SEMANTIC_URL` are read by nothing. `Dockerfile.orchestrator` copies only `orchestrator/` but `service.py` imports `capture`, `reconstruction`, `semantic`, `twin`, `policy`, `robot`, `sarvam`, `deployment`; `Dockerfile.semantic` copies only `semantic/service/` but imports `reconstruction`. The standalone apps (`capture/service/app.py`, `semantic/service/app.py`) are used by neither the phone app nor the dashboard — the orchestrator serves `/capture` and `/segment` itself. `requirements.txt` includes black, sphinx, safety and other dev tools, so every image would install them. Docker 29.1.3 is installed; its daemon is not running |
| k8s | `k8s/README.md` | "Future deployment manifests"; there are none |
| Config | `configs/default.yaml` | Loaded by nothing. Contradicts the code: voxel 0.005 (code 0.02), `fbx` export (code writes JSON), `yoloworld+mobilesam` (code segments geometry first) |
| CLAUDE.md | `CLAUDE.md` | Describes the hackathon scaffold ("thin wrappers", "no HTTP calls are actually wired up", Unity prefabs, UNO Q, FunctionGemma, QAIRT). Its "known inconsistencies" are all fixed: `setup.cfg` is flake8-only, CI runs `make` targets, `sdk/scanborn/__main__.py` exists, `__init__.py` exports `ScanBorn` |
| App README | `MOBILE-APP/README.md` | "5 screens", "Voice assistant placeholder", "Architecture supports offline LLM integration" — the app has chat, OCR, PDF summary, voice, scanning and a loaded model |
| Blueprint | `detailed implementation doc.md` | The July Snapdragon blueprint, no banner |

### 1.2 Found by the survey (not in the master plan)

| Claim | Where | Why it is wrong |
|---|---|---|
| Landing page copy predates Phases 2–5 | `dashboard/src/Landing.jsx` | "twenty evaluation episodes", "the 60% simulation gate", "Today that robot is simulated; the ESP32 rover is next", workflow step 01 is Scaniverse only; no missions, no mission gate (30 noisy runs, ≥ 90 % within 10 cm), no fake rover, no app scan |
| Every local policy bundle is labelled "Snapdragon X Elite CRD" | `deployment/aihub_export/export_script.py:15` `DEFAULT_DEVICE`, used by `/optimize` and `deployment/qairt/convert.py` | The bundle is int8 numpy timed on the laptop CPU; the `artifacts.device_label` row says a Snapdragon devkit |
| AI Hub path reports numbers it did not measure | `export_script.py:134` | `op_coverage` defaults to 100 % when the profile has no NPU ratio; the manifest has no `latency_source`, so `/optimize` labels an AI Hub profile `host-cpu` (`service.py:779`) |
| Frame uploads default to a OnePlus 15 | `service.py:169` `POST /capture` `device: str = "onePlus15"`, `docs/api/openapi.yaml:59` | A scan uploaded without a device name is recorded as a OnePlus 15 (the app sends its own; the SDK and scripts do not) |
| The twin is a "Unity scene" | `unity_scene_url` in `/generate-twin`, `twins` table, `pipeline.js:102`, `fixtures.json`; `twin/generator.py` docstrings "for the Unity batch-mode generator"; `CHANGELOG.md` "Unity batch-mode twin generator" | No Unity project or importer exists; `scene.json` is read only by tests |
| The offline planner is "FunctionGemma" | `sarvam/task_engine/fallback.py` `FunctionGemmaPlanner`, `FunctionGemma` shim, unused `model_path="models/function_gemma"` | It is a keyword grammar with no model (its own docstring says so) |
| "Arduino UNO Q over serial (needs the board attached)" | `Console.jsx:27`, `robot/adapters/unoq.py`, `registry.py` | No UNO Q firmware exists in the repo (the only old firmware drives an Ackermann buggy with `steer,throttle`); the adapter cannot drive (`supports_drive = False`); the project's robot is the ESP32 rover (master plan decision 1). Attaching a board would not work |
| Snapdragon buggy experiment inside `twin/` | `twin/buggy_motor_controller.ino`, `twin/inference.py` (opens `/dev/ttyUSB0` and registers QNN at import), `twin/Buggy.onnx` | Master plan §4.6, still open: not the target robot, 14-dimension observation that matches nothing in the pipeline |
| Licences for things the project does not use | `docs/THIRD_PARTY_LICENSES.md` | Unity, ROS2, Nav2, MoveIt2, Stable-Baselines3, LeRobot, COLMAP, gsplat, LiteRT "On-device FunctionGemma inference", Postgres; YOLO-World listed as GPL-3.0 (Ultralytics is AGPL-3.0); llama.cpp, Qwen2.5, three.js, ARCore, ML Kit and the ESP32 Arduino core missing |
| ADRs that do not exist | `docs/architecture/overview.md`, `docs/papers/references.md` | Both point at ADR-001…004; there are none |
| Changelog of things never built | `CHANGELOG.md` [Unreleased] | "Unity batch-mode twin generator", "Android capture companion … with the conversions applied once on device" (never built or run), "UnoQRobot serial bridge"; nothing from Phases 1–5 |
| Product context for a hackathon | `PRODUCT.md`, `dashboard/PRODUCT.md`, `DESIGN.md:115`, `dashboard/DESIGN.md:181` | "a policy that runs fully offline on Snapdragon silicon", "Snapdragon Multiverse judges", "NPU-resident workloads" |
| App About screen | `SettingsScreen.kt:180–198` | Version "1.0.0" (the build says `versionName = "1.0"`), Build "Production Foundation", Engine "ScanBorn-X1" — none of these exist |
| Model README | `MOBILE-APP/app/src/main/assets/models/README.md` | "add your exact model link here", size 1.04 GB, "Engine: ScanBorn-X1" (master plan §5 item) |
| `setup_llama.ps1` | `MOBILE-APP/setup_llama.ps1` | Pins tag `b4570` and copies root-level files; the vendored tree is newer and laid out in `src/` + `ggml/` — it cannot reproduce what is vendored (master plan §5 item) |
| Engine comment | `LlamaEngine.kt:39–41` | "Both target devices … a Snapdragon 8 Elite Gen 5" — the S24 FE is the only target |
| Unrelated wording | `policy/finetune/train_bc.py:6` "what the Hexagon export path needs"; `sarvam/task_engine/profile_planner.py:102` "ponytail: … the four example scenarios" | Stale reference and a stray word; the scenarios are `examples/*/scenario.yaml` |
| Example scenarios | `examples/{warehouse,factory,hospital,museum}/scenario.yaml` | Loaded by nothing; describe a 50×30 m warehouse with forklifts and a `warehouse_route_a` that does not exist |
| Contributor docs | `CONTRIBUTING.md` clones `adt-kmr/ScanBorn`; `SECURITY.md` says "email the project team" with no address; `.gitignore` ignores `twin/unity_project/` | Wrong repository; no way to report; a directory that never existed |

Already honest and kept as is: `.env.example` (AI Hub "requires Snapdragon hardware … no Exynos
equivalent"), `requirements-npu.txt` and `deployment/aihub_export/profile_models.py` (a cloud
profiling tool, labelled), the dashboard's Telemetry and optimize copy, the local bundle's
`backend: "local"` and `latency_source: "host-cpu"`, the app's "fully offline" chat copy (llama.cpp on
the phone) and "ML Kit — works offline" (`com.google.mlkit:text-recognition` is the bundled model).

---

## 2. Decisions

1. **README: rewrite from what runs.** One page: what ScanBorn is today, the loop, what is built and
   what is not (phone scan not yet run, rover not built, no NPU anywhere), measured numbers only with
   their source file (`benchmarks/phone.json`, `benchmarks/planning.json`, the Phase 3 route-length
   runs), how to run it (orchestrator, dashboard, SDK, app, firmware checks), the repository layout
   as it is, team, contact, licence, citation. The hackathon presentation and video links stay,
   labelled "July 2026 hackathon version — earlier architecture". The citation title drops
   "Reinforcement Learning". People's role descriptions in the team table are the owner's and stay.
2. **README screenshots: retake them.** A script (`scripts/capture_readme_screenshots.py`) drives
   headless Chrome over DevTools, the same way `check_live_drive.py` does, against a local
   orchestrator seeded with `seed_synthetic_scans.py`, and writes `assets/1–4.png`. Captions say the
   data is a synthetic scan.
3. **Fidelity mode: delete the stub, answer 501.** `reconstruction/fidelity_path/` is removed;
   `reconstruct()` raises `NotImplementedError` for `fidelity` (with or without COLMAP) and
   `ValueError` for any other unknown mode; `/reconstruct` maps them to 501 and 400. The console
   drops the option. A module whose only job is to fail is itself a loose end; the 501 carries the
   message. The `meshes.mode` CHECK keeps `'fidelity'` (removing a value needs a table rebuild and
   buys nothing).
4. **Docker: one orchestrator image that works.** The compose file runs the orchestrator only, with
   a named volume for `SCANBORN_DATA` and `SCANBORN_DB`. The Dockerfile copies every first-party
   package the orchestrator imports, plus `benchmarks/`; a `.dockerignore` keeps `MOBILE-APP/` (1 GB
   model) and `node_modules` out of the context. A test imports `orchestrator.service` and asserts
   every first-party top-level package it loads is copied, so the image cannot silently fall behind
   again. `Dockerfile.capture`, `Dockerfile.semantic` and the two standalone apps they served are
   deleted (nothing uses them; the orchestrator serves both routes). `tests/test_capture.py` moves to
   the store it was really testing. **Building the image needs the Docker daemon and downloads
   (`python:3.11-slim`, the wheels): ask the owner first; until then it is marked unverified.**
5. **`requirements.txt` holds runtime only.** Dev, test, docs and security tools move to
   `requirements-dev.txt` (which already includes `-r requirements.txt`, so `make install-dev` and CI
   are unchanged).
6. **k8s: delete the directory.** One laptop, SQLite, a phone and a rover on the same LAN — cluster
   manifests would be fiction.
7. **`configs/default.yaml` and `examples/`: delete.** Loaded by nothing; wiring them would make the
   code read values nobody has tuned.
8. **`unity_scene_url` → `scene_url`.** API response, `twins` column (DB v3: `ALTER TABLE twins RENAME
   COLUMN`), dashboard mapper, fixture (re-recorded), tests. `scene.json` keeps its content; the
   generator docstring says it is an engine-neutral manifest (boxes, collider kinds, and a Y-up
   left-handed transform named `unity_from_world` because that is exactly what it is) with no
   importer in this repository.
9. **Keyword planner named for what it is.** `FunctionGemmaPlanner` → `KeywordPlanner`; the
   `FunctionGemma` shim and the unused `model_path` are deleted. The `plans.provider` CHECK keeps
   `'function_gemma'` for old rows (already documented in `db.py`).
10. **UNO Q adapter: remove.** `robot/adapters/unoq.py`, its registry entry, the console option,
    its tests and the `pyserial` comment go. The robot kind CHECKs keep `'unoq'` so an existing
    database still opens; `/deploy` with `kind: "unoq"` becomes the same 400 as any unknown kind.
    Keeping a robot choice that cannot work is the claim; a board that does not exist in the project
    is not worth a firmware port.
11. **Device labels say where the numbers came from.** The local bundle's default `device_label` is
    `host-cpu`; the AI Hub path keeps a named AI Hub device as its own default. The AI Hub manifest
    gains `latency_source: "ai-hub-profile"` and reports `op_coverage` only when the profile has an
    NPU ratio. `POST /capture`'s default device is `unknown`. The AI Hub path stays "untested without
    a token", as its docstring already says.
12. **The buggy experiment moves out (master plan §4.6).** `twin/buggy_motor_controller.ino`,
    `twin/inference.py`, `twin/Buggy.onnx` → `experiments/rc_buggy_snapdragon/` with a README (a
    Snapdragon/QNN experiment from the hackathon; 14-dimension observation; Ackermann steering; why it
    is not the target robot). `inference.py`'s serial port and QNN registration move into `main()` so
    importing it has no side effects. `experiments/` is outside the lint list, like `scripts/` is for
    mypy.
13. **Docs that point at nothing are removed or repointed.** `docs/papers/references.md` is deleted;
    `docs/architecture/overview.md` becomes a short map pointing at START-HERE, the master plan, the
    rover protocol and the OpenAPI file. `THIRD_PARTY_LICENSES.md` lists what the code imports, with
    each licence read from the package's own metadata or licence file where it is on this machine;
    where it is not, the row says "see upstream" instead of guessing.
14. **CHANGELOG: rewrite [Unreleased]** by phase (1–5 and this one), with a "Removed" section for
    everything §2 deletes. `[0.1.0]` stays.
15. **Product and design context: correct the facts, keep the design.** In `PRODUCT.md`,
    `dashboard/PRODUCT.md`, `DESIGN.md`, `dashboard/DESIGN.md` only the lines that state what the
    product runs on or who it is for change; tokens, principles and anti-references stay (Phase 9
    owns design).
16. **App About screen reads the build.** Version from `PackageInfo.versionName`, Build from
    `BuildConfig.BUILD_TYPE`, Engine "llama.cpp (CPU) · Qwen2.5-1.5B-Instruct Q4_K_M". The ∞ mark
    stays for Phase 9's rebrand. Verified by a Gradle build; the screen itself is checked on the
    phone in the Phase 5 session (added to its step list).
17. **Model README and llama.cpp setup (master plan §5).** The model README records the file's
    SHA-256 and size, computed here, and its source only if it can be matched to a published file
    (Hugging Face's API lists size and LFS SHA-256 without downloading); otherwise it says the source
    is unconfirmed. `setup_llama.ps1` is deleted and the app README says llama.cpp is vendored,
    which commit it matches if the tree records one, and how to re-sync `src/`, `ggml/`, `common/`.
18. **`CLAUDE.md`: short and current.** What the project is, where to start (START-HERE), commands
    that work on this Windows machine and in CI, the architecture by directory as it is now, and the
    rules that are not derivable from code (honesty, public repo, phone guard). No duplicate of
    START-HERE's detail.

---

## 3. Steps

### 7.1 Code (tests first where behaviour changes)

- [x] Fidelity: delete `reconstruction/fidelity_path/`; `reconstruct()` mode checks; `/reconstruct`
      501/400; `Console.jsx` option removed; tests: 501 for fidelity with and without a fake
      `colmap` on PATH, 400 for an unknown mode; SDK docstring.
- [x] Device labels: `export_model(device_label=None)` → `host-cpu` locally, AI Hub default for the
      AI Hub path; AI Hub manifest `latency_source` and `op_coverage`; `convert_to_qairt` default;
      `/optimize` stores what the manifest says; `POST /capture` default `unknown`; tests.
- [x] `scene_url`: DB migration v3 with a test that a v2 database upgrades and keeps its rows;
      service, `pipeline.js`, tests; generator docstrings.
- [x] `KeywordPlanner` rename; shim and `model_path` removed; comments in `provider.py`,
      `sarvam_provider.py`, `schemas.py`; tests.
- [x] UNO Q adapter removed; registry, console, `api.test.js`, `tests/test_robot.py`,
      `tests/test_db.py` (the CHECK still accepts `unoq` for old rows — keep that assertion),
      `requirements.txt` comment; `/deploy` unknown-kind test.
- [x] Docker: compose, Dockerfile, `.dockerignore`, delete the two other Dockerfiles and the
      standalone apps; `tests/test_capture.py` → store tests; `tests/test_docker.py` (copied packages
      cover the orchestrator's imports); `docker compose config` parses.
- [x] `requirements.txt` / `requirements-dev.txt` split.
- [x] Buggy experiment moved with its README; side effects into `main()`.
- [x] Wording: `train_bc.py`, `profile_planner.py`, `LlamaEngine.kt` comment.
- [x] Delete `k8s/`, `configs/`, `examples/`, `docs/papers/references.md`; `.gitignore` Unity lines.
- [x] After route changes: `python scripts/export_openapi.py`; after response changes:
      `python scripts/record_dashboard_fixture.py`, then `npm test`.

### 7.2 App

- [x] About screen from the build (decision 16); `gradlew :app:testDebugUnitTest :app:assembleDebug`.
- [x] Model SHA-256 and size; source matched or marked unconfirmed; `setup_llama.ps1` deleted.

### 7.3 Dashboard copy

- [x] `Landing.jsx` rewritten against the current routes and gates (missions, mission gate, fake
      rover, rover protocol, app scan built but not yet run on the phone); browser-checked on the
      dashboard preview with no console errors.

### 7.4 Documents

- [x] `README.md` (decision 1) and retaken screenshots (decision 2).
- [x] `CLAUDE.md`, `MOBILE-APP/README.md`, model README, `docs/architecture/overview.md`,
      `THIRD_PARTY_LICENSES.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, product and design
      context, banner on `detailed implementation doc.md`.

### 7.5 Verification

- [x] A final sweep: `git grep -n -i -E "npu|snapdragon|hexagon|unity|whisper|\bppo\b|uno ?q|functiongemma|oneplus|ml-agents|qairt|ai hub"`
      outside `docs/superpowers/`, the vendored llama.cpp and the blueprint; every remaining hit is
      either labelled tooling (AI Hub profiler, `requirements-npu.txt`, `.env.example`), a CHECK kept
      for old rows, or the README's "not used" statement — listed in §5.
- [x] pytest, flake8, mypy, vitest, `npm run build`, Gradle, `check_live_drive.py` (the console's
      robot list and deploy path changed).
- [x] Master plan Phase 7 ticked with a Verified block; START-HERE, a handoff, memory.

---

## 4. Done when

- Every row in §1 is fixed or has a stated reason to stay.
- The checks in 7.5 pass; the Docker image build is either verified or listed as waiting for the
  owner's yes to the downloads.
- START-HERE and the master plan say Phase 7 is done and what it removed.

---

## 5. Progress and deviations (2026-09-16)

| Check | Result |
|---|---|
| Python | pytest **413 passed** (was 405); flake8 clean; mypy clean (92 files: 6 removed, `test_docker.py` added) |
| New or changed tests | `test_reconstruction.py` (fidelity is `NotImplementedError` with or without COLMAP; unknown mode refused before any work), `test_orchestrator.py` (501 / 400; a local bundle's artifact row says `host-cpu`), `test_deployment.py` (local label; AI Hub manifest with stand-in `qai_hub` and `torch`), `test_db.py` (v3 fresh and upgraded, the old column's value kept), `test_robot.py` (a waypoint-only stand-in replaces the UNO Q), `test_rover_deploy.py` (`unoq` by kind and an old `unoq` row are 400s), `test_capture.py` (now the store), `test_docker.py` (every package the orchestrator reaches is copied; compose runs one service with no unread variables) |
| Dashboard | vitest 44 passed; build passes; fixture re-recorded (`scene_url`); OpenAPI re-exported (default device `unknown`) |
| Browser pane | Landing: nine steps, gate meter at 0.90, the three tiers' status lines, the operator workflow, no stale phrases, no console errors. Console: robot kinds sim / fake rover / ESP32, reconstruct hint instead of a mode selector, the device-label hint |
| `check_live_drive.py` | 13/13 (mission 2.546 m planned in 19 ms; train 30/30; fake rover drive done 2 cm by its estimate, 5 cm in truth; E-STOP by Space 40 ms and by click 37 ms) |
| App | Gradle `:app:testDebugUnitTest :app:assembleDebug` BUILD SUCCESSFUL in 52 s, 0 Kotlin warnings |
| Model | 1,117,320,736 bytes, SHA-256 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`; GGUF header `qwen2.5-1.5b-instruct`, file type 15 (Q4_K_M); identical size and LFS SHA-256 to `Qwen/Qwen2.5-1.5B-Instruct-GGUF/qwen2.5-1.5b-instruct-q4_k_m.gguf` in Hugging Face's API; licence `apache-2.0` on that repository |
| Docker | `docker compose config` parses. **The image was not built:** the daemon is not running and a build downloads `python:3.11-slim` and the wheels, which needs the owner's yes |

| # | Change | Why |
|---|---|---|
| 1 | Decision 2: the README screenshots are crops of `check_live_drive.py`'s own captures (`scripts/readme_screenshots.py`), three instead of four, and the landing-page screenshots are gone | That check already drives the real console in headless Chrome on the synthetic room with the fake rover; a second DevTools script would duplicate it. The mission-map capture is from the check's last drive, stopped by E-STOP, and its caption says so |
| 2 | `Buggy.onnx`'s tensor names are Unity ML-Agents' export format, so the README says the hackathon version used Unity ML-Agents rather than that Unity was never used | Read from the file (`obs_0`, `action_masks`, `deterministic_continuous_actions`, `version_number`, `memory_size_vector`); "no Unity" would have been its own false claim |
| 3 | The vendored llama.cpp had no licence file; MIT's notice is now in `MOBILE-APP/app/src/main/cpp/llama/LICENSE` (text read from upstream) | MIT requires the notice with every copy |
| 4 | `TaskProvider` (sarvam/task_engine/provider.py) removed | "Kept for back-compat with existing callers" — there were none |
| 5 | The hero lede, `recon.js`'s segment caption (door and cabinet are never emitted by the geometric segmenter), the landing nav labels ("Silicon Tiers" → "Hardware") and two unused NPU style rules changed too | Same claims, outside `Landing.jsx` |
| 6 | `.env.example`'s `HF_TOKEN` note says no code reads it | "If pulling gated model weights" — nothing pulls weights, and the model is not gated |
| 7 | GSAP (landing animation) is under GSAP's own no-charge licence, not an open-source one; recorded in the licences list | Found reading `package.json` licences; replacing it is a design decision for Phase 9 |
| 8 | `SECURITY.md` names the contact address already public in the README; its response-time promises were left as the owner wrote them | A report had nowhere to go; the promises are the owner's to change |
| 9 | Existing `data/scanborn.db` rows: 145 scans recorded as device `scaniverse` and 10 as `onePlus15` (the old default) stay as they are | History; the default only affects new uploads |

Left for later, on purpose: the ∞ mark and launcher icons (Phase 9); a model read from the GGUF header
once sideloading exists (master plan §5); the stale `pipeline-nav` numbering scheme and unused `.bar`
styles belong to Phase 9's design pass.
