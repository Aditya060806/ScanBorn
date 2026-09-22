> **Superseded by `2026-09-16-phase-7-done.md`** (Phase 7 landed; this page's phone-session steps still apply).

# Handoff — Phase 4 closed, Phase 5 built on the laptop, phone session next (2026-09-15)

Read `docs/superpowers/START-HERE.md` first, then
`docs/superpowers/plans/2026-09-15-phase-5-capture.md` (decisions 1–16, §5 progress) and the master
plan's Phase 5 section. Traps from earlier handoffs still apply; START-HERE §8 has all of them.

---

## 1. Where the project stands

Milestones: M1 ✓ → M2 ✓ → **M3 ✓ (Phase 4, its open checks closed)** → **M4 in progress (Phase 5:
laptop part done; the phone session is next)** → M5 real drive (needs parts).

**Closed since the previous handoff (Phase 4's "not verified")**

| Item | How |
|---|---|
| Live rover in the 3D view | `scripts/check_live_drive.py`: headless Chrome over DevTools on the RTX 4050 (ANGLE, D3D11), 13/13 checks; live trace 63 → 191 violet px, rover centroid moved, same canvas throughout |
| Space and the E-STOP button as real input | Trusted `Input.dispatchKeyEvent` / `Input.dispatchMouseEvent`: latched 43 ms after the key, 63 ms after the click; latched rover refused a deploy; reset to idle; no page errors |
| On an ESP32 | Espressif QEMU (owner approved the 36 MB download): SELFTEST image PASS; driving image boots to its sensor refusal and stops at WiFi's radio start (no radio in the emulator). Builds switched to DIO flash; fixed 8 logged pull-up errors on the input-only encoder pins and a printed-but-unmeasured gyro bias |
| Scene rebuild when a drive starts | `toPipe.deploy` keeps the shared empty `poseTrace` |

**Phase 5 built (not yet run on the phone)**

| Area | What exists |
|---|---|
| Reconstruction | Fusion keeps voxels seen by ≥ 2 frames, reports frames/min_views/dropped |
| Orchestrator | `meta` validated (bad `home_pose` → 400) and merged; `/complete` takes final meta; `/reconstruct` returns `fusion` and `capture` |
| Axis check | `scripts/check_scan_axes.py`, tested on synthetic walks |
| App | `com.scanborn.ai.scan`: ARCore raw depth + confidence, frame gates, home frame at Start, colour, camera preview, retrying upload queue; Settings → Orchestrator with Save and test; Tools → Scan a space; model released while scanning, lazy reload; `onTrimMemory` |
| Step 0 | Kotlin warnings 13 → 0 |
| Marker | `docs/capture/home-marker.svg` (A4, 10 cm check bar) |

**Checks:** pytest 405 passed · flake8 clean · mypy clean (97 files) · vitest 44 · dashboard build ·
Gradle 26 JVM tests + assembleDebug, 0 Kotlin warnings · `nox -s firmware` (DIO) ·
`nox -s firmware_host` PASS · `nox -s firmware_qemu` PASS · `check_live_drive.py` 13/13.

---

## 2. Working tree — nothing committed since `5520c96`

`main` matches `origin/main`; the repository is **public**: commit and push only when the owner
says so (commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`).
Uncommitted: everything in the previous handoffs, plus:

- New: `scripts/check_live_drive.py`, `scripts/check_scan_axes.py`, `docs/capture/home-marker.svg`,
  `docs/superpowers/plans/2026-09-15-phase-5-capture.md`, `tests/test_capture_meta.py`,
  `tests/test_scan_axes.py`, `MOBILE-APP/app/src/main/java/com/scanborn/ai/scan/*` (HomeFrame, Frames,
  DepthFilter, TurnRate, NpzWriter, ColorSampler, UploadQueue, Uploader, OrchestratorUrl, ArFrames,
  BackgroundRenderer, ScanController), `ui/screens/ScanScreen.kt`, `ui/screens/OrchestratorSettings.kt`,
  `data/OrchestratorPreference.kt`, `res/xml/network_security_config.xml`, their tests under
  `app/src/test/.../scan/`, this handoff.
- Changed: `reconstruction/fast_path/fusion.py`, `reconstruction/reconstruct.py`,
  `capture/service/store.py`, `orchestrator/service.py` (CRLF), `docs/api/openapi.yaml`,
  `tests/test_reconstruction.py`, `noxfile.py` (DIO FQBN, `firmware_qemu`),
  `firmware/scanborn_rover/odometry.cpp`, `scanborn_rover.ino`, `dashboard/src/pipeline.js`,
  `MOBILE-APP` (`AndroidManifest.xml`, `build.gradle.kts`, `libs.versions.toml`, `MainActivity.kt`,
  `AIRepository.kt`, `LlamaEngine.kt`, `LibraryDatabase.kt`, `AppNavigation.kt`, `ToolsScreen.kt`,
  `SettingsScreen.kt`, six screens' icons), the master plan, the Phase 4 plan, START-HERE, the
  previous handoff (marked superseded).
- Outside the repo: `D:\Tools\esp-qemu` (Espressif QEMU).

---

## 3. New traps

- **The phone cannot reach the localhost preview:** run the orchestrator with `--host 0.0.0.0` and
  allow port 8000 in Windows Firewall for the phone session.
- **QEMU needs DIO flash images;** it has no WiFi radio, motors or sensors.
- **Gradle snapshots sources at compile time;** writing source files during a build splits the change
  across two builds. A cold app build is ~15 min.
- **ESP-IDF's pulse-counter driver requests pull-ups on every counter pin** — on GPIO 34–39 that logs
  errors; the firmware silences the `gpio` tag only during encoder attach.
- **Windows console output:** a script printing "→" crashes on cp1252 without
  `sys.stdout.reconfigure(encoding="utf-8")`.

---

## 4. Start here: Phase 5 step 5.5, the phone session

**If the phone is not connected (`adb devices` lists nothing) or the owner is not ready, do not
wait:** build Phase 7 (honesty pass), then the laptop side of Phase 6, each planned in its own doc
first — START-HERE §9 A and B list exactly what. Return to the phone session when it is ready.

Follow START-HERE §9 exactly. In short: the owner prints the marker and connects the phone; the agent
starts the orchestrator on the network and installs the APK; the owner sets the address in Settings,
scans a room from the marker, and does a tape-measured 2 m walk scan; the agent reconstructs the room
into a twin (home pose `[0, 0, 1.5708]`), runs the axis check and saves its poses as the fixture,
records the measurements (including Phase 2's "on a real scan" numbers), checks chat still works after
scanning, deletes `capture/android/`, and writes the Phase 5 Verified block.
