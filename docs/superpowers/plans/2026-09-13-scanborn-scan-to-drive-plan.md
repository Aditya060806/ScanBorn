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
| `arduino-cli` is installed but `Arduino15\packages` is a broken symlink: "No platforms installed" | ESP32 core must be reinstalled before firmware can compile — resolved 2026-09-15 with a separate data folder on D: (Step 0) |
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

- [x] Move `twin/buggy_motor_controller.ino`, `twin/inference.py` and `twin/Buggy.onnx` to
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
- [x] Record the SHA-256 of the model file in `MOBILE-APP/app/src/main/assets/models/README.md`
      with the exact source repository and filename, replacing "add your exact model link
      here".
- [x] Rewrite `MOBILE-APP/setup_llama.ps1`: it cannot reproduce the vendored tree. Either
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
- [x] **Fix Java on this laptop first.** Confirmed 2026-09-14: the user-level variable is set
      and a fresh JVM opens pipes and selectors with it. Every JVM here fails to open an NIO selector
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
- [x] **Free phone storage.** Install plus model extraction took free space from 5.2 GB to
      1.9 GB (99 % full). Freed to 8.4 GB on 2026-09-14; 7.2 GB after reinstalling. Keep several
      GB free before any reinstall and before Phase 5 capture.
- [ ] **One copy of the model on the phone.** The app still stores it twice (1.06 GB in the APK,
      1.06 GB extracted). `ModelStorageManager.kt` loads a sideloaded
      `getExternalFilesDir("models")/qwen.gguf` when present, and a `lite` build flavour leaves
      the asset out of the APK (§5).
- [x] Write `docs/hardware/s24fe.md` from `adb shell`: `getprop ro.soc.model`,
      `head -1 /proc/meminfo`, each core's `cpuinfo_max_freq` (confirms the big-core count
      `LlamaEngine` derives), free space on `/data`, ARCore version.
- [x] Measure: send an ~800-character prompt; read prefill tok/s, decode tok/s and time to
      first token in Settings, at 4 and 6 threads. Save as `benchmarks/phone.json`.
      **Measured at 6 threads on 2026-09-14:** 331-token prompt, prefill 35.2 tok/s (first token
      after 9.4 s), decode 14.5 tok/s over 256 tokens (19.1 over the first 8). The `onStats` JNI
      callback fired, so its signature matches at runtime. The 4-thread run moves to the next
      item, since it needs a rebuild anyway.
- [x] **Compile llama.cpp for this CPU.** Done 2026-09-14. `ggml_cpu`, where the kernels live,
      is compiled with `-march=armv8.2-a+dotprod+i8mm`; `scanborn_jni` stays baseline so its CPU
      check cannot itself use the new instructions. The library now contains 209 `sdot` and 36
      `smmla` instructions. `scanborn_jni.cpp` checks `HWCAP_ASIMDDP` and `HWCAP2_I8MM` before
      loading and logs `CPU features: dotprod + i8mm present` on the S24 FE. `LlamaEngine`
      reads `debug.scanborn.threads`, so thread counts compare without a rebuild. Same
      359-token prompt: **6 threads** — prefill 41.5 tok/s, decode 19.1 tok/s, 22.2 s total
      (baseline: 35.2 / 14.5 / 27.1 s); **4 threads** — 43.7 / 16.8 / 23.5 s. Six stays the
      default. Details in `docs/hardware/s24fe.md`.
- [x] **Enable ggml's CPU weight repacking.** Done 2026-09-14. `GGML_USE_CPU_REPACK` is now
      defined on `ggml_cpu`, and `scanborn_jni.cpp` sets `use_extra_bufts` from
      `debug.scanborn.repack` (unset = on, `0` = off), so one binary ran both modes on the same
      359-token prompt. The load log shows `CPU_REPACK model buffer size = 934.14 MiB` — every
      eligible Q4_K/Q6_K weight. **On vs off:** prefill 69.0 vs 46.8 tok/s, first token 5.3 vs
      7.7 s, total 19.0 vs 25.7 s; model load 2.8 vs 2.1 s. **Memory after a reply:** total PSS
      about the same (1.35 vs 1.33 GiB), but app-owned memory Android cannot reclaim rises from
      0.28 to 1.15 GiB. On by default. Details in `docs/hardware/s24fe.md`.
- [ ] **Release the model before scanning and under memory pressure.** With repacking on, about
      1.15 GiB of the loaded model is app-owned memory Android cannot drop, so ScanBorn is a
      larger target for the background killer and cannot share RAM with ARCore capture. Call
      `AIRepository.unload()` when capture starts and from `onTrimMemory` at
      `TRIM_MEMORY_BACKGROUND` or above, and reload lazily on the next AI request (a reload
      measured 2.8 s). *Built 2026-09-15 with Phase 5:* `AIRepository.release()` (stops a
      generation first) from the scan screen and `MainActivity.onTrimMemory`, and `generate()`
      loads the model again when needed. To check on the phone in the Phase 5 session.
- [x] **Kotlin warnings: 13 → 0** (2026-09-15). Deprecated icons moved to their AutoMirrored
      versions, Room's `fallbackToDestructiveMigration(true)`, and `LlamaEngine` no longer reads the
      delicate `isClosedForSend` (`close()` is idempotent). The Phase 5 code added none.
- [ ] **Order the rover parts now** — delivery is the longest wait in this plan.
- [x] Repair the Arduino toolchain. Done 2026-09-15 without touching the broken
      `%LOCALAPPDATA%\Arduino15` link, which belongs to the owner's C: offload setup: a separate
      data folder `D:/Arduino15` through `ARDUINO_DIRECTORIES_DATA`, `_DOWNLOADS`, `_USER` and the
      ESP32 board URL (`nox -s firmware` sets them from `SCANBORN_ARDUINO_DATA`). Installed: core
      `esp32:esp32` 3.3.11, ArduinoJson 7.4.3, ESP32Encoder 5.0.0, Adafruit MPU6050 2.2.9,
      VL53L0X 1.3.1.

**Done when:** the APK runs on the S24 FE, throughput is recorded, parts are ordered,
`arduino-cli core list` shows `esp32:esp32`.

---

### Phase 1 — The dashboard tells the truth · 2 days

- [x] **1.1 Real client** — `dashboard/src/api.js` calls every orchestrator route through the
      Vite proxy. `ApiError` mirrors `sdk/scanborn/client.py:ScanBornError`: an object `detail`
      (the sim gate's 409) survives whole for `Stage.jsx`, FastAPI's 422 lists flatten to their
      messages, and the dev proxy's empty 5xx says "is it running?". Uploads use
      `XMLHttpRequest` for byte progress; `health()` rejects when unreachable.
- [x] **1.2 One state shape** — `dashboard/src/pipeline.js` holds one `response → pipe`
      mapper per stage; live runs and the fixture both go through it.
- [x] **1.3 Controls that match the backend** — robot kind is `sim` | `unoq` until Phase 4
      adds the rover adapter (sending `rover` today would fail the `robots` CHECK constraint).
      The six decorative selects are gone; a **profile selector** sends `environment` to
      `/generate-twin`; the device label is optional.
- [x] **1.4 Honest fixture** — stronger than planned: `dashboard/src/fixtures.json` is recorded
      from the real orchestrator by `scripts/record_dashboard_fixture.py` (synthetic test room)
      and replayed through the same mappers. "Load offline fixture" shows a persistent banner
      and locks every stage until "New run". Re-record whenever a response shape changes.
- [x] **1.5 NLPPlayground** — calls a new **`POST /plan/preview`** (same planner, no twin,
      nothing stored), since `/plan` needs a scanned room. Shows the real `provider` and
      `on_device`; presets use the six real verbs; the Hindi preset says plainly that the
      offline planner reads English only.
- [x] **1.6 Telemetry** — real tiers; `GET /benchmarks` now also returns `phone` from
      `benchmarks/phone.json`, rendered as the measured run table. Rover control-loop timing
      joins it in Phase 6.
- [x] **1.7 RobotViewer3D** — rebuilt: scanned objects, the driven route, the rover at the
      reference size (constants until `robot/specs/rover.yaml`), and the profile's clearance
      ring. Also fixed a React state update on every frame and a full scene rebuild on
      "Rotate". The navmesh layer and planned path follow in Phase 2, which adds
      `GET /twins/{id}/navmesh`.
- [x] **1.8 Dead code** — `modify_console.js` deleted; `@paper-design/shaders*` removed.
      `dashboard/dist/` was already ignored by the root `.gitignore`.
- [x] **1.9 LAN access** — **opt-in** (`DASHBOARD_LAN=1`) rather than always on: the Vite 5
      dev server has open advisories that let a client read files through it.
- [x] **1.10 Tests** — 15 vitest tests (`api.test.js`, `pipeline.test.js`, including the fixture
      through the mappers); 7 pytest tests for the new endpoints (`tests/test_dashboard_api.py`).
- [x] **1.11 Upgrade Vite 5 → 8 and Vitest 2 → 5.** Done 2026-09-14: Vite 8.3.0, Vitest 5.0.0,
      `@vitejs/plugin-react` 6.1.1 — `npm audit` 0 vulnerabilities (was 5), the CJS warning is
      gone, `npm test` and `npm run build` pass, and the dev server was checked in the browser.
- [x] **1.12 Route legibility in 3D.** WebGL lines are 1 px, so the route barely read (351
      route-coloured pixels in a 592 × 597 frame). Now both routes are tubes whose width scales
      with the room (planned size/160, driven size/300, drawn above it so the planned route shows
      either side), the navmesh is the floor texture, and object fills are stronger (0.45, walls
      0.3). Measured on the offline fixture's 765 × 597 frame, same scene before and after the
      width change: planned route 480 → 805 px, driven 275 → 468 px. The pane was hidden, so the
      frame was read by remounting the viewer and reading pixels before compositing.

**Verified 2026-09-14** in the browser against the live orchestrator, on the tests' synthetic
room seeded through the API:

- [x] All stages run from the browser; every call returned 200, and Rescan found the removed
      table (576 voxels added, 324 removed).
- [x] Stopping the orchestrator turned the status pill to "orchestrator unreachable" within one
      5-second health check.
- [x] No hardware claim strings remain in `dashboard/src` or `index.html` (grep for NPU,
      Hexagon, LiDAR, QAIRT, 98.4, MuJoCo, Unity, Snapdragon, Gemma, Sarvam).
- [x] A training failure's 409 in the browser — seen 2026-09-14 once the rover model made it
      forceable: a 5 m mission is refused and Stage.jsx renders every field of the detail (see
      the Phase 3 "Verified" block).

---

### Phase 2 — Mission control: start, goal, path · 1.5 days

Detailed, code-grounded plan with decisions, API shapes and per-step tests:
`docs/superpowers/plans/2026-09-14-phase-2-missions.md`. Checkboxes below are ticked from it.

**Backend**

- [x] New `twin/navmesh.py` (split from `twin/generator.py`): three-state grid (0 free,
      1 blocked, 2 unknown) from three `np.bincount`s — blocked at ≥ 3 points in the sweep band
      **or ≥ 3 points more than 10 cm below the floor** (a step down); gap fill closes gaps one
      cell wide and never overwrites blocked cells. The floor comes from the new
      `reconstruction/floor.py` (densest low 2 cm height bin), which **segmentation now shares**
      — its min-z floor had the same outlier problem. `is_blocked` / `nearest_free` keep their
      signatures; `nearest_free` is vectorised.
- [x] New `policy/planning.py`: `clearance_field` (map edge counts as an obstacle), `inflate`,
      `astar` (√2 diagonals, no corner cutting, cost ×(1 + 4·shortfall) inside radius + 0.30 m),
      `shortcut` (a straight line replaces a stretch only if it stays traversable **and** no
      closer to obstacles than A* was there), `plan_mission` → sparse waypoints, dense path,
      length, minimum clearance, or a `MissionError` (`start_blocked`, `via_blocked`,
      `goal_blocked`, `unreachable`) with the nearest valid point. `evaluate.py` re-exports
      `inflate` / `plan_path`; evaluation no longer starts episodes inside the goal tolerance
      (that scored a do-nothing policy at 5 %).
- [x] Database migration in `orchestrator/db.py` using `PRAGMA user_version`. Version 2 adds
      `missions` and `runs` (`kind` includes `unoq`, so a UNO Q deploy can be recorded), adds
      `policies.mission_id`, and rebuilds `robots` to allow `rover`, with foreign keys checked
      before commit. Tested on a version-1 file; the real dev database migrated on first start.
- [x] Endpoints: `GET /twins/{id}/navmesh` (base64 cells + inflation band + home pose),
      `POST /missions` (goal, or `task_graph_id` to take the goal from a plan; 422 with `code`
      and `nearest`; `no_start` when there is neither a start nor a home pose),
      `GET /missions/{id}`; `POST /train` accepts `mission_id` and must also drive the mission
      route (`mission_reached`, `runs` row); `/deploy` drives the mission route; unknown robot
      kinds are a 400.
- [x] Stale missions — **rule changed while building**: paths keep more than the robot's radius
      from obstacles, so "a corridor cell changed" could never catch a new obstacle. `/sync`
      rasterises the rescan onto the twin's grid (unseen cells keep their old state) and marks a
      mission stale when its path no longer clears the robot's radius. A removed obstacle never
      does. `/train` and `/deploy` refuse stale missions; the fix is a twin from the new scan
      and a new mission.
- [x] `docs/api/openapi.yaml` is generated from `app.openapi()` by `scripts/export_openapi.py`
      (20 paths); `tests/test_openapi.py` fails when a route changes without re-exporting.
- [x] SDK: `navmesh()`, `mission()`, `get_mission()`, `train(mission_id=)`,
      `generate_twin(environment=)`, `run_pipeline(goal=, start=)`, `decode_cells()`.

**Dashboard**

- [x] `Floorplan.jsx` is replaced by `MissionMap.jsx` (+ pure, tested helpers in `navmesh.js`):
      occupancy image (free / too close for the robot / blocked / hatched never-scanned); click
      sets the goal; drag the start marker to move it, drag its arrow to aim it; Alt-click adds
      a via point, clicking one removes it; a blocked, unknown or too-close point is refused on
      the page with the planner's own reason; draws the planned route, simulated trace and
      real trace (empty until Phase 4). The **live rover marker** needs streamed telemetry, so
      it arrives with Phase 4's `WS /ws/deployments/{id}`. `place()` is unchanged.
- [x] Mission stage (06) in `Console.jsx`: start (home pose, else where the robot fits nearest
      the room centre), goal, heading, via list, "Goal from the plan", path length, clearance,
      planning time; a 422 offers "Use nearest valid point". Train sends `mission_id`; Rescan
      reports stale missions; the summary bar shows the mission. `RobotViewer3D` draws the
      navmesh as the floor and the planned and driven routes as tubes.

**Tests:** floor with injected outliers; three-state grid on a scan with a hole; A* keeps
clearance; shortcut never crosses an inflated cell; mission errors; migration; endpoints.

**Done when:** tapping a goal on a real scan shows a path in under a second, and a start or
goal cannot be placed on a blocked or unknown cell.

**Verified 2026-09-14.** Automated: pytest **284 passed**, vitest **28 passed**, flake8 and mypy
clean (78 files), `npm run build` passes, `benchmarks/planning.json` recorded (cold plan p95
8.0 ms at 10 cm, 27.4 ms at 5 cm, on a 698k-point synthetic room). In the browser against the
live orchestrator, on the tests' synthetic room seeded through the API, home profile:

- [x] The real dev database (`data/scanborn.db`, version 0) migrated to version 2 on first
      start: `robots` accepts `rover`, foreign-key check clean.
- [x] Navmesh at 10 cm: 2,252 cells free, 130 blocked, 118 never scanned; the map shows them with
      the chair and table, and a start placed where the robot fits.
- [x] Clicking the table is refused on the page ("That goal is on an obstacle.") with no request.
      Clicking open floor draws a route: 49–274 ms end to end over five plans, 8–64 ms of it
      planning.
- [x] Alt-click added a via point and dragging moved the start; the route re-planned around the
      chair through the via point (8.61 m, clearance 0.42 m, 6 waypoints). A typed 90° heading
      set the arrow.
- [x] A typed goal on the table → 422 `goal_blocked`, nearest (0.15, −0.81); "Use nearest valid
      point" planned it.
- [x] Train on the mission: 100 %, "mission route driven". Deploy: running, 42 poses, goal
      reached; the simulated trace starts at the mission start.
- [x] Rescan with a box on the route (seeded through the API): the current mission and 4 earlier
      test missions went stale; map, stage and summary say so; Deploy → 409 `mission_stale`
      with the reason.
- [x] Offline fixture: banner, locked map, 3.765 m route with 4 waypoints, 22-pose trace.
- [x] The 3D layers by pixel count (checked after the Phase 2 check, once the viewer drew its
      first frame on mount): on the fixture's 765 × 597 frame, free floor 90,178 px, too-close
      band 39,111, blocked 9,244, planned route 480, driven route 275 — every layer drawn.
- [ ] **"On a real scan"** — no real scan exists yet. Open until M4, or until a Scaniverse
      export of a real room is imported.

---

### Phase 3 — A simulation that behaves like the rover · 1.5 days

Detailed plan, decisions and measurements: `docs/superpowers/plans/2026-09-14-phase-3-diff-drive.md`.

- [x] `robot/spec.py` — a validated `RobotSpec` loaded from `robot/specs/rover.yaml`; every value
      is marked `source: placeholder` and the noise `source: assumed` until Phase 6 calibration.
      Served at `GET /robot-specs/{name}`; the 3D viewer sizes the rover from it (§4.5 item 1).
- [x] `policy/sim.py` — `DiffDriveSim(navmesh, spec, noise, seed)`: `step(v, ω)` with speed,
      turn-rate and acceleration limits, wheel slip, gyro bias, one-tick latency and placement
      error; it keeps a **true** pose and the **estimated** pose the rover can know (encoders +
      gyro), and the controller only sees the estimate. Collision = true centre within the
      footprint radius of anything not free.
- [x] `policy/controller.py` — pure pursuit, `obs = [d·cos(e), e]`, demonstrator
      `v = K_v·d·cos(e)`, `ω = K_ω·e`; the lookahead slides to the nearest path point ahead
      first (a rover part way along no longer steers back to the start). Stops when its
      estimate is within 2 cm of the goal (measured against 5 cm).
- [x] `policy/evaluate.py` — demonstrations and rollouts run in `DiffDriveSim`; labels are the
      unclamped controller output. `evaluate_mission`: 30 noisy runs, `MISSION_GATE = 0.9`,
      outcome counts (reached / stopped off goal / collided / timeout), median time, RMS
      deviation, median miss and drift, closest approach, and the **median** run's trace.
- [x] `policy/finetune/train_bc.py` — math unchanged; the export manifest records the
      observation, action, controller constants and spec, and `/deploy` refuses artifacts
      without them (`policy_outdated`).
- [x] `robot/policy_runner.py` and `robot/adapters/sim.py` drive `(v, ω)` through the simulator
      with the same loop as the gates; `move()` stays; adapters without velocity control are
      refused with a reason.
- [x] `/train` returns metrics and the median simulated trace; a 409 carries the numbers,
      including how failed runs ended. `/train` and `/deploy` refuse a rover wider than the
      twin's profile (§4.5 item 2). Each policy now gets its own checkpoint folder (a second
      training on one twin used to overwrite the first).
- [x] `scripts/gen_rover_golden.py` writes `tests/fixtures/rover_golden.json` (lookahead index,
      observation, int8 policy output, limited command, wheel speeds and rates, encoder counts
      per tick for 7 sample poses); `tests/test_rover_golden.py` pins it for Phase 4's firmware.
- [x] Floor band lowered to 5 cm (the rover climbs ~2 cm); the 2–5 cm gap is on the §8 checklist.

**Finding — dead reckoning limits route length.** With the assumed noise the ideal controller
reaches a 2.5 m route's goal within 10 cm in 30/30 runs, 3.7 m in 25/30, 4.6 m in 23/30. The 90 %
gate therefore passes routes up to about 3 m, as the real rover would. Demo routes stay near 3 m
(§8); longer ones need the Phase 10 position fix.

**Verified 2026-09-14.** Automated: pytest **339 passed**, vitest passes, flake8 and mypy clean
(86 files), `npm run build` passes, OpenAPI re-exported (21 paths), golden fixture written (7
rows). In the browser against the live orchestrator on the seeded synthetic room, home profile:

- [x] Stage 04: "Navmesh at 10 cm (too sparse for 5 cm cells: 6 floor points per 10 cm cell)".
- [x] A 2.55 m mission typed into the fields → Train: "30/30 runs reached · 2 cm RMS off route ·
      closest 0.5 m · noise assumed"; summary "SIM GATE: 30/30 runs ≥ 90%"; the median simulated
      trace starts within 1 cm of the start.
- [x] Optimize (local int8) → Deploy: running, 208 poses, goal reached, the trace ends 2.7 cm from
      the goal, 0.0016 ms per control tick (host CPU, int8).
- [x] 3D view by pixel count (765 × 597): too-close band 39,169 px, blocked 9,251, planned route
      356, driven route 373, rover body 346; HUD "rover 21 × 20 cm · spec placeholder".
- [x] A 5.09 m mission → Train refused, HTTP 409 in 175 ms: "policy failed the mission gate in
      simulation" — 16/30 reached, 14 stopped off goal, 0 collided, median miss 9.3 cm, median
      drift 9.0 cm, noise assumed. This is decision 14's drift limit, seen end to end.
- [x] That check exposed a console bug: the Train stage kept the previous route's "30/30" beside
      the new 409, and Optimize/Deploy still offered the old policy. Starting a stage now clears
      its output, its errors and everything downstream (`dashboard/src/pipeline.js:clearFrom`,
      `invalidatedBy`; vitest 33 passed). Re-checked in the browser: re-planning left Train empty
      with no error and Optimize/Deploy locked.

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

Detailed plan and deviations: `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md`
(decisions 1–15, §5 outcome).

**Python**

- [x] `robot/adapters/rover.py` — `RoverRobot`: status, send mission, start, stop, E-STOP, reset,
      calibrate, config, heartbeat thread. Telemetry is received by the orchestrator's session
      layer (`orchestrator/deployments.py`, one UDP socket for every drive), not by the adapter.
- [x] `robot/adapters/fake_rover.py` — standard-library HTTP + UDP server speaking the same
      protocol with `DiffDriveSim` inside; `python -m robot.adapters.fake_rover --port 8081`.
- [x] Registry adds `rover`; `robot/protocol.py` holds the payload validation both sides share.
- [x] Endpoints: `POST /robots` (`rover` with an address, or `fake_rover` run in-process),
      `GET /robots/{id}/status`, `POST /robots/{id}/estop`, `/reset`, `/calibrate`,
      `GET /deployments/{id}`, `POST /deployments/{id}/stop`, `WS /ws/deployments/{id}`. `/deploy`
      to a rover sends the mission and int8 policy, starts it, answers `running` at once, and when
      the drive ends stores a `runs` row with the deviation from the simulated trace.
- [x] Tests: `tests/test_protocol.py`; `test_rover_protocol.py` (the fake rover over real sockets:
      mission accepted, bad mission named, E-STOP latches, heartbeat loss stops it within
      1.0–1.4 s, telemetry arrives, collision); `test_rover_adapter.py`; `test_deployments.py`;
      `test_rover_deploy.py` (the orchestrator against in-process fake rovers: a live drive to
      `done` with its `runs` row, mid-drive E-STOP, operator stop, an unreachable rover, deploy
      refused when the rover is wider than the twin's profile).

**Firmware — `firmware/scanborn_rover/`**

- [x] `scanborn_rover.ino`; `config.h` (pins and limits); `rover_math.h` (angle wrap, lookahead,
      observation, int8 policy, limits, rate limits, wheel speeds, counts, pose update — no Arduino
      dependencies); `odometry.cpp`, `motors.cpp`, `mission.cpp` (validation and state machine),
      `net.cpp`, `safety.cpp`, `settings.cpp`, `calibrate.cpp`; `secrets.example.h` committed,
      `secrets.h` gitignored.
- [x] Behaviour: motors off at boot; mDNS name `scanborn-rover` and IP printed on serial;
      2-second gyro bias estimate while still; odometry, wheel PI and safety at 50 Hz, policy at
      20 Hz; ToF stop under 0.15 m; 1-second heartbeat deadman; battery cut-off at 9.9 V; button
      stops. Written and compiled; none of it has run on a board yet (Phase 6).
- [x] `SELFTEST` build flag: runs the golden vectors (`golden.h`, generated by
      `scripts/gen_rover_golden.py`) and prints PASS or FAIL. The same checks run on the laptop
      with `nox -s firmware_host`.
- [x] `nox -s firmware` → `arduino-cli compile --fqbn esp32:esp32:esp32`, driving and `SELFTEST`
      builds.

**Console:** robot choice sim / fake rover / ESP32 (address, Register); a rover deploy opens the
live view — the map draws the reported trace and the rover, the 3D view moves the rover; a sticky
live bar with state, reason, **Stop** and **E-STOP** (also Space); the result row shows reached,
deviation from the simulated run, the rover's own reported miss and, for the fake rover only, the
true miss.

**Done when:** dashboard → orchestrator → fake rover completes a full drive with a live
trace, with no hardware attached, and the firmware compiles.

**Verified 2026-09-15.** Automated: pytest **387 passed**, flake8 and mypy clean (95 files),
vitest **44 passed**, `npm run build` passes, `npm audit` 0 vulnerabilities, OpenAPI re-exported
(28 paths). `nox -s firmware_host` (MinGW g++ 6.3): **PASS** — 7 golden rows and 40 odometry
ticks, 0 failed checks. `nox -s firmware`: driving build 1,070,055 bytes (81 % of flash) and
61,244 bytes of RAM (18 %); `SELFTEST` build 499,511 bytes (38 %) and 39,920 bytes (12 %). In the
browser against the live orchestrator on the seeded synthetic room, home profile:

- [x] A 2.55 m mission → Train "30/30 runs reached · 2 cm RMS off route" → Optimize → Register
      fake rover ("fake rover · firmware fake-0.4.0 · idle") → Deploy.
- [x] Live: the bar read "Rover driving · N reported poses · FAKE ROVER · Stop · E-STOP", the
      map's violet trace grew frame by frame with the rover marker on it, and Deploy was locked.
- [x] Five drives ended `done`: "goal reached · 2 cm RMS from the simulated run · stopped 2 cm
      from the goal by its own estimate"; true miss 5, 3, 1, 5 and 3 cm (fake rover only); about
      170 reported poses each.
- [x] E-STOP mid-drive, by a Space keydown and by the button: the bar showed "E-STOP latched ·
      reason: dashboard" about 0.1 s later; the rover's status read `estop (dashboard)`; a deploy to
      the latched rover was refused "rover answered 409: not allowed in state 'estop'"
      (`rover_refused`); Reset returned it to `idle`. A deploy while driving: 409 `robot_busy`.
- [x] Found and fixed by this check: a WebGL context that would not start unmounted the whole
      console, E-STOP included (the 3D view now says it is unavailable, and each view sits in an
      error boundary); the Space handler read only `event.code`; a refused second deploy erased
      the live drive's record (Deploy is now locked while a rover drives); a nested refusal
      rendered as "[object Object]".
- [x] **Closed later on 2026-09-15:** the three items this block first left open.
      - *The live rover in 3D.* `scripts/check_live_drive.py` drives the console in headless
        Chrome over its DevTools protocol, on the laptop's GPU (ANGLE, NVIDIA RTX 4050,
        Direct3D 11): 13/13 checks. During a drive the 3D view's live trace grew from 63 to 191
        violet pixels and the rover's 379 pixels moved (centroid x 209 → 241 px) on the same canvas
        throughout — no scene rebuild, and none when the drive started either (a rover deploy used
        to hand the viewer a new empty trace array; fixed in `pipeline.js`).
      - *Space and the button as real input.* Trusted events through Chrome's input pipeline
        (`Input.dispatchKeyEvent`, `Input.dispatchMouseEvent`; the page saw `isTrusted: true`):
        Space latched E-STOP 43 ms after the key, the button 63 ms after the click; the latched
        rover refused a deploy; reset returned it to idle; no page errors. A finger on a physical
        keyboard takes the same path from the browser's input pipeline on.
      - *On an ESP32.* The `SELFTEST` build boots on Espressif's ESP32 emulator (QEMU
        esp-develop-9.2.2-20260417) and prints "7 golden rows, 40 odometry ticks, 0 failed checks"
        and PASS: the golden vectors hold on the ESP32's own compiler, libraries and CPU model
        (`nox -s firmware_qemu`). It needed DIO flash mode — QIO fails in the emulator's flash
        initialisation — so both builds now use DIO. The driving image boots there too: banner,
        flash settings, "IMU (MPU6050) not found" and "ToF (VL53L0X) not found: the rover will
        refuse to drive", then stops in WiFi's radio start-up, which the emulator cannot provide.
        That boot exposed and fixed two things: ESP-IDF's pulse-counter driver logged 8 pull-up
        errors on the input-only encoder pins (harmless, now silenced for the attach), and the
        firmware printed a gyro bias of 0 when no IMU had measured one (it now says "not
        measured"). Motors, sensors and WiFi remain Phase 6.

---

### Phase 5 — Capture accuracy, registration, one app · 2 days

Detailed plan: `docs/superpowers/plans/2026-09-15-phase-5-capture.md` (decisions 1–16). Items
marked *built* compile into the app and pass their laptop tests; each is ticked only once it has
worked on the S24 FE (step 5.5).

- [ ] **Depth guard** — check `session.isDepthModeSupported(Config.DepthMode.AUTOMATIC)`
      before configuring; if unsupported, say so and point to the Scaniverse `.ply` import,
      which already works end to end. *Built:* `ScanController` tries `RAW_DEPTH_ONLY`, then
      `AUTOMATIC`, and offers ARCore's install first.
- [ ] **Raw depth + confidence** — `acquireRawDepthImage16Bits()` and
      `acquireRawDepthConfidenceImage()`; drop low-confidence pixels; remove `& 0x1FFF`
      (verify the 16-bit encoding against the ARCore docs while changing it); keep
      0.25–4.0 m; skip frames while tracking is lost or the phone turns faster than ~45°/s.
      Record `depth_source`, `min_confidence`, `range_m` in `meta.json`. *Built:* the ARCore
      reference (checked 2026-09-15) confirms all 16 bits are millimetres, so the mask is gone;
      confidence ≥ 128; stale and sparse frames are skipped too, and every skip is counted
      (`DepthFilter.kt`, `TurnRate.kt`, JVM tests).
- [x] **Fusion** — keep voxels seen in ≥ 2 frames (`reconstruction/fast_path/fusion.py`). A one-frame
      scan keeps every voxel; `/reconstruct` reports frames, `min_views` and voxels dropped
      (`tests/test_reconstruction.py`, `tests/test_capture_meta.py`).
- [ ] **Registration** — the scan screen says: *stand over the home marker, point along the
      arrow, tap Start*. ARCore's origin is the phone's pose at that moment; `Frames.kt` maps
      it to +Y forward, Z up. `meta.json` records `home_pose: [0, 0, 1.5708]`; the twin carries
      it and missions start there by default. *Built, one change:* the home frame is taken from
      the camera pose at the Start tap (`HomeFrame.kt`), not from ARCore's session origin, so it
      does not depend on when tracking began; Start is refused while the phone points steeply up
      or down. The orchestrator validates `home_pose` and merges meta across chunks
      (`tests/test_capture_meta.py`); a printable marker is `docs/capture/home-marker.svg`.
- [ ] **Axis check** — walk 2 m straight ahead from the marker; the uploaded poses must show
      +Y ≈ 2 m. Put it in `tests` as a recorded scan fixture once captured. *Built:*
      `scripts/check_scan_axes.py` (±0.15 m along, < 0.15 m sideways, first frame facing +Y),
      tested on synthetic walks; the real walk is step 5.5.
- [ ] **Merge the capture app** into `MOBILE-APP` under `com.scanborn.ai.scan` (`Frames.kt`,
      `NpzWriter.kt`, `Uploader.kt`) with a Compose `ScanScreen.kt` hosting the
      `GLSurfaceView` through `AndroidView`. Add `com.google.ar:core:1.44.0` and
      `okhttp:4.12.0`. Manifest: `INTERNET`; `android.hardware.camera.ar` with
      `required="false"` and ARCore meta-data `optional`, so the app still installs for the
      assistant features; `network_security_config.xml` allowing cleartext on the LAN.
      *Built:* plus a camera preview, colour per depth pixel, and an upload queue that retries
      and counts every frame it could not send (`UploadQueue.kt`, JVM tests).
- [ ] Orchestrator URL and a **Test connection** button in Settings (DataStore), replacing the
      build-time `BuildConfig.ORCHESTRATOR_URL`. *Built:* Settings → Orchestrator, "Save and
      test connection" (`GET /health`); scanning stays locked until the saved address answered.
- [ ] Navigation entry, and `ToolsScreen.kt`'s empty `onClick = {}` becomes "Scan a space".
      *Built:* the empty handler was the Smart Notes card, which had no feature; it is now
      "Scan a space".
- [ ] Remove `capture/android/` once the merged scan works on the S24 FE.

**Laptop part verified 2026-09-15:** pytest **405 passed**; `gradlew :app:testDebugUnitTest
:app:assembleDebug` BUILD SUCCESSFUL with **26 JVM tests** passing and **0 Kotlin warnings**; the
debug APK builds (1.23 GB with the on-device model). Nothing in this phase has run on the phone yet.

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

Detailed plan, decisions and deviations: `docs/superpowers/plans/2026-09-15-phase-7-honesty.md`.

- [x] `README.md`: remove the PPO badge, Unity ML-Agents, the `unity/` tree and setup,
      `mlagents-learn configs/ppo.yaml` (neither exists), Whisper, and NPU claims. Describe what
      runs: behaviour cloning of a controller in a differential-drive simulation, on-device
      Qwen, Android speech recognition and TTS.
- [x] `reconstruction/fidelity_path/pipeline.py`: return a clean 501 instead of a dict that
      makes `service.py` raise `KeyError`.
- [x] `docker/docker-compose.yml`: remove `CAPTURE_URL` / `SEMANTIC_URL` (read by nothing);
      `Dockerfile.orchestrator` copies only `orchestrator/` but imports eight sibling
      packages — copy the tree.
- [x] `k8s/README.md` promises manifests that do not exist — remove or write them.
- [x] `configs/default.yaml` is loaded by nothing and contradicts the code — delete or wire.
- [x] `CLAUDE.md`: rewrite ("nearly every module is a stub" is false; its listed
      inconsistencies are fixed).
- [x] `MOBILE-APP/README.md` still describes the old "Infinity" app and palette — rewrite.
- [x] `detailed implementation doc.md`: add a banner that it describes the earlier Snapdragon
      architecture.

**Verified 2026-09-16.** Automated: pytest **413 passed**, flake8 and mypy clean (92 files), vitest
**44 passed**, dashboard build passes, Gradle `testDebugUnitTest assembleDebug` BUILD SUCCESSFUL with 0
Kotlin warnings, `check_live_drive.py` **13/13**, `docker compose config` parses. In the browser
pane: the landing page shows nine calls, the mission gate at 0.90 and each tier's real status, with
no console errors; the console offers sim, fake rover and ESP32 rover only, no fidelity mode, and the
host-cpu device hint. The model file matches Qwen's published `qwen2.5-1.5b-instruct-q4_k_m.gguf`
(1,117,320,736 bytes, SHA-256 `6a1a2eb6…9407e`, from Hugging Face's API). Beyond the list above
(Phase 7 plan §1.2): landing copy, device labels ("Snapdragon X Elite CRD" on every local bundle),
AI Hub coverage defaulting to 100 %, `onePlus15` default, `unity_scene_url` → `scene_url` (DB v3),
`FunctionGemmaPlanner` → `KeywordPlanner`, the UNO Q adapter removed, the buggy experiment moved
(§4.6), licences rewritten from local metadata (GSAP is not open-source-licensed; llama.cpp's MIT file
was missing and is added), the app's About screen, the model README and `setup_llama.ps1` (§5).
**Not verified:** the Docker image was not built or run (the daemon is off and the build downloads
`python:3.11-slim` and the wheels — waiting for the owner's yes); the About screen has not been seen
on the phone (Phase 5 step 5.5, item 7).

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
      during the drive, and tracking from ~15 cm height must be verified. **This is what lifts
      the ~3 m route limit Phase 3 measured**: the rover model can add a fresh-pose correction
      and the gate then shows the gain before any hardware is bought.
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
- [ ] Clear the route of anything 2–5 cm tall (cables, thresholds, books): the map counts points
      under 5 cm as floor and the rover climbs only ~2 cm, so these are neither mapped nor
      drivable.
- [ ] Keep demo routes to about 3 m and place the rover on the home marker within ±1°: the rover
      dead-reckons, and in the rover model longer routes miss the 10 cm bar on drift alone
      (Phase 3 plan, decision 14).

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
| Python | `python -m pytest` | all pass (387 on 2026-09-15, growing) |
| Firmware reference | `python scripts/gen_rover_golden.py` | `tests/test_rover_golden.py` passes: the JSON and both firmware headers are current |
| Planning speed | `python scripts/bench_mission_planning.py` | cold plan p95 ≤ 250 ms; writes `benchmarks/planning.json` |
| API document | `python scripts/export_openapi.py` | `tests/test_openapi.py` passes |
| Lint | `nox -s lint` | flake8 zero findings, mypy clean |
| Dashboard | `cd dashboard; npm test; npm run build` | pass, exit 0 |
| App | `cd MOBILE-APP; .\gradlew.bat assembleDebug` | exit 0 |
| Firmware | `nox -s firmware` with `SCANBORN_ARDUINO_DATA=D:/Arduino15` | the driving and `SELFTEST` builds compile |
| Firmware math | `nox -s firmware_host` with `SCANBORN_CXX=C:/MinGW/bin/g++.exe` | PASS on every golden row and odometry tick |
| Firmware on the ESP32 CPU | `nox -s firmware_qemu` with `SCANBORN_QEMU=D:/Tools/esp-qemu/qemu/bin/qemu-system-xtensa.exe`, after `nox -s firmware` | the emulated ESP32 prints PASS |
| Live drive in a real browser | `python scripts/check_live_drive.py` with the orchestrator and dashboard running | every check passes (WebGL 3D view, trusted E-STOP input, latch, reset) |
| Phone scan axes | `python scripts/check_scan_axes.py SCAN_ID --walk-m 2.0` | along +Y within 0.15 m, sideways under 0.15 m, first frame facing +Y |
| App | `cd MOBILE-APP; .\gradlew.bat :app:testDebugUnitTest :app:assembleDebug` | unit tests pass, exit 0 |
| Parity | `pytest tests/test_grammar_parity.py tests/test_planner_parity.py` | pass |
| Protocol | `pytest tests/test_protocol.py tests/test_rover_protocol.py tests/test_rover_deploy.py` | pass against the fake rover |
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

## 13. Decisions

Decided 2026-09-14 (the owner delegated both):

1. **Robot hardware — build the reference rover (§4.3).** No existing parts were identified, and
   its encoders, gyro and WiFi are exactly what the rover protocol and firmware in Phase 4
   assume. Parts are ordered by the owner.
2. **Deadline — none set.** Build in milestone order (§10): M1 truthful dashboard → M2 missions
   in simulation → M3 fake rover + firmware → M4 real scans from the app → M5 real drive. Voice
   (Phase 8), the design system (Phase 9) and the optional accuracy tier (Phase 10) come after
   the core loop; if a date is set later, they are what gets cut.


## 2026-09-17 reliability and shared-viewer continuation

The owner approved the expanded dashboard/Android plan. See [the milestone plan](2026-09-17-reliable-shared-viewer.md) for implementation, measured checks and open acceptance gates, and [the handoff](../handoff/2026-09-17-shared-viewer-calibration.md) for the next work. Existing phase checkmarks do not imply the new software/device gates are complete.
