# Handoff — Phase 4 done, Phase 5 next (2026-09-15)

> **Superseded** by `2026-09-15-phase-5-phone-session.md`. Its traps (§3) still apply.

Read `docs/superpowers/START-HERE.md` first, then the master plan
(`docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md`, §7 Phase 4 "Verified" block and
Phase 5) and `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md` (decisions 1–15, §5
outcome). The environment traps in the earlier handoffs still apply; new ones are in §3 below and in
START-HERE §8.

---

## 1. Where the project stands

Milestones: M1 truthful dashboard ✓ → M2 missions in simulation ✓ → **M3 fake rover + firmware ✓
(Phase 4)** → **M4 real scans from the app (Phase 5) next** → M5 real drive.

**Done since the last handoff**

| Area | What exists now |
|---|---|
| Toolchain | ESP32 core 3.3.11 and the four libraries under `D:/Arduino15`; the broken C: link left alone; `nox -s firmware` and `nox -s firmware_host` |
| Shared step | `policy/controller.py:control_step`, used by the gates, the fake rover and (in C++) the firmware |
| Protocol | `docs/robot/rover-protocol.md` v1; `robot/protocol.py` validation, payload, int8 policy |
| Fake rover | `robot/adapters/fake_rover.py` — HTTP + UDP, rover model inside, deadman, E-STOP latch, `/sim/world`, simulated calibration; CLI |
| Adapter | `robot/adapters/rover.py` — `RoverRobot` commands and heartbeat thread; registry adds `rover` |
| Sessions | `orchestrator/deployments.py` — one UDP telemetry socket, sessions by token, silence → `lost`, `on_finish` once |
| Orchestrator | `/robots` routes, rover `/deploy` (mission required, footprint check, busy check), `/deployments/{id}`, stop, `WS /ws/deployments/{id}`, `runs` rows with deviation from the simulated run; fake rovers closed on shutdown |
| Firmware | `firmware/scanborn_rover/` — control math, odometry, motors, mission state machine, network, safety, flash settings, calibration, `SELFTEST`; `firmware/tests/host_selftest.cpp` |
| Golden vectors | 7 control rows + 40 odometry ticks; `golden.h` and `rover_spec.h` generated and checked for staleness |
| Console | robot choice and registration, live bar with Stop/E-STOP (Space), live map trace and rover, 3D rover, result row, Deploy lock, view error boundaries, `live.js` |
| SDK | `register_robot`, `robot_status`, `estop`, `reset_robot`, `calibrate`, `deployment`, `stop_deployment` |

**Measured:** host self-test PASS (0 failed checks at 1e-9). Firmware sizes: driving build
1,070,055 bytes (81 % of flash), 61,244 bytes RAM; `SELFTEST` 499,511 bytes (38 %). In the browser:
five fake-rover drives of a 2.55 m mission ended `done` with 2 cm RMS from the simulated run, 2 cm
reported miss, true miss 1–5 cm; E-STOP by Space and by button latched with reason `dashboard` about
0.1 s after the press, a deploy to the latched rover was refused (409 `rover_refused`), reset
returned it to `idle`.

**Checks:** pytest 387 passed · flake8 clean · mypy clean (95 files) · vitest 44 passed ·
`npm run build` passes · `npm audit` 0 vulnerabilities · OpenAPI 28 paths · both nox firmware
sessions pass.

**Open, not Phase 5 but must not be lost**
- The live rover in the 3D view is unverified: WebGL could not start in the browser pane this
  session. Check it in a browser with a GPU (the owner's Chrome) during any later browser pass.
- Nothing has run on an ESP32. Phase 6: every `PLACEHOLDER` in `config.h`, every placeholder in
  `rover.yaml`, the `SELFTEST` build on the board, calibration, PI tuning, the first real drive.
- "On a real scan" checks from Phase 2: Phase 5 closes them.
- Step 0: order rover parts (owner); one model copy on the phone; 13 Kotlin warnings; release the
  model before scanning / under memory pressure (Phase 5 touches this — ARCore capture needs the
  memory). Phase 8: planner prompt role mismatch. `com.infinity.ai` still on the phone.

---

## 2. Working tree — nothing committed since `5520c96`

`main` matches `origin/main`; the repository is **public**: commit and push only when the owner
says so (commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`).
Uncommitted: everything in the previous handoffs, plus Phase 4:

- New: `robot/protocol.py`, `robot/adapters/fake_rover.py`, `robot/adapters/rover.py`,
  `orchestrator/deployments.py`, `firmware/` (sketch, headers, sources, `secrets.example.h`,
  generated `golden.h` and `rover_spec.h`, `tests/host_selftest.cpp`), `docs/robot/rover-protocol.md`,
  `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md`, `dashboard/src/live.js`,
  `live.test.js`, `ViewBoundary.jsx`, tests `test_protocol.py`, `test_rover_protocol.py`,
  `test_rover_adapter.py`, `test_deployments.py`, `test_rover_deploy.py`, this handoff.
- Changed: `orchestrator/service.py` (CRLF working copy), `robot/adapters/registry.py`,
  `policy/controller.py`, `policy/evaluate.py`, `scripts/gen_rover_golden.py`,
  `tests/fixtures/rover_golden.json`, `tests/test_rover_golden.py`, `tests/test_sdk.py`,
  `sdk/scanborn/client.py`, `docs/api/openapi.yaml`, `noxfile.py`, `.gitignore` (`secrets.h`,
  firmware build folder), dashboard `Console.jsx`, `api.js`, `pipeline.js`, `MissionMap.jsx`,
  `RobotViewer3D.jsx`, `Stage.jsx`, `styles.css`, `vite.config.ts`, `api.test.js`,
  `pipeline.test.js`, the master plan, START-HERE, the previous handoff (marked superseded).
- Build output `build/firmware/` and `build/firmware_host/` is gitignored (`build/`).

---

## 3. New traps found in Phase 4

- **Arduino data folder:** use `D:/Arduino15` through the `ARDUINO_DIRECTORIES_*` variables (or
  `SCANBORN_ARDUINO_DATA` for nox); never repair the C: link. A cold `nox -s firmware` takes ~8 min.
- **Arduino macros:** `PI`, `TWO_PI`, `HALF_PI`, `DEG_TO_RAD`, `DEFAULT`, `bit()`, `sq()` and friends
  are macros in Arduino.h; a C++ constant with one of those names does not compile.
- **MinGW GCC 6.3 is C++14 and 32-bit:** `-msse2 -mfpmath=sse` keeps doubles off x87.
- **Browser pane hidden:** clicks and `get_page_text` time out; drive the page with DOM clicks from
  `javascript_tool` and read the DOM. **WebGL may not start at all**, even when visible.
- **`computer key` has no key name:** its keydown arrives with empty `key` and `code`.
- **Drives outlast round trips:** a fake-rover drive takes ~12 s; do deploy → E-STOP → checks in one
  polling `javascript_tool` call.
- **Do not run the full pytest suite during browser checks:** it starved the orchestrator (twin
  generation 102 s instead of seconds).
- **Telemetry port:** the orchestrator binds UDP 5005 on the first rover deploy
  (`SCANBORN_TELEMETRY_PORT`; tests use 0).

---

## 4. Start here: Phase 5 — capture accuracy, registration, one app

1. Read the master plan's Phase 5 list and done-when, and START-HERE §9.
2. **Write `docs/superpowers/plans/2026-09-15-phase-5-capture.md` first**, grounded in the code it
   builds on: `capture/android/` (`Frames.kt`, `NpzWriter.kt`, `Uploader.kt`), `MOBILE-APP` (Compose
   navigation, `ToolsScreen.kt`'s empty `onClick`, settings/DataStore, manifest, the llama.cpp model's
   memory — release it when capture starts), `capture/service/` and the `.npz` + `meta.json` format,
   `reconstruction/fast_path/fusion.py`, and the twin's existing `home_pose` path
   (`orchestrator/service.py:_home_pose`; missions start there by default).
3. Build step by step with tests; on-phone steps are the owner's (check `mCurrentFocus` before any
   `adb` input). Done when a scan on the S24 FE produces a twin whose home pose matches the marker
   and the axis check passes — which also closes Phase 2's "on a real scan" checks.
