# ScanBorn — Scan → Twin → Train → Drive

> Revised master plan, 2026-09-13. Supersedes `2026-09-07-scanborn-master-plan.md` for
> scope and ordering; that file stays as history. `- [ ]` is a task, `- [x]` is done.
> Estimates are rough single-developer days, not measurements.

---

## 1. The goal

Point the Galaxy S24 FE at a room. The laptop turns the scan into a digital twin with a
map of where the robot physically fits. You choose where the robot starts and where it
should go — by tapping the map, or by saying it. A driving policy is trained and tested in
a simulation of **that room**, using the **same motion model as the real robot**. Only a
policy that passes the simulation test is sent to a small wheeled robot, which drives the
route in the real room while the dashboard overlays the real drive on the simulated one.

Everything that exists today stays: environment profiles, the on-device Qwen model, voice,
chat / OCR / PDF / quiz / Circle Learn, rescan diff, live job feed, the landing page.

**Rules for this revision**

1. **Light compute.** The core loop needs no GPU and no language model. It runs on the
   laptop CPU in seconds. The GPU and the LLM are optional layers on top.
2. **Accuracy from better data handling, not bigger models.** A robust floor, unknown space
   treated as blocked, noise rejection, and a simulation that behaves like the robot.
3. **The simplest motion that transfers.** A differential-drive robot: turn toward the next
   waypoint, drive, keep correcting.
4. **Nothing on screen that was not measured** — or it is labelled as a fixture.

---

## 2. What was verified before writing this

| Finding | Why it matters |
|---|---|
| `twin/buggy_motor_controller.ino` drives an **Ackermann RC buggy** with `steer,throttle\n`; `robot/adapters/unoq.py` sends JSON `{"method":"set_wheel_speed",...}` | Python cannot talk to the only robot firmware in the repo |
| Simulation and policy are **holonomic**: action is a `(dx, dy)` step in any direction; `SimRobot.move` teleports | A policy trained this way cannot drive a real wheeled robot |
| `robot/policy_runner.py` integrates pose from its own commands; `UnoQRobot.move` only stores the target | No feedback: the "trace" is where the robot was *told* to go |
| Nothing links the scan's coordinate frame to where the robot physically stands | The robot cannot know where it is in the twin |
| `/train` and `/deploy` choose start (farthest free cell) and goal (first `navigate_to` target, else room centre) themselves | No way to set start and goal yourself |
| Navmesh marks never-observed cells **free**; one noise point blocks a cell; floor height is the **minimum** z | Scan holes (black, glossy, glass) become drivable; one outlier moves the floor |
| `semantic/service/inference.py:_nn_spacing` builds `(25, N, 3)` float64 blocks | ~300 MB per block at 500k points — real phone scans will crawl |
| Capture uses smoothed depth with `& 0x1FFF`, ignores confidence, sets depth mode without `isDepthModeSupported` | Hallucinated depth enters the twin; possible range wraparound; crash on unsupported devices |
| Dashboard: `api.js` has no `fetch`; `NLPPlayground` invents 9 verbs; `Telemetry` shows fabricated AI Hub numbers; `RobotViewer3D` renders a built-in rover with "Hexagon NPU Payload" and "Active LiDAR Sweep"; the demo writes 7 wrong state keys; the Deploy dropdown sends kinds that violate `CHECK (kind IN ('sim','unoq'))` | Phase 1 |
| The model is **Qwen2.5-1.5B-Instruct, Q4_K_M, 1.06 GB** (read from the GGUF header); the vendored llama.cpp can also load `qwen3`, `gemma3`, `gemma3n` | A model swap needs no re-vendoring |
| The app copies the 1.06 GB asset into internal storage on first launch | ~2.2 GB used on the phone |
| `MOBILE-APP` has no `INTERNET` permission and no cleartext-HTTP config | A merged scan feature could not reach the laptop |
| `setup_llama.ps1` pins tag `b4570` and copies root-level files; the vendored tree is newer and laid out in `src/` + `ggml/` | The script cannot reproduce what is vendored |
| Android SDK is complete at `D:\Android_SDK` (android-36, build-tools 36, NDK 28.2.13676358, CMake 3.22.1) | `MOBILE-APP/local.properties` created, gitignored |
| Gradle fails with `Unable to establish loopback connection` before compiling anything. A 10-line Java test isolated it: the JDK's Unix-domain-socket pipe fails inside `C:\Users\…\AppData\Local\Temp` (`SocketException: Invalid argument: connect`), and every Java NIO selector needs that pipe. With `jdk.net.unixdomain.tmpdir` on `D:`, `Pipe.open` and `Selector.open` succeed | Machine environment, not Kotlin — it will hit Android Studio and any terminal too. Fix in Step 0 |
| `arduino-cli` is installed but `Arduino15\packages` is a broken symlink: "No platforms installed" | ESP32 core must be reinstalled before firmware can compile |
| RTX 4050 with `torch 2.5.1+cu121` works; `scipy 1.15.2` is installed | GPU is optional; scipy gives cheap KD-trees and distance transforms |

---

## 3. System at a glance

```
 Galaxy S24 FE                 Laptop (Lenovo LOQ — CPU is enough)       Rover (ESP32)
 ─────────────────────         ──────────────────────────────────        ─────────────────────
 ARCore depth scan ──HTTP──►   orchestrator :8000                         HTTP :80
 Voice + Qwen 1.5B ──HTTP──►     reconstruct → twin → navmesh               mission + policy in
   → goal suggestion             mission (start, goal) → path       ──►     50 Hz loop:
 Mission screen                  simulation, training, sim gate             encoders + gyro → pose
 (tap goal, E-STOP)              deploy, telemetry relay            ◄──     UDP pose at 20 Hz
 dashboard in browser ◄───────   dashboard :5173                            heartbeat, E-STOP

           one private 2.4 GHz network (pocket router or phone hotspot) — no internet needed
```

| Device | Responsibilities | Compute |
|---|---|---|
| **S24 FE** | ARCore depth capture; voice and on-device Qwen ("go to the table" → goal suggestion); mission screen; can open the dashboard | CPU. ~1.5 GB RAM while the model generates |
| **Laptop** | Orchestrator; reconstruction; segmentation; navmesh; path planning; simulation; behaviour cloning; int8 export; dashboard | **CPU only on the core loop.** GPU only for optional Phase 10 |
| **Rover** | Stores the int8 policy; runs odometry and the control loop on the microcontroller; follows waypoints; reports pose | ESP32 dual-core |

---

## 4. The robot

### 4.1 Differential drive, not the RC buggy

- **It can turn in place**, so any path with corners is drivable. An RC car has a minimum
  turning radius, which makes tight indoor routes infeasible and forces a curvature-aware
  planner — heavier and much harder to get right.
- **Its motion model is two lines**: `v_left = v − ω·L/2`, `v_right = v + ω·L/2`. A linear
  policy over *(forward distance, heading error)* represents the controller exactly, so
  behaviour cloning fits it exactly.
- **Wheel encoders plus a gyro** give usable odometry across a room. An RC ESC has no speed
  feedback and a deadband that makes slow driving jerky.
- Common, cheap parts.

### 4.2 If you buy a ready-made kit, it must have

- 2 driven wheels + 1 caster (4-wheel skid-steer slips when turning and corrupts odometry)
- Quadrature encoders on **both** motors
- A gyro (MPU6050 or better)
- An ESP32 (2.4 GHz WiFi), or a controller that accepts JSON over WiFi
- Footprint ≤ 25 × 25 cm, so its radius (~0.18 m) is under the `generic` profile's 0.25 m
- ≥ 0.3 m/s top speed; ≥ 30 min battery
- For demos: a hard, flat floor. Carpet makes odometry drift.

### 4.3 Reference build

| # | Part | Specification | Note |
|---|---|---|---|
| 1 | Chassis | 2WD + ball caster, ~20 × 20 cm | room for the battery under the deck |
| 2 | Motors | 2 × JGB37-520 geared DC, 12 V, ~110–170 RPM, **with hall encoder** | 65 mm wheels → roughly 0.37–0.58 m/s at full speed |
| 3 | Wheels | 2 × 65 mm rubber, 6 mm D-shaft hub | |
| 4 | Motor driver | 2-channel, ≥ 2 A continuous, voltage range above 12.6 V (e.g. Cytron MDD3A, or 2 × DRV8871) | TB6612FNG (1.2 A) is marginal for these motors |
| 5 | Controller | ESP32 DevKit V1 (WROOM-32) | hardware pulse counters for encoders |
| 6 | IMU | MPU6050 breakout, I²C | mount flat, near the centre |
| 7 | Front range sensor (recommended) | VL53L0X ToF, I²C | stops the rover if something appears that was not in the scan |
| 8 | Battery | 3S Li-ion 18650 pack (11.1 V) + 3S BMS + holder | |
| 9 | Regulator | Buck converter to 5 V, ≥ 2 A | feeds ESP32 `VIN` |
| 10 | Kill switch | Rocker switch on battery positive | physical power cut |
| 11 | Button | Momentary push button | on-robot stop / start |
| 12 | Passives | 4 × 10 kΩ (encoder pull-ups), 100 kΩ + 33 kΩ (battery divider), wires, standoffs | |
| 13 | Home marker | A4 print: crosshair + arrow | defines the robot's start pose in the scan |

Budget guide: roughly ₹4,000–6,500 at common Indian electronics retailers — check current
prices before ordering.

**Electrical rules**

- Power the encoders from **3.3 V**. ESP32 pins are not 5 V tolerant, and many encoder
  boards output at their supply voltage.
- GPIO 34/35/36/39 are input-only with no internal pull-ups — fit the external 10 kΩ.
- Battery sensing must use an **ADC1** pin; ADC2 does not work while WiFi is on.
- One common ground for battery, driver, ESP32, and sensors.

### 4.4 Pin map (ESP32 DevKit V1)

| Function | GPIO | Reason |
|---|---|---|
| Left motor A / B (PWM) | 25 / 26 | safe outputs |
| Right motor A / B (PWM) | 27 / 19 | safe outputs; avoids 14/15, which pulse at boot |
| Left encoder A / B | 34 / 35 | input-only, external pull-ups |
| Right encoder A / B | 36 / 39 | input-only, external pull-ups |
| I²C SDA / SCL (IMU + ToF) | 21 / 22 | default I²C |
| Battery sense | 33 | ADC1 |
| Button | 23 | `INPUT_PULLUP` |
| Status LED | 2 | on-board LED |

Avoid 0, 5, 12, 15 (boot strapping) and 6–11 (flash).

### 4.5 Rover spec, measured not assumed

- [ ] New `robot/specs/rover.yaml`: `wheel_radius_m`, `wheelbase_m`, `counts_per_rev`,
      `footprint_radius_m`, `height_m`, `v_max` (demo cap 0.30), `w_max`, `a_max`,
      `alpha_max`. Values are placeholders until Phase 6 calibration writes them.
- [ ] `/deploy` refuses if the twin's `profile.robot_radius` is smaller than the rover's
      `footprint_radius_m` — a twin built for a smaller robot would plan paths this one
      cannot fit through.

### 4.6 The existing buggy

- [ ] Move `twin/buggy_motor_controller.ino`, `twin/inference.py` and `twin/Buggy.onnx` to
      `experiments/rc_buggy_snapdragon/`, with a README: it is a Snapdragon/QNN experiment,
      its 14-dimension observation space does not match the pipeline, and why it is not the
      target robot. Move `inference.py`'s module-scope serial port and QNN registration into
      `main()` so importing it has no side effects.

---

## 5. The on-device model

| Option | GGUF size (Q4_K_M) | Licence | Vendored llama.cpp | Prompt format | Verdict |
|---|---|---|---|---|---|
| **Qwen2.5-1.5B-Instruct** (current) | **1.06 GB** (measured) | Apache-2.0 | yes (`qwen2`) | ChatML — `PromptFormatter.kt` already matches | **Keep** |
| Qwen3-1.7B | ~1.1–1.3 GB | Apache-2.0 | yes (`qwen3`) | ChatML + thinking mode. Under a grammar it cannot emit `<think>`; free chat must turn thinking off | Only if the Phase 8 eval says so |
| Gemma 3 1B IT | ~0.8 GB | Gemma Terms of Use | yes (`gemma3`) | Different template → new formatter | Smaller, but check Hindi on the model card; more restrictive licence |
| Gemma 3n E2B | roughly 3 GB | Gemma Terms of Use | yes (`gemma3n`) | Different template | Too heavy next to ARCore on 8 GB |

**Keep Qwen2.5-1.5B-Instruct Q4_K_M.** It already loads and streams; the GBNF grammar makes
structured output valid by construction; the core scan-to-drive loop does not depend on
it; it is Apache-2.0; it handles Hindi.

**What it needs on the S24 FE (8 GB)**

- **RAM while generating ≈ 1.5 GB**: ~1.0 GB of weights (memory-mapped) + **117 MB KV cache**
  at `n_ctx = 4096` (28 layers × 2 KV heads × 128 dims × K and V × 2 bytes × 4096 tokens) +
  a few hundred MB of compute buffers.
- **Storage ≈ 2.3 GB** today: the 1.23 GB debug APK (measured) plus the 1.06 GB copy of the
  model extracted on first launch. Measured on the project's S24 FE on 2026-09-14
  (`docs/hardware/s24fe.md`): free space went from **5.2 GB to 1.9 GB (99 % full)** across the
  install and first launch. Storage, not RAM, is the binding constraint — at 99 % Android can
  refuse writes, which breaks the Room database, capture uploads and the next install. The
  one-copy packaging below is therefore a priority, not a nicety.
- Threads are derived from the big-core count (Phase 1.2 code); Step 0 measures 4 vs 6.
- Never run ARCore capture and generation at the same time.

**Packaging**

- [ ] Keep the model bundled in the default build — a self-contained APK.
- [ ] `ModelStorageManager.kt`: before extracting, look for
      `getExternalFilesDir("models")/qwen.gguf` and load it directly if present. A model can
      then be swapped with `adb push` and no rebuild, and a `lite` build flavour can skip the
      asset for fast iteration.
- [ ] Settings shows the model's name, architecture and quantisation (from the GGUF header),
      its size, and whether it is bundled or sideloaded.
- [ ] Record the SHA-256 of the model file in `MOBILE-APP/app/src/main/assets/models/README.md`
      with the exact source repository and filename, replacing "add your exact model link
      here".
- [ ] Rewrite `MOBILE-APP/setup_llama.ps1`: it cannot reproduce the vendored tree. Either
      document that llama.cpp is vendored and how to re-sync `src/`, `ggml/`, `common/`, or
      delete it.

---

## 6. Accuracy upgrades that cost almost nothing

| Stage | Today | Change | Cost |
|---|---|---|---|
| Capture | smoothed depth, `& 0x1FFF`, no confidence, no range limit | raw depth + confidence threshold, no bit mask, 0.25–4.0 m range, skip frames while tracking is lost or the phone turns fast | less data |
| Fusion | every pixel kept once per 2 cm voxel | keep voxels observed in ≥ 2 frames (removes flying pixels) | one `np.unique` |
| Floor | minimum z | peak of the height histogram near the bottom — ARCore is gravity-aligned, so the floor is flat in z | one histogram |
| Navmesh | unseen = free; one point blocks | three states — **free** (floor seen, no obstacle), **blocked** (≥ 3 points in the robot's height band), **unknown** (treated as blocked); fill 1-cell floor gaps | `np.bincount` + one morphological close |
| Segmentation | O(sample × N) spacing | downsample to 5 cm first; `scipy.spatial.cKDTree` for spacing | faster |
| Path | 8-way BFS where a diagonal costs the same as a straight step; hugs walls | A* with true diagonal cost + a clearance cost from a distance transform, then line-of-sight shortcutting to a few straight segments | milliseconds |
| Simulation | holonomic, teleporting, no noise | differential drive with speed, turn and acceleration limits; wheel slip; gyro bias; one-tick latency | milliseconds |
| Collision check | robot centre vs raw grid | robot centre vs grid grown by the exact footprint radius | same |
| Sim gate | 20 random starts, 60 % | 30 noisy runs of **this mission**, ≥ 90 % to deploy (60 % kept for the no-mission path) | under a second |
| Real drive | open loop | closed loop on encoders + gyro at 50 Hz, on the rover | on the MCU |
| Registration | none | the scan starts on the home marker, so the rover's start pose is known | none |

---

## 7. Phases

### Step 0 — Build, install, measure · 0.5 day

- [x] `MOBILE-APP/local.properties` → `sdk.dir=D\:\\Android_SDK` (gitignored). The backslash
      must be doubled: `D\:\Android_SDK` fails with "The filename, directory name, or volume
      label syntax is incorrect".
- [ ] **Fix Java on this laptop first.** Every JVM here fails to open an NIO selector
      (`Unable to establish loopback connection`) because the JDK's Unix-domain-socket pipe
      cannot connect inside `C:\Users\…\AppData\Local\Temp`; a directory on `D:` works (tested
      with `D:\Endeavors`). Create `D:\tmp`, set the **user** environment variable
      `JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=D:\tmp`, and open a new terminal. It is the
      only switch that reaches every JVM Gradle starts (launcher, daemon, Kotlin compiler); each
      Java start will print a harmless "Picked up JAVA_TOOL_OPTIONS" line. Verified: with it
      set, Gradle runs and the native llama.cpp code compiles.
- [x] Build: `cd MOBILE-APP` then `.\gradlew.bat assembleDebug`. **Built 2026-09-14** in 1 m 26 s
      once the fixes below were in; `app-debug.apk` is 1.23 GB because it bundles the model.
      Keep the laptop plugged in and awake — sleeping mid-build kills the Gradle daemon,
      reported only as "Gradle build daemon disappeared unexpectedly".
- [x] Fix whatever the first compile surfaces. Three errors in the never-compiled Phase
      1.4–1.5 Kotlin, all fixed: `LocalProfiler.kt` and `LocalTaskPlanner.kt` built
      `ChatMessage` without its required `id`; `SpeechController.kt`'s `init` block used
      `listener` before its declaration (Kotlin initialises in source order). Compiling cannot
      prove the JNI `onStats` callback matches its `GetMethodID` lookup — a mismatch silently
      no-ops at runtime — so the throughput rows in Settings are that check.
- [ ] Clear the 13 compiler warnings (none block the build): deprecated `Icons.Filled.*`
      (`Chat`, `ArrowBack`, `ArrowForward`, `ScreenShare`) → `Icons.AutoMirrored.Filled.*`;
      `LibraryDatabase.kt:30` `fallbackToDestructiveMigration()` → the overload that takes
      `dropAllTables`; two delicate-API uses at `LlamaEngine.kt:162` and `:167` — justify in a
      comment or replace.
- [x] Install: `adb install -r app\build\outputs\apk\debug\app-debug.apk`. Streamed install
      succeeded in 65 s on 2026-09-14.
- [x] First launch: cold start 1108 ms; model extracted in about 4 s and loaded in 2.6 s with
      `ctx=4096, threads=6 of 10 cores`; no crash (`docs/hardware/s24fe.md`).
- [ ] **Free phone storage.** Install plus model extraction took free space from 5.2 GB to
      1.9 GB (99 % full). Get back to at least 5 GB before any reinstall and before Phase 5
      capture, and do the one-copy model packaging from §5 (`ModelStorageManager.kt` loads a
      sideloaded `getExternalFilesDir("models")/qwen.gguf`; a `lite` build flavour leaves the
      asset out of the APK).
- [x] Write `docs/hardware/s24fe.md` from `adb shell`: `getprop ro.soc.model`,
      `head -1 /proc/meminfo`, each core's `cpuinfo_max_freq` (confirms the big-core count
      `LlamaEngine` derives), free space on `/data`, ARCore version.
- [x] Measure: send an ~800-character prompt; read prefill tok/s, decode tok/s and time to
      first token in Settings, at 4 and 6 threads. Save as `benchmarks/phone.json`.
      **Measured at 6 threads on 2026-09-14:** 331-token prompt, prefill 35.2 tok/s (first token
      after 9.4 s), decode 14.5 tok/s over 256 tokens (19.1 over the first 8). The `onStats` JNI
      callback fired, so its signature matches at runtime. The 4-thread run moves to the next
      item, since it needs a rebuild anyway.
- [ ] **Compile llama.cpp for this CPU.** No source gets `-march`/`-mcpu`, so ggml's
      dot-product and int8-matmul kernels — chosen at compile time — are compiled out, although
      the S24 FE reports `asimddp` and `i8mm` on all 10 cores. In
      `MOBILE-APP/app/src/main/cpp/CMakeLists.txt`, add `-march=armv8.2-a+dotprod+i8mm` to the
      `ggml_cpu`, `llama_core` and `scanborn_jni` targets. In `scanborn_jni.cpp`, have
      `loadModel` check `getauxval(AT_HWCAP) & HWCAP_ASIMDDP` and
      `getauxval(AT_HWCAP2) & HWCAP2_I8MM` first and return `false` with a log line naming the
      missing extension, so an older phone shows the load error instead of crashing with
      `SIGILL`. Leave SVE off unless a measurement shows it helps. Then repeat the same prompt at
      6 and 4 threads and update `benchmarks/phone.json`. Reinstalling needs free phone storage
      first.
- [ ] **Order the rover parts now** — delivery is the longest wait in this plan.
- [ ] Repair the Arduino toolchain: remove the broken `%LOCALAPPDATA%\Arduino15\packages`
      link and stale `Arduino15\tmp\package-*`, then
      `arduino-cli core update-index`, `arduino-cli core install esp32:esp32`,
      `arduino-cli lib install ArduinoJson ESP32Encoder "Adafruit MPU6050" VL53L0X`.

**Done when:** the APK runs on the S24 FE, throughput is recorded, parts are ordered,
`arduino-cli core list` shows `esp32:esp32`.

---

### Phase 1 — The dashboard tells the truth · 2 days

- [ ] **1.1 Real client** — rewrite `dashboard/src/api.js` against every orchestrator route
      (`vite.config.ts` already proxies them). Mirror `sdk/scanborn/client.py:ScanBornError`:
      throw with `status` and `detail`, so `Stage.jsx` renders the sim gate's 409 details.
      Uploads use `XMLHttpRequest` for progress. `health()` rejects when unreachable.
- [ ] **1.2 One state shape** — one `response → pipe` mapper per stage in `Console.jsx`;
      live runs and the fixture both pass through it. Ends the 7-key drift (`pointCount` vs
      `points`, `simRate` vs `successRate`, …) by construction.
- [ ] **1.3 Controls that match the backend** — robot kind becomes `sim` | `rover`; delete
      `trainEnvironment`, `rlOption`, `aiModelSource`, `qualcommModel`, `localModel`,
      `customRobotKind` (collected, never sent). Add a real **profile selector** that sends
      `environment` to `/generate-twin`.
- [ ] **1.4 Honest fixture** — move to `dashboard/src/fixtures.js`; button reads "Load
      offline fixture"; persistent banner while active; values are what this hardware returns
      (`backend: "local"`, `op_coverage: null`, `latency_source: "host-cpu"`,
      `compute_unit: "cpu"`).
- [ ] **1.5 NLPPlayground** — delete `parsePromptToGraph` and its invented verbs; call
      `POST /plan`; show the response's real `provider` and `on_device`; presets use the real
      six verbs; keep both Hindi presets; remove "Tokenizing on NPU tensor cores".
- [ ] **1.6 Telemetry** — tiers become *S24 FE (Exynos 2400e, CPU)*, *Laptop (i5-13450HX
      CPU; RTX 4050 optional)*, *Rover (ESP32)*. `benchmarks()` calls `GET /benchmarks`, so
      the existing "Not profiled yet" state shows. Add a "Measured here" section reading
      `benchmarks/phone.json` and the rover's control-loop timing.
- [ ] **1.7 RobotViewer3D** — remove the "Hexagon NPU Payload" / "Active LiDAR Sweep" stage
      copy, the LiDAR rays and camera frustum. Render the rover at its real size from
      `rover.yaml`, the scanned objects, the navmesh, the planned path, and the simulated and
      live traces. Keep the existing materials and lighting.
- [ ] **1.8 Dead code** — delete `dashboard/src/modify_console.js` (hardcoded
      `/Users/deepesh/...` path); remove `@paper-design/shaders` and
      `@paper-design/shaders-react` (zero imports); gitignore `dashboard/dist/`.
- [ ] **1.9 LAN access** — `server.host: true` in `vite.config.ts` so the phone can open the
      dashboard.
- [ ] **1.10 Tests** — add `vitest` (dev dependency) covering the error mapping in `api.js`
      and every stage mapper, including feeding the fixture through the mappers.

**Done when:** with the orchestrator running, all stages work from the browser; stopping the
orchestrator turns the status pill red within 5 s; a forced training failure shows the 409's
`sim_success_rate` and `required`; no `NPU`, `LiDAR`, `QAIRT` or `98.4` string remains in
`dashboard/src`.

---

### Phase 2 — Mission control: start, goal, path · 1.5 days

**Backend**

- [ ] New `twin/navmesh.py` (split from `twin/generator.py`): robust floor height,
      three-state grid (0 free, 1 blocked, 2 unknown), vectorised rasterisation, one-cell gap
      fill that never overwrites blocked cells. `is_blocked` / `nearest_free` keep their
      signatures (any non-zero cell is blocked), so existing callers still work.
- [ ] New `policy/planning.py` (moved out of `policy/evaluate.py`): `inflate`, `astar`
      (diagonal cost √2, clearance cost from `scipy.ndimage.distance_transform_edt`),
      `shortcut` (keeps a waypoint only where a straight line would cross an inflated cell),
      `plan_mission(navmesh, start, goal, via)` → waypoints, length, minimum clearance —
      or a named error (`start_blocked`, `goal_blocked`, `unreachable`) plus the nearest
      valid point.
- [ ] Database migration in `orchestrator/db.py` using `PRAGMA user_version`. Version 2 adds
      `missions` and `runs`, adds `policies.mission_id`, and widens `robots.kind` to include
      `rover`. SQLite cannot alter a `CHECK` constraint, so `robots` is rebuilt in a
      transaction. Test: open a version-1 database file and migrate it.
- [ ] Endpoints: `GET /twins/{id}/navmesh`, `POST /missions`, `GET /missions/{id}`;
      `POST /train` accepts `mission_id`.
- [ ] Stale missions: `/sync` marks a mission stale if any cell on its path changed state;
      `/deploy` refuses stale missions until retrained.
- [ ] Replace the hand-written, out-of-date `docs/api/openapi.yaml` with one generated from
      `app.openapi()` by `scripts/export_openapi.py`.
- [ ] SDK: `navmesh()`, `mission()`.

**Dashboard**

- [ ] `Floorplan.jsx` becomes `MissionMap.jsx`: grid layer (free / blocked / unknown plus the
      inflation band); click sets the goal; drag from the start sets start and heading;
      alt-click adds a via point; draws the planned path, simulated trace, real trace, and a
      live rover marker with heading. Keep the `place()` projection unchanged.
- [ ] Mission panel in `Console.jsx`: start (defaults to the scan's home pose), goal, via
      points, path length, minimum clearance; errors offer "use nearest valid point".

**Tests:** floor with injected outliers; three-state grid on a scan with a hole; A* keeps
clearance; shortcut never crosses an inflated cell; mission errors; migration; endpoints.

**Done when:** tapping a goal on a real scan shows a path in under a second, and a start or
goal cannot be placed on a blocked or unknown cell.

---

### Phase 3 — A simulation that behaves like the rover · 1.5 days

- [ ] `robot/spec.py` — a `RobotSpec` dataclass loaded from `robot/specs/rover.yaml`.
- [ ] `policy/sim.py` — `DiffDriveSim(navmesh, spec, noise, seed)`: `step(v, ω)` with speed,
      turn-rate and acceleration limits, wheel-slip noise, gyro bias, one-tick latency;
      collision = robot centre inside the grid grown by the footprint radius.
- [ ] `policy/controller.py` — `observe(pose, waypoints, index, lookahead)`: finds the
      lookahead point, then `obs = [d·cos(e), e]` where `d` is its distance and `e` the heading
      error. Demonstrator: `v = K_v·d·cos(e)`, `ω = K_ω·e`. The rover clamps `v` to
      `[0, v_max]`, so when the target is behind it `v` becomes 0 and it turns in place.
- [ ] `policy/evaluate.py` — demonstrations and rollouts run in `DiffDriveSim`. **Record the
      unclamped controller output as the label**; limits belong to the plant, so the linear
      fit is exact. `evaluate_mission` returns success rate, time, path deviation (RMS),
      minimum clearance, and the best trace. `MISSION_GATE = 0.9`.
- [ ] `policy/finetune/train_bc.py` — math unchanged; `obs_dim = act_dim = 2` retained; the
      export manifest records what the observation and action mean.
- [ ] `robot/policy_runner.py` and `robot/adapters/sim.py` drive `(v, ω)` through the
      simulator; `move()` stays for compatibility.
- [ ] `/train` returns metrics and the simulated trace; a 409 carries the same metrics.
- [ ] `scripts/gen_rover_golden.py` writes `tests/fixtures/rover_golden.json` (observation,
      action, int8 dequantisation, wheel speeds for sample poses) — the firmware self-test
      checks against it in Phase 4.

**Tests:** limits are never exceeded; seeded noise is reproducible; the controller turns in
place when the target is behind; behaviour cloning recovers the demonstrator's gains within
1 %; the gate passes on a furnished room; `test_policy.py`, `test_robot.py` and
`test_orchestrator.py` are updated rather than deleted.

---

### Phase 4 — Rover protocol, fake rover, firmware · 2.5 days (no hardware needed)

**Contract — `docs/robot/rover-protocol.md`.** Units: metres, radians, seconds; heading is
counter-clockwise from +X.

| Call | Payload |
|---|---|
| `GET /status` | firmware version, battery V, calibrated, state (`idle` / `running` / `done` / `estop` / `error`), pose, mission id |
| `POST /mission` | mission id, waypoints, start pose, int8 policy (weights, biases, scales), limits, lookahead, goal tolerance |
| `POST /start` · `POST /stop` · `POST /estop` · `POST /reset` | E-STOP latches until reset |
| `POST /calibrate` | `straight` (1 m) or `spin` (3 turns) |
| `POST /config` | counts per metre, wheelbase, PI gains — saved in flash |
| UDP telemetry, 20 Hz | time, x, y, θ, v, ω, state, waypoint index, battery |
| Heartbeat | from the orchestrator every 200 ms while running; the rover stops after 1 s without one |

**Python**

- [ ] `robot/adapters/rover.py` — `RoverRobot`: register, status, send mission, start, stop,
      E-STOP, heartbeat thread, UDP listener.
- [ ] `robot/adapters/fake_rover.py` — standard-library HTTP + UDP server speaking the same
      protocol with `DiffDriveSim` inside; `python -m robot.adapters.fake_rover --port 8081`.
- [ ] Registry adds `rover`.
- [ ] Endpoints: `POST /robots`, `GET /robots/{id}/status`, `POST /robots/{id}/estop`,
      `POST /robots/{id}/calibrate`, `POST /deployments/{id}/stop`. `/deploy` with
      `kind=rover` sends the mission and policy, starts it, relays telemetry to
      `WS /ws/deployments/{id}`, and stores a `runs` row with deviation from the simulated
      trace.
- [ ] `tests/test_rover_protocol.py` drives the orchestrator against the fake rover: mission
      accepted; bad mission rejected; E-STOP latches; losing the heartbeat stops it within 1 s;
      telemetry arrives; deploy refused when the rover is wider than the twin's profile.

**Firmware — `firmware/scanborn_rover/`**

- [ ] `scanborn_rover.ino` (setup, WiFi, HTTP server, state machine); `config.h` (pins and
      defaults); `rover_math.h` (pure functions: angle wrap, lookahead, observation, int8
      dequantisation, policy output, wheel speeds — no Arduino dependencies);
      `odometry.cpp` (ESP32Encoder + MPU6050 yaw); `motors.cpp` (PWM + per-wheel PI);
      `mission.cpp`; `secrets.example.h` committed, `secrets.h` gitignored.
- [ ] Behaviour: motors off at boot; mDNS name `scanborn-rover` and IP printed on serial;
      2-second gyro bias estimate while still; 50 Hz control, policy at 20 Hz; ToF stop under
      0.15 m; 1-second heartbeat deadman; battery cut-off at 9.9 V; button stops.
- [ ] `SELFTEST` build flag: runs the golden vectors (embedded by
      `scripts/gen_rover_golden.py` as `golden.h`) and prints PASS or FAIL.
- [ ] `nox -s firmware` → `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/scanborn_rover`.

**Done when:** dashboard → orchestrator → fake rover completes a full drive with a live
trace, with no hardware attached, and the firmware compiles.

---

### Phase 5 — Capture accuracy, registration, one app · 2 days

- [ ] **Depth guard** — check `session.isDepthModeSupported(Config.DepthMode.AUTOMATIC)`
      before configuring; if unsupported, say so and point to the Scaniverse `.ply` import,
      which already works end to end.
- [ ] **Raw depth + confidence** — `acquireRawDepthImage16Bits()` and
      `acquireRawDepthConfidenceImage()`; drop low-confidence pixels; remove `& 0x1FFF`
      (verify the 16-bit encoding against the ARCore docs while changing it); keep
      0.25–4.0 m; skip frames while tracking is lost or the phone turns faster than ~45°/s.
      Record `depth_source`, `min_confidence`, `range_m` in `meta.json`.
- [ ] **Fusion** — keep voxels seen in ≥ 2 frames (`reconstruction/fast_path/fusion.py`).
- [ ] **Registration** — the scan screen says: *stand over the home marker, point along the
      arrow, tap Start*. ARCore's origin is the phone's pose at that moment; `Frames.kt` maps
      it to +Y forward, Z up. `meta.json` records `home_pose: [0, 0, 1.5708]`; the twin carries
      it and missions start there by default.
- [ ] **Axis check** — walk 2 m straight ahead from the marker; the uploaded poses must show
      +Y ≈ 2 m. Put it in `tests` as a recorded scan fixture once captured.
- [ ] **Merge the capture app** into `MOBILE-APP` under `com.scanborn.ai.scan` (`Frames.kt`,
      `NpzWriter.kt`, `Uploader.kt`) with a Compose `ScanScreen.kt` hosting the
      `GLSurfaceView` through `AndroidView`. Add `com.google.ar:core:1.44.0` and
      `okhttp:4.12.0`. Manifest: `INTERNET`; `android.hardware.camera.ar` with
      `required="false"` and ARCore meta-data `optional`, so the app still installs for the
      assistant features; `network_security_config.xml` allowing cleartext on the LAN.
- [ ] Orchestrator URL and a **Test connection** button in Settings (DataStore), replacing the
      build-time `BuildConfig.ORCHESTRATOR_URL`.
- [ ] Navigation entry, and `ToolsScreen.kt`'s empty `onClick = {}` becomes "Scan a space".
- [ ] Remove `capture/android/` once the merged scan works on the S24 FE.

**Done when:** a scan on the S24 FE produces a twin whose home pose matches the marker, and
the axis check passes.

---

### Phase 6 — Hardware bring-up and the first real drive · 2 days (needs parts)

- [ ] Bench, wheels off the ground: `SELFTEST` passes; motor directions; encoder signs; gyro
      sign (counter-clockwise positive).
- [ ] Calibrate: drive a tape-measured 1.0 m → counts per metre; three 360° spins → effective
      wheelbase. Write `rover.yaml` and the rover's flash.
- [ ] Tune the wheel PI from a logged step response (dashboard chart over UDP).
- [ ] Odometry check: drive a 1 m square and back; target under 5 cm and 5° error.
- [ ] First mission in open floor; then a real scan with furniture.
- [ ] Five recorded runs: success, end error measured with a tape from the goal mark,
      deviation from the simulated trace.
- [ ] Put the rover's measured control-loop timing into Telemetry.

**Done when:** 4 of 5 runs stop within 10 cm of the goal (tape-measured) without contact.

---

### Phase 7 — Honesty pass · 1 day, any time

- [ ] `README.md`: remove the PPO badge, Unity ML-Agents, the `unity/` tree and setup,
      `mlagents-learn configs/ppo.yaml` (neither exists), Whisper, and NPU claims. Describe what
      runs: behaviour cloning of a controller in a differential-drive simulation, on-device
      Qwen, Android speech recognition and TTS.
- [ ] `reconstruction/fidelity_path/pipeline.py`: return a clean 501 instead of a dict that
      makes `service.py` raise `KeyError`.
- [ ] `docker/docker-compose.yml`: remove `CAPTURE_URL` / `SEMANTIC_URL` (read by nothing);
      `Dockerfile.orchestrator` copies only `orchestrator/` but imports eight sibling
      packages — copy the tree.
- [ ] `k8s/README.md` promises manifests that do not exist — remove or write them.
- [ ] `configs/default.yaml` is loaded by nothing and contradicts the code — delete or wire.
- [ ] `CLAUDE.md`: rewrite ("nearly every module is a stub" is false; its listed
      inconsistencies are fixed).
- [ ] `MOBILE-APP/README.md` still describes the old "Infinity" app and palette — rewrite.
- [ ] `detailed implementation doc.md`: add a banner that it describes the earlier Snapdragon
      architecture.

---

### Phase 8 — Voice and language on top of the loop · 2 days

- [ ] Phone **Mission screen**: fetch the navmesh; tap a goal; see the path; Train and Deploy
      buttons; live progress; a large E-STOP.
- [ ] Voice → `LocalTaskPlanner` → first `navigate_to` target → `POST /missions/suggest`
      (`twin_id`, `target_label`) → the goal appears on the map **for confirmation**. Voice
      never deploys on its own.
- [ ] Say it plainly in the UI: labels come from geometry, so "table" exists only if the
      segmenter called something a table. Tapping a goal always works.
- [ ] `datasets/eval/instructions.jsonl`: 40 English and Hindi instructions with expected
      task graphs, validated in CI by `schemas.validate_task_graph`. A debug screen runs them
      on the phone and exports results. Compare Qwen3-1.7B only if Qwen2.5-1.5B misses.
- [ ] **Planner prompt role.** `LlamaEngine` always calls `PromptFormatter.buildPrompt`, which
      injects the chat persona as the ChatML system turn, so `LocalTaskPlanner` and
      `LocalProfiler` send their task prompt as a *user* turn — while the Python planners send
      the same text in the system role. The grammar keeps output valid either way, but the eval
      and any distilled examples assume the system role. Add a system-prompt override to
      `buildPrompt` and `AIRepository.generate`, use it from both planners, and extend
      `tests/test_planner_parity.py` to assert the role.
- [ ] Optional: generate more examples at development time with Claude, Gemini or Groq;
      validate every one in CI; never call them at runtime.

---

### Phase 9 — One design system and a better UI · 3 days

- [ ] `design/tokens.json` lifted from the documented `:root` block in
      `dashboard/src/styles.css` (depth ramp, `--near-ink` / `--mint-ink` contrast pairs,
      `--hair: 1.5px`, both type ramps, motion tokens); `design/build-tokens.mjs` generates
      `dashboard/src/tokens.css` and `MOBILE-APP/.../ui/theme/Tokens.kt`.
- [ ] App theme onto the depth ramp with a dark variant; Archivo + JetBrains Mono; never use
      the graphic hues as small text (2.69:1 and 3.49:1 fail); `AiBodyOrb.kt` driven by real
      inference state.
- [ ] Charts in `dashboard/src/charts/`, SVG, no new dependency — each with an explicit empty
      state, never zeros: **SimVsReal** (trace overlay + deviation), **MissionMetrics**,
      **NavmeshDelta** (walkable area by profile), **TokenThroughput**, **WheelStep** (PI
      tuning). Compose Canvas versions for the phone.
- [ ] Motion through `useReveal.js` only (it already honours reduced motion); stage
      transitions from `Stage.jsx`'s derived states; animated `JobFeed` inserts.
- [ ] Texture: reuse `aperture.js`'s dithered halftone. Do not re-add post-processing to
      `dashboard/src/recon/pipeline.js` — its header documents why it was removed.
- [ ] **Finish the rebrand in the app.** The Infinity-era **∞** mark still ships: as text in
      `DashboardScreen.kt:72` (the Home header), `SplashScreen.kt:29`,
      `SettingsScreen.kt:93,188`, `ChatScreen.kt:520`, `VoiceScreen.kt:77`,
      `CircleLearnEntryScreen.kt:206,258` and `CircleLearnBottomSheet.kt:165`; drawn in
      `circle/FloatingBubbleView.kt:413`; in the Circle Learn notification
      (`ScanBornOverlayService.kt:450`); and in every launcher icon
      (`res/drawable/ic_launcher_foreground.xml`, `ic_launcher_legacy.xml`, all
      `res/mipmap-*/ic_launcher*.xml`). Replace with one `ScanBornMark` composable and a
      launcher icon built from the depth-ramp mark. Leave `ContentTypeDetector.kt:24` alone —
      there `∞` is a maths-formula signal, not branding.
- [ ] App placeholders: `ToolsScreen` "Smart Notes" (`onClick = {}`), Library entry detail,
      Settings "Storage" badge (checks a permission not in the manifest).

---

### Phase 10 — Optional accuracy tier

- [ ] **Phone rides the rover.** After scanning, mount the phone on the rover without ending
      the ARCore session and stream its pose over UDP at 15–30 Hz. The pose is already in the
      twin's frame, so there is no registration error and no odometry drift; the rover uses it
      when fresh (< 200 ms) and odometry in between. Costs: a phone mount, the phone is busy
      during the drive, and tracking from ~15 cm height must be verified.
- [ ] **YOLO-World labels** on the laptop GPU: RGB keyframes, labels voted onto the geometric
      clusters, `set_classes(profile.labels())`, `backend` reported honestly.
- [ ] **Open3D TSDF** (`tsdf_fusion` already exists, unused) — only if the navmesh needs
      smoother surfaces.

---

## 8. Checklists

**Network**

- [ ] One private **2.4 GHz** network: a pocket router, or the S24 FE hotspot set to 2.4 GHz.
      The ESP32 has no 5 GHz radio, and campus or venue WiFi often blocks device-to-device
      traffic.
- [ ] Laptop: `uvicorn orchestrator.service:app --host 0.0.0.0 --port 8000`; network profile
      set to Private; inbound firewall rules on the Private profile only for TCP 8000, TCP
      5173 and the rover telemetry UDP port.
- [ ] Phone: orchestrator URL in Settings; Test connection passes.
- [ ] Rover: WiFi credentials in `secrets.h`; IP shown on serial and in `/status`; reserve the
      IP on the router if possible.

**Safety**

- [ ] Physical kill switch; motors off at boot; 1-second heartbeat deadman; E-STOP on the
      dashboard (Space key), on the phone, and on the rover button.
- [ ] Demo speed cap 0.3 m/s; ToF stop; battery cut-off; drive on the floor only, with a clear
      area.

**Demo day**

- [ ] Charged rover battery, phone and power bank; printed home marker and tape; tape measure;
      USB cable; laptop charger.
- [ ] A real twin scanned earlier, labelled "recorded earlier", as the fallback. The offline
      fixture is the last resort and is labelled as one.

---

## 9. The demo, beat by beat

1. **Scan** — rover on the home marker. Hold the phone over it, tap Start, sweep the room for
   60–90 s, tap Finish.
2. **Twin** — frames arrive on the dashboard's live job feed; the map shows free, blocked and
   unknown space; the 3D view shows the scanned room.
3. **Profile** — switch *home* ↔ *warehouse*; walkable area visibly changes. Same scan,
   different robot.
4. **Mission** — the start is already the marker; tap a goal, or say "go to the table" and
   confirm the suggestion. The path appears.
5. **Train** — simulated runs animate over the twin; the gate shows e.g. 30 of 30 under noise.
6. **Drive** — the rover follows the route; the live trace draws over the simulated one; it
   stops at the goal; the deviation is shown. E-STOP visible throughout.

---

## 10. Milestones and ownership

| Milestone | Phases | Needs hardware | Estimate |
|---|---|---|---|
| M0 Unblocked | Step 0 | phone | 0.5 d |
| M1 Truthful dashboard | 1 | — | 2 d |
| M2 Missions in simulation | 2, 3 | — | 3 d |
| M3 Drive a fake rover | 4 | — | 2.5 d |
| M4 Real scans from the app | 5 | phone | 2 d |
| M5 Real drive | 6 | rover | 2 d |
| Honesty | 7 | — | 1 d, any time |
| Voice on top | 8 | phone | 2 d |
| Design and UI | 9 | — | 3 d |
| Optional accuracy | 10 | phone + rover | 2–3 d |

Core loop (M0–M5) ≈ 12 developer-days — about 5–6 calendar days for three people, plus
parts delivery, which is the real critical path.

| Person | Owns |
|---|---|
| Android | Step 0, Phase 5, Phase 8, phone side of Phase 9 |
| Robot | Parts, firmware (Phase 4), bring-up (Phase 6) |
| Backend + dashboard | Phases 1, 2, 3, Python side of 4, 7, web side of 9 |

Contracts to agree on day one, because two people build against each: `meta.json`'s
`home_pose` (Phase 5 → Phase 2) and `docs/robot/rover-protocol.md` (Phase 4 Python ↔
firmware).

---

## 11. Verification

| Check | Command | Bar |
|---|---|---|
| Python | `python -m pytest` | all pass (223 today, growing) |
| Lint | `nox -s lint` | flake8 zero findings, mypy clean |
| Dashboard | `cd dashboard; npm test; npm run build` | pass, exit 0 |
| App | `cd MOBILE-APP; .\gradlew.bat assembleDebug` | exit 0 |
| Firmware | `nox -s firmware` | compiles |
| Parity | `pytest tests/test_grammar_parity.py tests/test_planner_parity.py` | pass |
| Protocol | `pytest tests/test_rover_protocol.py` | pass against the fake rover |
| Migration | `pytest tests/test_db.py` | a version-1 database migrates |

**Definition of done for the core loop**

- The APK runs on the S24 FE; throughput is recorded in `benchmarks/phone.json`.
- A real room scanned with the phone becomes a twin with free, blocked and unknown space and
  a home pose.
- A tapped (or spoken and confirmed) goal becomes a mission with a short, clear path.
- Training passes the 90 % mission gate under noise.
- The rover reaches the goal within 10 cm (tape-measured) in at least 4 of 5 runs, without
  contact, and the dashboard shows real against simulated.
- No fabricated value remains in the dashboard; the fixture is clearly labelled.
- Every command in the table above passes.

---

## 12. Not doing

- **The RC buggy / Ackermann steering** — archived as an experiment (§4.6).
- **Unity** — no project exists; claims removed in Phase 7.
- **NPU paths** (QNN, QAIRT, AI Hub) — the S24 FE is Exynos; the export path keeps its honest
  `backend` label.
- **A 4B model server on the laptop** — dropped from the previous plan. The core loop needs no
  LLM, and the phone's 1.5B covers language.
- **Cloud models at runtime** — development-time dataset generation only.
- **SLAM or LiDAR on the rover**; avoiding moving obstacles beyond the ToF stop.

---

## 13. Decisions needed

1. **Robot hardware** — build the reference rover (§4.3), or reuse parts you already own
   (an ESP32? motors with encoders?).
2. **Deadline** — if there is a fixed date, Phases 8–10 are what get cut first.
