# ScanBorn — start here (current plan: 2026-09-18)

**Current source of truth:** [Enhanced master plan](plans/2026-09-18-enhanced-master-plan.md)
and [Execution checklist and evidence ledger](plans/2026-09-18-execution-plan.md).
Use these two documents for requirements, accuracy targets, API fallback and next implementation.
[Implementation handoff](handoff/2026-09-17-shared-viewer-calibration.md) retains measured evidence.
The sections below are historical orientation and environment notes; their older “next step”,
milestone counts and master-plan references do not override the current pair. Execution-plan
batches E1.1, E1.2 and E0 are software verified (18 Sept; evidence blocks in the execution plan);
latest full run 675 Python passed, 1 skipped (`build/stage-full-pytest.txt`); canonical flake8
and mypy pass (132 source files). Dashboard: 83 tests; Android: 50 unit tests and debug /
instrumentation APK builds. Provider settings, versioned float/int8 release evaluation, durable
native calibration/deployment recovery and stale-range protection are implemented. E2–E5 remain
partial until their remaining evidence gates close. Eighteen isolated browser/fake-rover checks
pass, including context loss with working E-STOP and narrow-screen layout. ESP32 host checks,
compilation and QEMU pass. Nine ordinary instrumentation tests now pass on the installed API 37 emulator (two harness-only
methods are skipped in that run). Separate actual force-stop/new-process checks pass for pending
deployment, calibration, processing-stage and synthetic capture-queue recovery. Native Stop/E-STOP survive renderer-error state and activity
recreation. Paginated mission/deployment history, route reopening and read-only result inspection
are implemented and tested. Generic native stage recovery now has a durable journal and read-only lookup; process tests also
cover stages and the synthetic capture queue. Camera/model capture lifecycle, full navigation, phone
rendering, physical accuracy and live-provider evaluation remain open. Production sensor safety
has 23 passing host fault checks; A3/A8 measurement reporting is implemented. See the execution ledger for precise scope and evidence.
Phone/physical acceptance (E7) remains open.

One document for a new chat: what the project is, what is built and measured, every decision that
still matters, what is open, how the owner works, the environment traps, and the next step. The
detail lives in the linked plans; this page is the map. Update it whenever a phase lands.

---

## 1. The project

Scan a room with a **Galaxy S24 FE** → build a digital twin with a map of where the robot fits →
the operator picks a start and a goal → train the robot's controller in a simulation that behaves
like the real robot → only a policy that passes the simulation gate drives a real **ESP32
two-wheel rover**. Existing features stay: voice, chat/OCR/PDF on the phone, environment profiles,
rescan-and-diff.

- Laptop (Lenovo LOQ, i5-13450HX, RTX 4050; **CPU only on the core loop**): FastAPI orchestrator,
  SQLite, reconstruction, segmentation, navmesh, planning, simulation, training, int8 export,
  dashboard.
- Phone (Exynos 2400e, **not Snapdragon — make no NPU claims**): ARCore capture (Phase 5, built,
  not yet run), on-device Qwen2.5-1.5B-Instruct Q4_K_M via llama.cpp.
- Rover (to be built, plan §4.3): ESP32 DevKit, 2× JGB37-520 motors with encoders, MPU6050, VL53L0X.
  Its firmware compiles, passes its self-test on the laptop and on an emulated ESP32, and has not
  run on a board.

**Repo:** `github.com/Aditya060806/ScanBorn` — **public**. `README.md` and `CLAUDE.md` were rewritten
from the code in Phase 7; where anything disagrees with this page or the plans, trust the plans.

## 2. Documents

| Document | What it holds |
|---|---|
| `docs/superpowers/plans/2026-09-13-scanborn-scan-to-drive-plan.md` | **Master plan.** §4 robot, §5 on-device model, §6 accuracy table, §7 phases with checkboxes and "Verified" blocks, §8 checklists, §11 verification commands, §13 decisions |
| `docs/superpowers/plans/2026-09-14-phase-2-missions.md` | Phase 2 detailed plan, decisions 1–16, §11 deviations |
| `docs/superpowers/plans/2026-09-14-phase-3-diff-drive.md` | Phase 3 detailed plan, decisions 1–14, §5 deviations |
| `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md` | Phase 4 detailed plan, decisions 1–15, §5 outcome and deviations |
| `docs/superpowers/plans/2026-09-15-phase-5-capture.md` | **Phase 5** detailed plan, decisions 1–16, §5 progress and deviations |
| `docs/superpowers/plans/2026-09-15-phase-7-honesty.md` | Phase 7 (honesty pass, done) — everything removed or renamed and why, §5 deviations |
| `docs/robot/rover-protocol.md` | The rover protocol, v1 — routes, payloads, states, telemetry, heartbeat, safety |
| `docs/capture/home-marker.svg` | The printable A4 home marker (print at 100 %; check its 10 cm bar) |
| `docs/superpowers/handoff/2026-09-16-phase-7-done.md` | Latest handoff (earlier ones hold older environment traps, all still valid) |
| `docs/hardware/s24fe.md`, `benchmarks/phone.json` | Phone specs and measured model speed |
| `benchmarks/planning.json` | Measured planning speed |
| `robot/specs/rover.yaml` | The rover's numbers — all placeholders until Phase 6 |

## 3. Milestones

| Milestone | Phases | State |
|---|---|---|
| M0 Unblocked | Step 0 | ✓ APK builds and runs on the S24 FE; throughput measured; Arduino toolchain on D:; Kotlin warnings 0. Open sub-items in §7 |
| M1 Truthful dashboard | 1 | ✓ incl. 1.11 (Vite 8) and 1.12 (route legibility) |
| M2 Missions in simulation | 2, 3 | ✓ browser-verified |
| M3 Drive a fake rover | 4 | ✓ browser-verified; 3D view and trusted E-STOP input verified in headless Chrome; firmware self-test passes on an emulated ESP32 |
| M4 Real scans from the app | 5 | **in progress:** laptop part built and tested; the phone session (step 5.5) is next |
| M5 Real drive | 6 | needs rover parts |
| Honesty | 7 | ✓ 2026-09-16 (built out of order while the phone session waited; needed no hardware) |
| Voice / design / accuracy | 8, 9, 10 | after the core loop |

No deadline. Build in this order; plan each phase in its own document first.

## 4. What is built (by layer)

- **Phone (Step 0):** llama.cpp `ggml_cpu` built with `-march=armv8.2-a+dotprod+i8mm` and
  `GGML_USE_CPU_REPACK`; HWCAP check before loading; debug switches `debug.scanborn.threads`,
  `debug.scanborn.repack`. Measured (359-token prompt, 6 threads): repack on — prefill 69 tok/s,
  first token 5.3 s, 256-token reply 19.0 s; baseline was 35.2 tok/s and 27.1 s.
- **Phone scan (Phase 5, built, not yet run on the phone):** `MOBILE-APP` package
  `com.scanborn.ai.scan` — ARCore raw depth + confidence (≥ 128, 0.25–4 m, full 16-bit millimetres),
  frame gates (tracking, 45°/s turn, stale, sparse, ~3 fps), home frame fixed at the Start tap
  (`HomeFrame.kt`), colour per depth pixel, camera preview, `UploadQueue` that retries and counts
  every frame it could not send; Settings → Orchestrator with "Save and test connection"; Tools →
  "Scan a space"; the model is released while scanning and reloads on the next AI request.
- **Dashboard (Phase 1):** real API client, one response→state mapper per stage, offline fixture
  recorded from the real orchestrator (`scripts/record_dashboard_fixture.py`), honest copy, measured
  telemetry, LAN access opt-in. Starting any stage clears its output, errors and everything
  downstream (`pipeline.js:clearFrom`).
- **Floor, navmesh, planning, missions (Phase 2):** robust floor (`reconstruction/floor.py`);
  three-state grid (`twin/navmesh.py`), floor band 5 cm, grid 5 or 10 cm from floor density;
  `policy/planning.py` (clearance field, A*, shortcut, named refusals); DB v2 (`missions`, `runs`);
  navmesh/mission routes; stale missions on rescan; `MissionMap.jsx`.
- **Capture and reconstruction (Phase 5):** fusion keeps voxels seen by ≥ 2 frames (one-frame scans
  keep all) and reports it; `POST /capture` validates and merges `meta` (bad `home_pose` → 400),
  `/complete` accepts final meta, `/reconstruct` returns `fusion` and `capture`;
  `scripts/check_scan_axes.py` for the axis walk.
- **Rover model (Phase 3):** `robot/spec.py` + `rover.yaml`; `policy/sim.py` (limits, slip, gyro
  bias, latency, placement error; true vs estimated pose); `policy/controller.py` (pure pursuit,
  obs `[d·cos e, e]`, K_V 1.0, K_W 2.5, lookahead 0.35 m, stop radius 2 cm, `control_step` shared by
  every loop); `policy/evaluate.py` (mission gate: 30 noisy runs ≥ 90 % within 10 cm; no-mission
  gate: 20 random starts ≥ 60 %).
- **Rover protocol and orchestrator (Phase 4):** `robot/protocol.py`, `robot/adapters/fake_rover.py`,
  `robot/adapters/rover.py`; `orchestrator/deployments.py`; `/robots`, `/deployments`,
  `WS /ws/deployments/{id}`; `runs` rows with `deviation_from_sim_m`, `reported_miss_m`, and
  `true_miss_m` for the fake rover only. `docs/api/openapi.yaml` generated (28 paths).
- **Firmware (Phase 4):** `firmware/scanborn_rover/` — `rover_math.h` (C++14), odometry, motors,
  mission state machine, WiFi/mDNS/HTTP/UDP, safety, flash settings, calibration, `SELFTEST` build;
  golden vectors (7 control rows + 40 odometry ticks) generated into `golden.h` and `rover_spec.h`.
  Both builds use DIO flash.
- **Console (Phase 4):** robot choice and registration; live bar with Stop and **E-STOP** (also
  Space); map trace and rover marker; live 3D rover; result row; Deploy locked while driving; views
  in error boundaries (`ViewBoundary.jsx`); `live.js`.
- **Checks you can run:** `scripts/check_live_drive.py` (headless Chrome over DevTools: WebGL 3D view,
  trusted E-STOP key and click, latch, reset), `nox -s firmware_host`, `nox -s firmware_qemu`.
- **SDK:** `sdk/scanborn/client.py` covers every orchestrator route.
- **Honesty pass (Phase 7):** README, `CLAUDE.md`, app README, landing copy, licences and changelog
  rewritten from the code; fidelity mode answers 501; `scene_url` (DB v3); `KeywordPlanner`; local
  bundles labelled `host-cpu`; UNO Q adapter, standalone capture/semantic services, `k8s/`,
  `configs/`, `examples/` removed; buggy experiment in `experiments/rc_buggy_snapdragon/`; one
  orchestrator Docker image checked by `tests/test_docker.py`; the app's About screen reads the build;
  the model's source confirmed by SHA-256.

**Checks at hand-off (2026-09-16):** pytest 413 passed · flake8 clean · mypy clean (92 files) · vitest
44 passed · `npm run build` passes · Gradle unit tests (26) and `assembleDebug` pass with 0 Kotlin
warnings · `check_live_drive.py` 13/13 · `docker compose config` parses. Firmware checks were not
re-run in Phase 7 (no firmware changed); last run 2026-09-15: `nox -s firmware` both builds compile,
`firmware_host` PASS, `firmware_qemu` PASS.

## 5. Decisions and findings that still matter

- **Robot:** the reference ESP32 differential-drive rover (plan §4.3); the owner orders parts.
- **On-device model:** Qwen2.5-1.5B-Instruct Q4_K_M, weight repacking on by default.
- **Honesty:** only measured numbers; fixtures labelled; spec values `placeholder`, noise `assumed`;
  the fake rover says `fake` everywhere and only it reports a true miss; nothing is printed as
  measured unless it was (the firmware says "gyro bias not measured" without an IMU).
- **Missions are immutable;** stale means a rescan leaves the path too narrow for the robot.
- **Dead reckoning limits route length (Phase 3 decision 14):** 2.5 m routes reach the goal within
  10 cm in 30/30 runs, 3.7 m in 25/30, 4.6 m in 23/30; demo routes stay near 3 m; Phase 10's "phone
  rides the rover" is the fix.
- **Floor band 5 cm:** objects 2–5 cm tall are neither mapped nor climbable — on the §8 checklist.
- **The control loop runs on the rover;** the orchestrator never steers. A rover deploy needs a
  mission. Protocol v1 details in Phase 4 §5.
- **Scan registration (Phase 5 decision 5):** origin at the phone at the Start tap, +Y its forward
  direction, +Z up; `home_pose` is always `[0, 0, 1.5708]`. The same marker places the rover.
- **Fusion keeps what several frames saw (Phase 5 decision 7).**
- **Phase 7 removals and renames:** robot kinds are `sim`, `fake_rover`, `rover` (the UNO Q adapter
  is gone; the DB CHECKs still accept `unoq` for old rows and `/deploy` refuses it); `twins.scene_url`;
  reconstruction mode `fidelity` is a 501; a local bundle's `device_label` is `host-cpu`. Do not
  bring any of these back without a plan.

## 6. How the owner works

- Wants expert, proper planning before building, and **no loose ends**: every gap goes into the plan.
- Delegates judgement calls — make the call, state why.
- Terse replies ("continue", "done"); expects long autonomous stretches.
- **Honesty is a hard rule.** Say plainly what could not be verified.
- **Commit and push only on explicit request** (public repo). Never expose `.env` keys.
- The phone is their daily device: check the focused app before any `adb` tap; never drive another
  app; ask them to do on-phone steps.
- Downloads need their yes first (they approved Espressif's QEMU, 36 MB, on 2026-09-15).

## 7. Open items (not lost)

- **Phase 5, step 5.5 (next, needs the owner and the phone):** see §9. It now also checks the app's
  Settings → About screen (Phase 7 decision 16, built but not seen on the phone).
- **Docker image (Phase 7):** written and its package list tested, but never built or run — the
  Docker daemon is off and a build downloads `python:3.11-slim` plus the wheels. Needs the owner's yes.
- **GSAP** (landing animation) is under GSAP's own no-charge licence, not an open-source one —
  Phase 9 decides whether to keep it.
- **Step 0:** order rover parts (owner) · one model copy on the phone · check the model release and
  reload on the phone during the scan session.
- **Needs a real scan:** Phase 2's "path under a second on a real scan" and whether real scans reach
  the 5 cm grid threshold — closed by the Phase 5 session.
- **Phase 6:** every placeholder in `rover.yaml` and `PLACEHOLDER` in `firmware/scanborn_rover/config.h`;
  the `SELFTEST` build on the board; motors, encoders, IMU, ToF, battery, WiFi; calibration; first
  real drive.
- **Phase 8:** planner prompt role mismatch (phone sends the task prompt as a user turn).
- **Phase 9:** rebrand leftovers (∞ mark in several Kotlin screens, including Settings, and launcher
  icons).
- The old `com.infinity.ai` app is still installed on the phone (owner's call).
- Nothing committed since `5520c96` (Phases 1–7 are all uncommitted in the working tree).

## 8. Environment traps (all sessions)

**Shell and files**
- Windows + Git Bash + PowerShell. The Bash tool collapses `\\` in inline commands: write files with
  backslashes via Write/Edit or a scratchpad script. `cd` in a Bash call moves the session's working
  directory — use absolute paths, and `cd` back to the repo root at the end of a command.
- Python 3.11 global (no venv): no backslashes inside f-string expressions. `pytest.approx` does not
  accept nested lists. Printing non-ASCII to the Windows console needs
  `sys.stdout.reconfigure(encoding="utf-8")`.
- `orchestrator/service.py` is CRLF in the working tree: patch it through a scratchpad script that
  writes `newline="\r\n"`. Many other files are CRLF too (git stores LF); a small helper that reads
  with `newline=""`, replaces exact strings and writes the original line endings back handles all of
  them. Write such patch scripts with the Write tool — a heredoc in the Bash tool mangles `\\`.
- After changing a route: `python scripts/export_openapi.py`. After changing a response shape:
  `python scripts/record_dashboard_fixture.py` then `npm test`. After changing the controller, policy
  format or rover spec: `python scripts/gen_rover_golden.py`.
- Run unsandboxed: Gradle, `adb`, `npm install`/`audit`, `arduino-cli` (network or sockets).
- The full pytest suite (~4–13 min depending on load) starves the orchestrator and slows Gradle and
  arduino-cli. Do not run it during browser checks.

**Android**
- SDK `D:\Android_SDK`; `MOBILE-APP/local.properties` must read `sdk.dir=D\:\\Android_SDK`.
- Every JVM needs `JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=D:\tmp` (set at user level; set it
  again in agent shells, as `D:/tmp` from Git Bash). Keep the laptop awake during builds. Git Bash
  rewrites `/data` and `/sdcard` for `adb` — prefix `MSYS_NO_PATHCONV=1`. Phone adb id `RZCY90QZFRD`.
- A cold `gradlew :app:testDebugUnitTest :app:assembleDebug` takes ~15 min; warm ~2 min. The debug
  APK is ~1.23 GB (the model is inside).
- Gradle snapshots sources when it compiles: do not add source files while a build is running.

**Phone session**
- **The phone cannot reach `localhost`.** For scans run the orchestrator on the network:
  `python -m uvicorn orchestrator.service:app --host 0.0.0.0 --port 8000`, allow port 8000 in
  Windows Firewall (private network), and enter `http://<laptop LAN IP>:8000` in the app's Settings.

**Arduino and firmware**
- The global `%LOCALAPPDATA%\Arduino15` is a broken link into the owner's C: offload — leave it.
  Everything lives in `D:/Arduino15`: `SCANBORN_ARDUINO_DATA=D:/Arduino15 python -m nox -s firmware`
  (~8 min cold, longer under load).
- Arduino.h defines `PI`, `TWO_PI`, `HALF_PI`, `DEG_TO_RAD`, `DEFAULT`, `EXTERNAL`, `bit()`, `sq()`,
  `radians()`, `degrees()` as macros — never use those names in firmware code.
- The host compiler is MinGW.org GCC 6.3 at `C:\MinGW\bin` (32-bit, no C++17): keep `rover_math.h`
  C++14; `SCANBORN_CXX=C:/MinGW/bin/g++.exe python -m nox -s firmware_host`.
- **ESP32 emulator:** Espressif's QEMU at `D:\Tools\esp-qemu`. Images must be DIO flash (the nox
  FQBN is `esp32:esp32:esp32:FlashMode=dio`); a QIO image asserts in `init_flash`.
  `SCANBORN_QEMU=D:/Tools/esp-qemu/qemu/bin/qemu-system-xtensa.exe python -m nox -s firmware_qemu`
  after `nox -s firmware`. No WiFi radio (the driving image stops in `esp_phy_enable`), motors or
  sensors.

**Browser pane**
- `.claude/launch.json` has `orchestrator` (:8000, localhost only) and `dashboard` (:5173) servers.
  The orchestrator preview does not reload — restart it after Python changes.
- Seed scans with `python scripts/seed_synthetic_scans.py` and type the id into Capture.
- The README's screenshots are crops of `check_live_drive.py`'s captures:
  `python scripts/readme_screenshots.py` after a check run.
- Do not use `resize_window` with a custom size. Refs reset after `navigate`: `find` first.
  `browser_batch` takes at most 25 actions.
- **A hidden pane** gets no animation frames, throttles timers and times out `computer` clicks and
  `get_page_text`: drive the page with DOM clicks from `javascript_tool` and read the DOM.
- **The pane may have no WebGL**, and **`computer key` sends keydown with empty `key` and `code`.**
  For both, use `python scripts/check_live_drive.py` (headless Chrome, its own temporary profile,
  the real GPU, trusted input; output in `build/live_check/`). It never touches the owner's browser.
- A fake-rover drive lasts about 12 s, shorter than a tool round trip: run deploy → mid-drive action →
  checks inside one polling script.

## 9. Next step: Phase 5 step 5.5 — the phone session (owner + agent)

Plan: `docs/superpowers/plans/2026-09-15-phase-5-capture.md` §3 5.5. The owner does every on-phone
action; check `dumpsys window | grep mCurrentFocus` before any `adb` input.

**First, in a new chat:** run `adb devices` (it can show `offline` for a moment; `adb reconnect
offline` then `adb wait-for-device` brought it back on 2026-09-16). If the S24 FE is listed and the
owner says the phone and the printed marker are ready, do 5.5 (steps 1–7 below). If not, do not wait:
build B, and return to 5.5 as soon as the phone is ready.

- **A. Phase 7, honesty pass — done 2026-09-16.** Plan, removals and deviations in
  `docs/superpowers/plans/2026-09-15-phase-7-honesty.md`; open leftovers in §7.
- **B. Phase 6, the laptop side** (master plan §7 Phase 6; write its own plan doc first):
  calibration arithmetic — tape-measured 1.0 m straight → counts per metre, three 360° spins →
  effective wheelbase — that writes `robot/specs/rover.yaml` with `source: calibrated` and the
  rover's flash through `POST /config`; a bench bring-up script that walks the owner through motor
  directions and encoder and gyro signs over the protocol; wheel-PI step-response logging with a
  dashboard chart over UDP; the 1 m square odometry check. Build and test all of it against the fake
  rover; every hardware step stays unticked until the parts arrive.

1. **Owner:** print `docs/capture/home-marker.svg` at 100 % and check the 10 cm bar; tape it to the
   floor in a room; have a tape measure for a 2 m walk.
2. **Owner:** connect the S24 FE by USB with USB debugging on; say it is free to install.
3. **Agent:** start the orchestrator on the network (§8 Phone session); install the debug APK
   (`gradlew :app:installDebug` or `adb install -r`).
4. **Owner:** open ScanBorn → Settings → Orchestrator, enter the laptop's address, Save and test.
   Then Tools → Scan a space; allow the camera; stand over the marker, point along the arrow, Start;
   sweep the room; Finish. Then a second scan: Start on the marker and walk 2 m straight ahead
   (tape-measured), Finish.
5. **Agent:** reconstruct → segment → twin for the room scan (`home_pose` must be `[0, 0, 1.5708]`;
   a mission's default start is the marker); `python scripts/check_scan_axes.py <walk scan> --walk-m 2.0
   --save-poses tests/fixtures/scan_axis_s24fe.json`; record depth mode, frames kept and skipped,
   fusion drops, axis error, plan time and grid size; check chat still works after the scan (model
   reload).
6. **Agent:** delete `capture/android/`; tick the master plan's Phase 5 items with a Verified block.
7. **Owner:** open Settings → About and read out Version, Build, Engine and Model (expected "1.0",
   "debug", "llama.cpp (CPU)", "Qwen2.5-1.5B-Instruct Q4_K_M"); the agent records it in the Phase 7
   plan §5.

## 10. Verification commands

```
python -m pytest -q -o addopts=""
python -m flake8 sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests scripts noxfile.py
python -m mypy sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests
cd dashboard; npm test; npm run build
SCANBORN_CXX=C:/MinGW/bin/g++.exe python -m nox -s firmware_host
SCANBORN_ARDUINO_DATA=D:/Arduino15 python -m nox -s firmware
SCANBORN_QEMU=D:/Tools/esp-qemu/qemu/bin/qemu-system-xtensa.exe python -m nox -s firmware_qemu
python scripts/check_live_drive.py            # orchestrator :8000 and dashboard :5173 running
cd MOBILE-APP; .\gradlew.bat :app:testDebugUnitTest :app:assembleDebug   # JAVA_TOOL_OPTIONS set
```
