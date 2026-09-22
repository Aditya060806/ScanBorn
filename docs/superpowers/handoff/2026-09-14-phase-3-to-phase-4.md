# Handoff — Phase 3 done, Phase 4 next (2026-09-14)

> **Superseded** by `2026-09-15-phase-4-to-phase-5.md`. Its environment traps (§3) still apply.

Read this, then `docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md` (§7 Phase 4,
§4.3–4.5 rover, §13 decisions) and `docs/superpowers/plans/2026-09-14-phase-3-diff-drive.md`
(decisions 1–14, §5 outcome). The environment traps in `2026-09-14-phase-1-to-phase-2.md` §3 and
`2026-09-14-phase-2-to-phase-3.md` §3 still apply; new ones are in §3 below.

---

## 1. Where the project stands

Milestones: M1 truthful dashboard ✓ → **M2 missions in simulation ✓ (Phases 2 and 3)** →
**M3 fake rover + firmware (Phase 4) next** → M4 real scans from the app → M5 real drive.

**Done since the last handoff**

| Area | What exists now |
|---|---|
| Phase 2 fixes | Grid size chosen per scan from floor density (`twin/navmesh.py:choose_cell`, 5 or 10 cm, `resolution` in the API and on stage 04); 3D layers verified by pixel count; route tubes scale with the room (1.12 ✓) |
| Phase 1.11 | Vite 8.3, Vitest 5.0, plugin-react 6.1; `npm audit` 0 vulnerabilities |
| Rover spec | `robot/specs/rover.yaml` + `robot/spec.py` — every value `placeholder`, noise `assumed`; `GET /robot-specs/{name}`; the 3D viewer sizes the rover from it |
| Rover model | `policy/sim.py` — limits, slip, gyro bias, latency, placement error; true vs estimated pose |
| Controller | `policy/controller.py` — pure pursuit `obs = [d·cos e, e]`, `K_V 1.0`, `K_W 2.5`, lookahead 0.35 m, stop radius 2 cm |
| Gates | `policy/evaluate.py` — mission: 30 noisy runs ≥ 90 % within 10 cm; no mission: 20 random starts ≥ 60 %; outcomes reached / stopped off goal / collided / timeout |
| Orchestrator | `/train` on the model with metrics and a median trace; 409s carry the numbers; footprint check; `policy_outdated` refusal; one checkpoint folder per policy; mission-less deploy starts ~2.5 m out |
| Firmware reference | `robot/kinematics.py`; `scripts/gen_rover_golden.py` → `tests/fixtures/rover_golden.json` (7 poses through the whole per-tick chain) |
| Floor band | 5 cm (was 8 cm); 2–5 cm objects are on the §8 checklist |
| Console | Starting a stage clears its output, errors and everything downstream (`pipeline.js:clearFrom`) |

**Key finding (Phase 3 decision 14):** the rover dead-reckons. With the assumed noise (gyro bias
σ 0.002 rad/s after calibration, ±1° / ±1 cm placement) the ideal controller reaches a 2.5 m goal
within 10 cm in 30/30 runs, 3.7 m in 25/30, 4.6 m in 23/30; in the browser a 5.1 m route was
refused at 16/30. Demo routes stay near 3 m; Phase 10's "phone rides the rover" lifts the limit.

**Checks:** pytest 339 passed · vitest 33 passed · flake8 clean · mypy clean (86 files) ·
`npm run build` passes · browser-verified (master plan Phase 3 "Verified" block).

**Open, not Phase 4 but must not be lost**
- "On a real scan" checks (Phase 2 done-when, grid density at 5 cm): need a real scan — Phase 5,
  or a Scaniverse `.ply` export of a real room dropped into the dashboard.
- Every spec number and all noise are placeholders until Phase 6 calibration.
- Step 0: order rover parts (owner); **repair the Arduino toolchain (Phase 4 needs it)**; one model
  copy on the phone; 13 Kotlin warnings; release the model before scanning / under memory
  pressure. Phase 8: planner prompt role mismatch. `com.infinity.ai` still on the phone.

---

## 2. Working tree — nothing committed since `5520c96`

`main` matches `origin/main`; the repository is **public**: commit and push only when the owner
says so (commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`).
Uncommitted: everything in the previous two handoffs, plus Phase 3 and the fixes:

- New: `robot/spec.py`, `robot/specs/rover.yaml`, `robot/kinematics.py`, `policy/sim.py`,
  `policy/controller.py`, `scripts/gen_rover_golden.py`, `tests/fixtures/rover_golden.json`,
  `tests/test_spec.py`, `test_sim.py`, `test_controller.py`, `test_rover_golden.py`,
  `docs/superpowers/plans/2026-09-14-phase-3-diff-drive.md`, this handoff.
- Changed: `policy/evaluate.py` (rewritten), `robot/policy_runner.py`, `robot/adapters/base.py`,
  `robot/adapters/sim.py`, `deployment/aihub_export/export_script.py`, `orchestrator/service.py`
  (CRLF working copy), `twin/navmesh.py`, `twin/generator.py`, `sdk/scanborn/client.py`,
  `docs/api/openapi.yaml`, `benchmarks/planning.json`, dashboard `Console.jsx`,
  `RobotViewer3D.jsx`, `api.js`, `pipeline.js`, `vite.config.ts`, `package.json`,
  `package-lock.json`, `fixtures.json`, tests `test_policy.py`, `test_robot.py`,
  `test_missions.py`, `test_sdk.py`, `test_deployment.py`, `test_navmesh.py`, `api.test.js`,
  `pipeline.test.js`, the master plan.

---

## 3. New traps found in Phase 3

- **Hidden browser pane:** no animation frames and no screenshots. To read the WebGL canvas,
  switch tabs away and back inside one `javascript_tool` call, await a few microtasks, then
  `readPixels` — the viewer draws its first frame on mount. A read after a timeout returns zeros.
- **Refs reset on navigation:** after `navigate`, run `find` before clicking a ref; adding a
  control shifts every later ref number.
- **Typing into the console's number fields:** `triple_click` the label ref, `ctrl+a`, type,
  `Return` — the field commits on Enter/blur.
- **`pytest.approx` does not take nested lists** — flatten first.
- **The orchestrator preview server does not reload**: restart it after changing Python.
- **Training a mission takes ~0.1–0.3 s** on the synthetic room; long routes fail the gate by
  design (decision 14), so browser checks of success should use routes ≤ ~3 m.

---

## 4. Start here: Phase 4 — rover protocol, fake rover, firmware (no hardware needed)

1. Repair the Arduino toolchain first (Step 0): `%LOCALAPPDATA%\Arduino15\packages` is a broken
   symlink; then `arduino-cli core install esp32:esp32` and the libraries ArduinoJson,
   ESP32Encoder, "Adafruit MPU6050", VL53L0X (network: run unsandboxed). Confirm with
   `arduino-cli compile --fqbn esp32:esp32:esp32` on a blank sketch.
2. Read the master plan's Phase 4 contract table and the Phase 3 plan decisions 2, 4, 10 and 12.
3. **Write `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md` first**, grounded in the
   code it builds on:
   - `robot/adapters/base.py` velocity interface (`supports_drive`, `reset`, `step`,
     `estimated_pose`, `true_pose`) — `RoverRobot` implements it over HTTP + UDP, and
     `robot/adapters/fake_rover.py` serves the same protocol with `DiffDriveSim` inside.
   - `robot/policy_runner.run_policy` and `policy/evaluate.drive_outcome` — on the real rover the
     loop runs on the ESP32; the orchestrator sends mission + policy and relays telemetry.
   - `policy/controller.py:policy_manifest` + `tests/fixtures/rover_golden.json` — what the
     firmware must compute; the `SELFTEST` build checks the golden rows.
   - `orchestrator/service.py` `/deploy` (`kind`, `robots` table already allows `rover`,
     `runs` rows) and the new endpoints in the Phase 4 table.
   - Heartbeat deadman, E-STOP latch, and `WS /ws/deployments/{id}` for the live trace (which
     also gives the mission map its live rover marker, deferred from Phase 2).
4. Then build it step by step with tests, keep the plan's checkboxes in sync, and finish with the
   done-when: dashboard → orchestrator → fake rover completes a drive with a live trace, and the
   firmware compiles.
