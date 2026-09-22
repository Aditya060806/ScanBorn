# Handoff — Phase 2 done, Phase 3 next (2026-09-14)

> **Superseded** by `2026-09-14-phase-3-to-phase-4.md` for project state. Its §3 traps still apply.

Read this, then `docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md` (§7 Phase 3,
§13 decisions) and `docs/superpowers/plans/2026-09-14-phase-2-missions.md` (§9 "What Phase 3
inherits", §11 deviations). Environment traps from the previous handoff
(`2026-09-14-phase-1-to-phase-2.md` §3) all still apply; only new ones are listed here.

---

## 1. Where the project stands

Milestones: M1 truthful dashboard ✓ → **M2 missions in simulation: Phase 2 ✓, Phase 3 next** →
M3 fake rover + firmware → M4 real scans from the app → M5 real drive.

**Phase 2 delivered** (details and the browser-verified list in the master plan's Phase 2 block):

| Layer | What exists now |
|---|---|
| Floor | `reconstruction/floor.py` — densest low 2 cm height bin; shared by segmentation and the navmesh |
| Navmesh | `twin/navmesh.py` — free / blocked / unknown, drops blocked, gap fill, base64 encode; 10 cm cells (decision 6) |
| Planning | `policy/planning.py` — clearance field, inflate, A* with clearance cost, clearance-preserving shortcut, `plan_mission` + `MissionError` |
| DB | `orchestrator/db.py` v2 via `PRAGMA user_version`: `missions`, `runs`, `policies.mission_id`, `robots.kind` allows `rover`; dev DB already migrated |
| API | `GET /twins/{id}/navmesh`, `POST /missions`, `GET /missions/{id}`; `/train` + `/deploy` follow a mission; `/sync` marks missions stale; `docs/api/openapi.yaml` generated |
| SDK | `navmesh`, `mission`, `get_mission`, `train(mission_id=)`, `decode_cells`, `run_pipeline(goal=)` |
| Dashboard | `MissionMap.jsx` + `navmesh.js`, Mission stage 06, 3D navmesh floor and route tubes, fixture re-recorded with a mission |
| Measured | `benchmarks/planning.json` — cold plan p95 8.0 ms (10 cm) / 27.4 ms (5 cm) on a 698k-point synthetic room, i5-13450HX |

**Checks:** pytest 284 passed · vitest 28 passed · flake8 clean · mypy clean (78 files) ·
`npm run build` passes.

**Open, not Phase 3 but must not be lost** (all in the plan):
- Phase 2: 3D layers not pixel-verified (browser pane was not drawing); "on a real scan" waits
  for M4 or a Scaniverse export.
- Phase 1: 1.11 Vite 5 → 8 / Vitest 2 → 5; 1.12 pixel check + object fills; the sim-gate 409
  in the browser (the mission-stale 409 *was* seen rendered).
- Step 0: order rover parts (owner); Arduino toolchain; one model copy on the phone; 13 Kotlin
  warnings; release the model before scanning / under memory pressure.
- Phase 8: planner prompt role mismatch. The old `com.infinity.ai` app is still on the phone.

---

## 2. Working tree — nothing committed since `5520c96`

`main` matches `origin/main`; the repository is **public**, so commit and push only when the
owner says so (end commit messages with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`).
Uncommitted: everything listed in the previous handoff §2, plus Phase 2:

- New: `reconstruction/floor.py`, `twin/navmesh.py`, `policy/planning.py`,
  `dashboard/src/MissionMap.jsx`, `dashboard/src/navmesh.js`, `dashboard/src/navmesh.test.js`,
  `scripts/bench_mission_planning.py`, `scripts/export_openapi.py`, `benchmarks/planning.json`,
  `tests/test_floor.py`, `tests/test_navmesh.py`, `tests/test_planning.py`,
  `tests/test_missions.py`, `tests/test_openapi.py`, the Phase 2 plan, this handoff.
- Changed: `orchestrator/db.py`, `orchestrator/service.py` (still CRLF in the working tree),
  `policy/evaluate.py`, `robot/policy_runner.py`, `robot/adapters/sim.py`,
  `semantic/service/inference.py`, `twin/generator.py`, `sdk/scanborn/client.py`,
  `requirements.txt` (scipy), `docs/api/openapi.yaml`, dashboard `Console.jsx`,
  `RobotViewer3D.jsx`, `api.js`, `pipeline.js`, `styles.css`, `vite.config.ts`, `fixtures.json`,
  tests `test_db.py`, `test_profile.py`, `test_sdk.py`, `test_semantic.py`, `api.test.js`,
  `pipeline.test.js`, the master plan.
- Deleted (staged with `git rm`): `dashboard/src/Floorplan.jsx`.

---

## 3. New traps found in Phase 2

**Editing**
- `orchestrator/service.py`: after any change, restore CRLF with
  `python -c 'p="orchestrator/service.py"; t=open(p, encoding="utf-8").read(); open(p, "w", encoding="utf-8", newline="\r\n").write(t)'`
  (single quotes, so the shell leaves `\r\n` alone). `git ls-files --eol` shows `i/lf w/crlf`.
- After changing any route, run `python scripts/export_openapi.py`; after changing a response
  shape, `python scripts/record_dashboard_fixture.py`, then `npm test`.
- numpy's `.tolist()` confuses mypy; wrap it in `typing.cast`.
- `cd` inside a Bash call moves the session's working directory; use absolute paths.

**Browser pane (in-app browser)**
- Do not use `resize_window` with a custom size: the emulated page is scaled into the pane and
  ref-based clicks then miss. Work at the pane's own size.
- When the pane is hidden, screenshots time out and `requestAnimationFrame` never fires, so the
  WebGL canvas cannot be read. DOM, `find`, `get_page_text` and network reads still work. Guard
  any rAF-based check with a timeout.
- The pane can change size between calls; take coordinates from the latest screenshot.
- `read_network_requests` `urlPattern` is a plain substring, not a regex.
- `browser_batch` takes at most 25 actions.
- The fixture button's accessible name is its tooltip ("Replays a real orchestrator run…").
- Seed a box-on-the-route rescan for stale checks with `tests.test_missions.with_box`
  (see the seeding command in this session's history, or import it in a one-liner).

---

## 4. Start here: Phase 3 — a simulation that behaves like the rover

1. Read the master plan's Phase 3 section and §6 rows "Simulation", "Collision check",
   "Sim gate"; read Phase 2 plan §9.
2. **Write `docs/superpowers/plans/2026-09-14-phase-3-diff-drive.md` first**, grounded in the
   code it changes, the same way the Phase 2 plan was:
   - `policy/evaluate.py` — `rollout(path=, trace=)`, `record_demos`, `evaluate`, `_lookahead`,
     `_cap`, `DEMO_GAIN`, `MAX_STEP` (holonomic, teleporting; `obs = target − pos`).
   - `policy/finetune/train_bc.py` — `LinearPolicy`, int8 export; `OBS_DIM = ACT_DIM = 2` in
     `orchestrator/service.py` stays, but obs/act change meaning to `[d·cos e, e]` → `(v, ω)`.
   - `robot/policy_runner.py`, `robot/adapters/sim.py` (`move()` teleports; keep for
     compatibility) and `deployment/aihub_export/export_script.py` (manifest must say what
     obs/act mean).
   - `orchestrator/service.py` `/train`: today random-start gate (60 %) + one clean mission
     rollout; Phase 3 replaces the latter with 30 noisy runs of the mission at
     `MISSION_GATE = 0.9`, stored as `runs` rows, metrics in the 409.
   - `policy/planning.clearance_field` is the collision test (`field < footprint radius`); the
     mission's `start[2]` heading and sparse `waypoints` are the controller's inputs.
   - Tests to update rather than delete: `test_policy.py`, `test_robot.py`,
     `test_orchestrator.py`, `test_missions.py`, `test_sdk.py`, the dashboard fixture.
   - Decide and record: the rover's step height vs `FLOOR_CLEARANCE = 0.08` (a 7 cm object would
     stop a 65 mm-wheel rover); noise magnitudes and where they come from until Phase 6
     measures them (label them as assumptions).
3. Then build it step by step with tests, keep the plan's checkboxes in sync, re-record the
   fixture, and check the dashboard in the browser with the pane in front.
