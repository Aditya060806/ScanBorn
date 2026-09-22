# Phase 5 — Capture accuracy, registration, one app (implementation plan)

Detailed plan for Phase 5 of `2026-09-13-scanborn-scan-to-drive-plan.md` (§7), written 2026-09-15
after reading the code it builds on. Milestone M4. The on-device steps need the Galaxy S24 FE and the
owner: it is their daily phone, so every on-phone action is theirs (check `mCurrentFocus` before any
`adb` input, never drive another app).

**Outcome:** the ScanBorn app scans a room with ARCore raw depth and its confidence, registered to
the home marker, and uploads it to the orchestrator chosen in Settings. The orchestrator keeps only
surfaces seen from more than one frame. The twin's home pose is the marker, and an axis check proves
the scan's +Y is the direction the phone pointed at Start.

---

## 1. What exists (read before changing)

| Piece | Where | State |
|---|---|---|
| Capture app | `capture/android/` (`com.scanborn.capture`) | A separate AppCompat app. GL surface with **no camera preview**; `acquireDepthImage16Bits()` read with `& 0x1FFF`, which truncates depth above 8.191 m (the 16-bit API is full-range millimetres, ARCore reference checked 2026-09-15); ~3 fps; no confidence filter, no depth-support check, no turn-rate gate; failed chunks are logged and never re-sent; orchestrator URL fixed at build time. Never built or run on hardware (its README says so) |
| Frame format | `NpzWriter.kt` ↔ `reconstruction/reconstruct.py:load_frames` | `.npz` of `depth` (H×W f4 metres), `intrinsics` (3×3, scaled to the depth resolution), `pose` (4×4 camera→map, OpenCV camera, Z-up map); optional `color` (H×W×3 u8) already read by `fusion.backproject` |
| Axis mapping | `Frames.kt` | map ← ARCore world `(x, y, z) → (x, −z, y)`; OpenCV camera ← ARCore camera flips Y and Z. The map origin is wherever ARCore's session started |
| Upload API | `orchestrator/service.py` `POST /capture` (`scan_id`, `index`, `meta` JSON string, file), `POST /capture/{id}/complete` | `meta` is written to `meta.json` whenever sent (a later one replaces it); no validation |
| Home pose | `service.py:_home_pose` reads `meta.json` → `home_pose`; `GET /twins/{id}/navmesh` returns it; `POST /missions` starts there by default | Wired and tested with a hand-written `meta.json`; never fed by a real capture |
| Fusion | `reconstruction/fast_path/fusion.py:fuse_frames` | Back-projects every valid pixel of every frame and keeps the first point per 2 cm voxel, so one frame's outlier survives |
| Main app | `MOBILE-APP` (`com.scanborn.ai`, Compose) | `AppNavigation.kt` (bottom nav + routes), `ToolsScreen.kt`, `SettingsScreen.kt`, DataStore `scanborn_prefs` (`ThemePreference.kt`), `AIRepository` singleton (only `ChatViewModel.onCleared` unloads it). No AR, no network code, no `INTERNET` permission |
| Phone | `docs/hardware/s24fe.md` | ARCore 1.56 installed; Depth API support must be checked at runtime; a loaded model holds ~1.15 GiB the system cannot reclaim |

**Correction to the master plan:** it says `ToolsScreen.kt`'s empty `onClick = {}` becomes "Scan a
space". The only empty handler there is the **Smart Notes** card, which has no feature behind it. That
card is replaced by "Scan a space" (decision 13).

## 2. Decisions

1. **One app.** Capture moves into `MOBILE-APP` under `com.scanborn.ai.scan`. `capture/android/` is
   deleted as the phase's last step, once the merged scan has worked on the S24 FE.
2. **Depth support first.** At the scan screen: `ArCoreApk.checkAvailability` (offer
   `requestInstall` when needed), then `session.isDepthModeSupported(RAW_DEPTH_ONLY)`, falling back to
   `AUTOMATIC` (both provide raw depth). Neither → the screen says the phone cannot scan with depth and
   points to the Scaniverse `.ply` import, which already works end to end. The mode used goes into
   `meta.json` (`depth_mode`).
3. **Raw depth with confidence.** `acquireRawDepthImage16Bits()` (16-bit little-endian millimetres, 0
   = no estimate) and `acquireRawDepthConfidenceImage()` (8-bit, 0–255). Keep a pixel when confidence
   ≥ 128 (ARCore's own guidance) and depth is in 0.25–4.0 m. Nothing is masked: the full 16 bits are
   depth. A raw image whose timestamp did not change carries no new estimate (ARCore updates raw depth
   ~10×/s) and is skipped. Recorded: `depth_source: "raw"`, `min_confidence`, `range_m`.
4. **Frame gates.** A frame is kept only while tracking is `TRACKING`, the phone turns slower than
   45°/s (from consecutive camera poses and frame timestamps), at most ~3 frames per second, and at
   least 5 % of its pixels survive decision 3. Every skip is counted by reason (`tracking`, `turning`,
   `stale`, `sparse`), shown on screen and recorded.
5. **Registration on the home marker, fixed at Start.** The screen says *stand over the home marker,
   point along its arrow, tap Start*. At the tap the camera pose defines the **home frame**: origin at
   the phone, +Z up (ARCore world +Y is gravity-up), +Y the phone's forward direction projected onto
   the floor plane, +X = Y × Z. Every frame's pose is expressed in it, so `meta.json` carries
   `home_pose: [0, 0, 1.5708]` (x, y and a heading of +Y — the master plan's value). Fixing the frame
   at the tap, not at session start, makes registration independent of when ARCore began tracking.
   Start is refused while the phone points nearly straight up or down (horizontal forward component
   < 0.5). The floor height is left to `reconstruction/floor.py`. The math is pure Kotlin
   (`HomeFrame.kt`) with JVM tests.
6. **Colour.** Each kept depth pixel takes the camera image's colour at the matching position
   (nearest sample, YUV→RGB), written as the optional `color` member fusion already reads.
7. **Fusion keeps what several frames saw.** A voxel survives when points from at least
   `min_views = 2` distinct frames fall in it (a frame hitting one voxel with many pixels counts
   once). A one-frame scan (the tests' synthetic room, a single capture) falls back to 1. The
   `POST /reconstruct` answer reports `min_views`, frames used and points dropped.
8. **Meta is validated and merged.** `POST /capture` validates `meta` (JSON object; `home_pose` if
   present must be 3 finite numbers; 400 naming the field otherwise) and **merges** it into
   `meta.json` rather than replacing it. `POST /capture/{id}/complete` accepts an optional
   `{"meta": {...}}` body, merged the same way, so the app can add counts at the end without losing
   `home_pose`.
9. **Uploads never silently drop frames.** Chunks upload serially and in order. A chunk that could
   not be sent stays queued and goes again before the next one; a chunk the orchestrator refuses
   (HTTP 4xx) is counted and dropped so it cannot block the rest; the chunk that creates the scan
   carries its meta, whichever index that is. A frame is about 130 KB at 160×120 with colour, so up
   to 300 (~40 MB) wait in memory while the network is gone and later ones are counted as dropped.
   Finish retries what is left five times with back-off, then closes the scan anyway and reports
   what could not be sent; `meta.json` records `frames`, `uploaded`, `failed`, `rejected`,
   `dropped_offline` and the skip counts.
10. **Orchestrator URL in Settings** (DataStore `orchestrator_url`) with a **Test connection** button
    calling `GET /health` (5 s timeout) and showing the answer. It replaces the capture app's
    build-time `BuildConfig.ORCHESTRATOR_URL`. Scanning is disabled until a URL has tested reachable.
11. **Cleartext on the LAN.** The orchestrator is plain HTTP on the laptop, so
    `network_security_config.xml` permits cleartext. Android cannot restrict that to private
    addresses; the app's only network use is the orchestrator URL the owner enters. Stated in the
    manifest comment and here.
12. **Memory before scanning.** Starting a scan calls `AIRepository.unload()`; the model reloads
    lazily on the next AI request (`AIRepository` gains an `ensureReady()` used before every generate),
    and `onTrimMemory(TRIM_MEMORY_BACKGROUND)` or above also unloads. This closes the Step 0 item.
13. **Where it lives in the app.** Route `scan` in `AppNavigation.kt`; the Tools screen's dead Smart
    Notes card becomes **Scan a space**; the screen is Compose, hosting a `GLSurfaceView` through
    `AndroidView` with a camera-background renderer so the operator sees what the phone sees.
    Portrait, screen kept on while scanning.
14. **Dependencies.** `com.google.ar:core:1.44.0` and `com.squareup.okhttp3:okhttp:4.12.0` in
    `libs.versions.toml`. Manifest: `INTERNET`; `android.hardware.camera.ar` with
    `required="false"` and ARCore meta-data `optional`, so the app still installs for its assistant
    features on phones without ARCore.
15. **Axis check.** `scripts/check_scan_axes.py SCAN_ID --walk-m 2.0` reads the scan's frame poses:
    the phone's path must run +Y by the walked distance (±0.15 m) with less than 0.15 m sideways, and
    the first pose must face +Y. It is tested on synthetic poses (labelled synthetic) until the real
    walk is recorded; the real poses (a few KB) then become `tests/fixtures/scan_axis_s24fe.json`.
16. **A printable home marker.** `docs/capture/home-marker.svg` — A4, crosshair, arrow, a 10 cm scale
    bar to check the print is 1:1. The same marker places the rover in Phase 6.

## 3. Steps

### 5.1 Orchestrator and reconstruction
- `fusion.py`: `min_views` (decision 7); `reconstruct.py` reports it.
- `service.py`: meta validation and merge on `/capture`; optional meta body on `/complete`
  (decision 8); `/reconstruct` returns the capture's `depth_source`, frame count and `home_pose` when
  present. OpenAPI re-exported.
- `scripts/check_scan_axes.py` (decision 15).
- **Tests:** a multi-frame synthetic scan with a one-frame outlier loses the outlier and keeps the
  surfaces; one-frame scans are unchanged; bad `home_pose` is refused with the field named; meta sent
  on the first chunk survives a later chunk and a `/complete` meta body; the axis check passes a
  synthetic 2 m walk and fails a sideways one.

### 5.2 Capture math (pure Kotlin, JVM unit tests)
- `scan/HomeFrame.kt` (decision 5), `scan/DepthFilter.kt` (decision 3: 16-bit LE decode with row
  stride, confidence, range, valid ratio), `scan/TurnRate.kt` (quaternion angle / dt),
  `scan/Frames.kt` (moved; intrinsics scaling; pose through the home frame), `scan/NpzWriter.kt`
  (moved; adds `color`), `scan/ColorSampler.kt` (YUV_420_888 → RGB at depth pixels).
- **Tests** (`app/src/test`): a depth value above 8.191 m survives decoding; stride padding is
  skipped; low confidence and out-of-range pixels are dropped; at Start the phone's forward maps to
  +Y whatever ARCore's yaw; a 2 m walk forward ends at +Y 2 m; a pitched-down Start is refused; the
  `.npy` header matches numpy's layout.

### 5.3 The app
- `scan/ScanSession.kt` (ARCore availability, config, depth support), `scan/BackgroundRenderer.kt`,
  `scan/Uploader.kt` (settings URL, retry queue), `scan/ScanViewModel.kt` (checking → ready →
  scanning → finishing → done | failed, with counts), `ui/screens/ScanScreen.kt`.
- `data/OrchestratorPreference.kt` + Settings section with Test connection.
- `AppNavigation.kt` route and Tools card; `AIRepository.ensureReady()` + `unload()` on scan start;
  `onTrimMemory` in `MainActivity`.
- Manifest, `network_security_config.xml`, dependencies.

### 5.4 Build
- `gradlew :app:testDebugUnitTest :app:assembleDebug` (with `JAVA_TOOL_OPTIONS` set, see START-HERE §8).
  Kotlin warnings do not grow (13 at Step 0).

### 5.5 On the phone (owner) and the laptop
1. Owner: print the marker (check the 10 cm bar), connect the phone by USB, confirm `adb` may install.
2. Install the debug APK; owner grants camera, enters the laptop URL, taps Test connection.
3. Owner scans a room from the marker; the laptop runs reconstruct → segment → twin; the twin's home
   pose must be `[0, 0, 1.5708]` and a mission from it must start on the marker.
4. Owner does the axis check walk (2 m, tape-measured); `check_scan_axes.py` must pass; its poses
   become the fixture.
5. Record: depth mode, frames kept and skipped by reason, valid-pixel share, fusion points dropped,
   axis error, reconstruct time, and Phase 2's open "on a real scan" numbers (plan time, grid size).
6. Delete `capture/android/`.
7. Settings → About shows the installed version ("1.0"), build "debug", engine "llama.cpp (CPU)" and
   model "Qwen2.5-1.5B-Instruct Q4_K_M" (added by Phase 7, decision 16; built, not yet seen on the
   phone).

### 5.6 Verification
- Full Python suite, lint, types; dashboard tests and build; Gradle unit tests and build.
- The master plan's Phase 5 checkboxes and a "Verified" block with the measured numbers.

## 4. Done when

(Master plan) a scan on the S24 FE produces a twin whose home pose matches the marker, and the axis
check passes.

**Not verifiable without the phone:** everything in 5.5. Steps 5.1–5.4 are done and checked on the
laptop first, so the phone session is short.

## 5. Progress (2026-09-15)

Steps 5.1–5.4 are built and pass on the laptop; 5.5 (the phone) and 5.6 are next.

| Check | Result |
|---|---|
| Python suite | 405 passed (was 387); flake8 clean; mypy clean (97 files) |
| New Python tests | `test_reconstruction.py` (multi-view fusion), `test_capture_meta.py` (validation, merge, home pose to the twin), `test_scan_axes.py` (synthetic walks) |
| App | `gradlew :app:testDebugUnitTest :app:assembleDebug` BUILD SUCCESSFUL; 26 JVM tests (HomeFrame 5, Frames 3, DepthFilter 3, TurnRate 3, NpzWriter 3, ColorSampler 3, UploadQueue 3, OrchestratorUrl 2, the stock example 1); Kotlin warnings 0 (were 13) |
| Dashboard | vitest 44 passed; build passes |

Where the build departed from §2:

| # | Change | Why |
|---|---|---|
| 1 | Decision 9's frame size is ~130 KB at 160×120 with colour, not 20–40 KB; the offline queue holds 300 frames (~40 MB) and counts the rest as dropped. Finish retries five times, then closes the scan and reports what it could not send, instead of asking | The first figure ignored colour and float32 depth; an interactive "finish without them?" adds a state the operator does not need when every count is recorded |
| 2 | A chunk the orchestrator refuses (HTTP 4xx) is counted as `rejected` and skipped; only unreachable chunks are retried | A permanently refused chunk would otherwise block every chunk after it |
| 3 | The scan screen observes the activity's lifecycle, not the navigation entry's | `LocalLifecycleOwner` is deprecated in this Compose version, and ARCore should pause exactly when the app leaves the screen; leaving the route is handled by `onDispose` |
| 4 | A pause during a scan ends capture ("tap Finish to keep what was captured") | ARCore may re-anchor its world on resume, so later frames could not be trusted to share the home frame |
| 5 | The Step 0 Kotlin warnings were fixed alongside (13 → 0) | The phase's rule was "warnings do not grow"; clearing them closed the Step 0 item at no risk |
| 6 | The orchestrator must listen on the network for the phone session (`uvicorn ... --host 0.0.0.0`), with Windows Firewall allowing port 8000 | The `.claude/launch.json` preview binds localhost only, which the phone cannot reach |
