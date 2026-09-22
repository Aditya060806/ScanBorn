# Handoff — Phase 1 done, Phase 2 next (2026-09-14)

> **Superseded** by `2026-09-14-phase-2-to-phase-3.md` for project state. Its §3 environment
> traps and §4 working notes still apply.

Read this first, then `docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md`
(the master plan; §7 lists every phase with done-criteria, §13 the decisions). This file
carries what the plan does not: the state of the working tree, what was verified and how, the
environment traps that cost time, and how the owner likes to work.

---

## 1. Where the project stands

**Goal (unchanged):** scan a room with the Galaxy S24 FE → digital twin with a map of where the
robot fits → pick start and goal → train in a simulation that behaves like the robot → only a
policy that passes drives a real ESP32 differential-drive rover. Existing features stay (voice,
chat/OCR/PDF, profiles, rescan).

**Decisions (plan §13, delegated by the owner):**
- Robot: the reference ESP32 two-wheel rover (plan §4.3). The owner orders the parts.
- No deadline. Build in milestone order: M1 dashboard ✓ → **M2 missions in simulation (Phases
  2 + 3)** → M3 fake rover + firmware → M4 real scans from the app → M5 real drive. Voice
  (Phase 8), design system (Phase 9) and the optional accuracy tier (Phase 10) come after the
  core loop.
- On-device model: keep Qwen2.5-1.5B-Instruct Q4_K_M. Weight repacking is on by default.

**Done and verified:**

| Area | State |
|---|---|
| Step 0 | APK builds and runs on the S24 FE; phone specs in `docs/hardware/s24fe.md`; Java fix in place |
| Phone model speed | Measured, in `benchmarks/phone.json`. Same 359-token prompt, 6 threads: repack **on** — prefill 69.0 tok/s, first token 5.3 s, full 256-token reply 19.0 s; repack off 46.8 / 7.7 s / 25.7 s. Baseline before any fix: 35.2 tok/s, 27.1 s |
| llama.cpp build | `ggml_cpu` compiled with `-march=armv8.2-a+dotprod+i8mm`; `GGML_USE_CPU_REPACK` defined; `scanborn_jni.cpp` checks HWCAP before loading; debug switches `debug.scanborn.threads` and `debug.scanborn.repack` |
| Phase 1 (dashboard) | Complete and checked in the browser against the live orchestrator — see the plan's Phase 1 "Verified" list |
| Tests | pytest **230 passed**; vitest **15 passed**; flake8 clean (incl. `scripts/`); mypy clean (70 files); `npm run build` passes |

**Open items that are not Phase 2 but must not be lost** (all written in the plan):
- Step 0: order rover parts (owner); repair the Arduino toolchain; one copy of the model on the
  phone; clear 13 Kotlin compiler warnings; release the model before scanning and under memory
  pressure (repack makes ~1.15 GiB app-owned).
- Phase 1: **1.11** Vite 5 → 8 and Vitest 2 → 5 (5 npm advisories left, all need majors);
  **1.12** route legibility in 3D; the 409 sim-gate failure has not been seen in the browser
  (cannot be forced from the UI).
- Phase 8: planner prompt role mismatch (phone sends the task prompt as a user turn; Python
  sends it as system).
- The old `com.infinity.ai` app is still installed on the phone (owner's call).

---

## 2. Working tree — nothing below is committed

Last commit `5520c96` ("Update dashboard and mobile app fixes", by the owner) is pushed;
`main` matches `origin/main`. Everything since is **uncommitted**. The repository is
**public** (`github.com/Aditya060806/ScanBorn`), so **commit and push only when the owner says
so**, and end commit messages with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

| Change | Files |
|---|---|
| ARM flags, weight repacking, CPU check, debug switches | `MOBILE-APP/app/src/main/cpp/CMakeLists.txt`, `scanborn_jni.cpp`, `…/ai/engine/LlamaEngine.kt` |
| Measurements and docs | `benchmarks/phone.json`, `docs/hardware/s24fe.md`, the master plan, this handoff |
| Orchestrator | `orchestrator/service.py`: `POST /plan/preview`, `phone` in `GET /benchmarks` (`BENCHMARK_DIR`) |
| Dashboard | `api.js` (real client), new `pipeline.js`, `fixtures.js`, `fixtures.json`, `api.test.js`, `pipeline.test.js`; rewritten `Console.jsx`, `NLPPlayground.jsx`, `Telemetry.jsx`, `RobotViewer3D.jsx`, `Landing.jsx`; `ReconCanvas.jsx`, `recon.js`, `JobFeed.jsx`, `styles.css`, `index.html`, `vite.config.ts`, `package.json`, `package-lock.json`; `modify_console.js` deleted |
| Scripts and tests | `scripts/record_dashboard_fixture.py`, `scripts/seed_synthetic_scans.py`, `tests/test_dashboard_api.py` |
| Tooling | `.claude/launch.json` (preview servers) |

---

## 3. Environment traps (each one cost real time)

**Windows + agent shell**
- The **Bash tool collapses `\\` to `\`** in inline commands. Write source files containing
  backslashes with Write/Edit, not heredocs; keep inline Python patch scripts backslash-free.
- **Python is 3.11** (global, no venv): an f-string expression cannot contain a backslash.
- **`orchestrator/service.py` has CRLF line endings.** Patch it by reading with universal
  newlines and writing back with `newline="\r\n"`, or anchors will not match. Most other files
  are LF in the working tree.
- **Git Bash rewrites `/data` and `/sdcard`** into Windows paths before `adb` sees them: prefix
  with `MSYS_NO_PATHCONV=1`.
- Commands that need the sandbox **disabled**: Gradle (loopback sockets), `adb`, `npm install`
  / `npm audit` (network), `arduino-cli`.

**Android build**
- SDK at `D:\Android_SDK` (android-36, build-tools 36, NDK 28.2.13676358, CMake 3.22.1).
  `MOBILE-APP/local.properties` must be `sdk.dir=D\:\\Android_SDK` — a single backslash fails
  with "filename, directory name, or volume label syntax is incorrect".
- Every JVM on this laptop fails with "Unable to establish loopback connection" unless
  `jdk.net.unixdomain.tmpdir` points at a directory on `D:`. The **user-level**
  `JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=D:\tmp` is set, but agent shells may not
  inherit it. Build from PowerShell, unsandboxed:
  ```
  if (-not $env:SystemRoot) { $env:SystemRoot = 'C:\WINDOWS' }
  $env:JAVA_TOOL_OPTIONS = "-Djdk.net.unixdomain.tmpdir=D:\tmp"
  Set-Location D:\Endeavors\Coding\Projects\DragVerse\MOBILE-APP
  & .\gradlew.bat assembleDebug --no-daemon --console=plain
  ```
- Keep the laptop awake during builds: sleep kills the Gradle daemon with only "daemon
  disappeared unexpectedly".
- The debug APK is ~1.3 GB (model bundled). Install with
  `D:\Android_SDK\platform-tools\adb.exe install -r …\app-debug.apk` (~45–65 s).

**Phone (S24 FE, adb id `RZCY90QZFRD`)**
- The owner uses the phone while work runs (Netflix, WhatsApp calls and Instagram were in the
  foreground). **Before any `adb shell input` or `am start`, read
  `dumpsys window | grep mCurrentFocus`**; never launch over a call, and never type into an app
  that is not ScanBorn. Ask the owner to send chat messages rather than driving the UI.
- Taps right after a cold start get lost; wait for `Model loaded successfully` in logcat first.
- Measurement recipe and every `adb` command: `docs/hardware/s24fe.md`. Logcat tags:
  `LlamaEngine` (stats lines), `ScanBornLlama` (native; llama.cpp INFO is logged at Debug),
  `AIRepository`, `ModelStorage`.
- Storage runs tight (it hit 99 % once). The currently installed build is the repack build with
  both debug switches cleared.

**Arduino**
- `arduino-cli` is installed, but `%LOCALAPPDATA%\Arduino15\packages` is a broken symlink, so it
  reports "No platforms installed". Repair before Phase 4 firmware (plan Step 0).

**Dashboard + orchestrator**
- Preview servers: `.claude/launch.json` has `orchestrator` (uvicorn :8000) and `dashboard`
  (Vite :5173). The orchestrator writes `data/` and `data/scanborn.db` (gitignored).
- Seed a scan without the file picker: `python scripts/seed_synthetic_scans.py`, then type the
  id into Capture. The in-app browser's accessible name for the fixture button is its `title`.
- Vite proxy keys are path prefixes. **New routes (`/twins`, `/missions`, …) must be added to
  `dashboard/vite.config.ts`** or the browser gets the SPA instead of JSON.
- If an API response shape changes, re-record the offline fixture:
  `python scripts/record_dashboard_fixture.py`, then `npm test`.
- Screenshots do not capture the WebGL canvas; verify 3D by reading pixels inside a
  `requestAnimationFrame` callback.
- LAN access to the dev server is opt-in (`DASHBOARD_LAN=1`) because of open Vite 5 advisories.

**Verification commands**
```
python -m pytest -q -o addopts=""
python -m nox -s lint            # or: python -m flake8 <pkgs> scripts/ && python -m mypy <pkgs>
cd dashboard; npm test; npm run build
```

---

## 4. How the owner works

- Wants **proper, expert planning first**, then building — and "no loose ends": every gap found
  goes into the plan, not just into a reply.
- Delegates judgement calls ("do what you think is best"); make the call, state it and why.
- Replies are short ("continue", "do", "done"); expect long autonomous stretches.
- **Honesty is a hard rule**: nothing on screen or in docs that was not measured; demos are
  labelled as fixtures; say plainly what could not be verified.
- Commits and pushes only on request (public repo).

---

## 5. Start here in the next session

1. Read the master plan's **§7 Phase 2 and Phase 3** (they form milestone M2), plus §6's
   accuracy table.
2. **Start with the plan:** write `docs/superpowers/plans/2026-09-14-phase-2-missions.md` — a
   detailed, file-by-file implementation plan for Phase 2 (and how it hands off to Phase 3),
   with the tests for each step and the done-criteria from the master plan. Ground it in the
   code first; the pieces that already exist and must be reused or replaced are:
   - `twin/generator.py` — `_navmesh()` (two-state grid, floor from minimum z, one point blocks a
     cell), `is_blocked()`, `nearest_free()`; keep their signatures working.
   - `policy/evaluate.py` — `inflate()` (reads the profile's `robot_radius` from the navmesh),
     `plan_path()` (8-way BFS, diagonal cost 1), `record_demos`, `rollout`, `evaluate`.
   - `orchestrator/service.py` — `_goal_for()` and `_start_pose()` choose goal and start
     automatically today; `/train`, `/deploy`, `/sync`.
   - `orchestrator/db.py` — schema with CHECK constraints (`robots.kind IN ('sim','unoq')`);
     no migration mechanism yet (Phase 2 adds `PRAGMA user_version`).
   - `dashboard/src/Floorplan.jsx` — becomes `MissionMap.jsx`; keep its `place()` projection.
   - `tests/test_orchestrator.py` — the synthetic room (`scan_frame`, `frame_bytes`).
3. Then build Phase 2 step by step with tests, keep the master plan's checkboxes in sync, and
   verify in the browser against the live orchestrator (seed script above).
