# Phase 4 — Rover protocol, fake rover, firmware (implementation plan)

Detailed plan for Phase 4 of `2026-09-13-scanborn-scan-to-drive-plan.md` (§7), written 2026-09-14
after reading the code it builds on. Milestone M3. No rover hardware is needed.

**Outcome:** the orchestrator deploys a trained mission to a rover over a documented protocol and
shows the drive live. The "rover" is either the ESP32 firmware (compiles now, runs on metal in
Phase 6) or a fake rover that speaks the identical protocol with the Phase 3 rover model inside.
The firmware's arithmetic is proven against the Python golden vectors on the laptop.

---

## 1. What exists (read before changing)

| Piece | Where | State |
|---|---|---|
| Velocity interface | `robot/adapters/base.py` | `supports_drive`, `reset`, `step`, `estimated_pose`, `true_pose`; `SimRobot` implements it |
| Control loop | `policy/evaluate.py:drive_outcome` | loops internally: estimate → `observe` → `act` → `step`; stop on estimate within 2 cm |
| Controller | `policy/controller.py` | `lookahead_index`, `observe`, `demonstrate`, `policy_manifest`; constants LOOKAHEAD 0.35, STOP_RADIUS 0.02 |
| Kinematics | `robot/kinematics.py` | `limit_command`, `wheel_speeds`, `wheel_rates`, `counts_per_tick` |
| Golden vectors | `tests/fixtures/rover_golden.json` | 7 poses through the per-tick chain |
| Rover model | `policy/sim.py` | real-time-agnostic `step(v, ω)` |
| Spec | `robot/specs/rover.yaml` | placeholders; `v_max` 0.30 |
| Deploy | `orchestrator/service.py:/deploy` | synchronous; sim only; `robots.kind` allows `rover`; `runs` rows |
| Job events | `orchestrator/jobs.py` | in-memory `subscribe` pattern; `WS /ws/status` |
| Robots table | `orchestrator/db.py` | `kind`, `config_json` |
| Dashboard | `Console.jsx` Deploy stage; `MissionMap.jsx` has a `realTrace` prop (empty) | no live view |
| Toolchain | arduino-cli 1.5.0 | global data dir is a dead link; repaired on D: (§2 decision 13) |

## 2. Decisions

1. **The control loop runs on the rover.** The orchestrator sends the whole mission and the int8
   policy, then only watches, heartbeats and can stop it. A WiFi hiccup must never become a
   steering input. The fake rover runs the same loop.
2. **One step function for every loop.** `policy/controller.py:control_step(estimate, path,
   index, goal, act)` returns `(command | None, index)` — `None` when the estimate is within the
   stop radius. `drive_outcome`, the fake rover and the firmware (`rover_math.h`) all implement
   exactly this, and the golden vectors pin it.
3. **Mission payload** (`POST /mission`, protocol version 1): `protocol`, `session` (a random
   token the rover echoes in telemetry), `mission_id`, `start` [x, y, θ], `goal` [x, y], `path`
   (the dense path, ≤ 256 points — a 25 m route; longer is refused), `policy`
   (`W` int8 2×2, `W_scale`, `b` int8 ×2, `b_scale`), `limits` (`v_max`, `w_max`, `a_max`,
   `alpha_max`), `controller` (`lookahead_m`, `stop_radius_m`), `telemetry_port`. The rover
   validates every field (finite, shapes, `v_max` ≤ its own cap 0.30 m/s, path length) and
   answers 400 with the field named.
4. **Transport.** HTTP/JSON for commands (port 80 on the rover). UDP for the streams, so neither
   blocks the control loop: telemetry rover → orchestrator at 20 Hz to the request's source address
   and `telemetry_port` (one JSON object per datagram, < 256 bytes); heartbeat orchestrator →
   rover UDP 4210 every 200 ms (`{"session", "seq"}`).
5. **States:** `idle` (no mission) → `ready` (mission loaded) → `running` → `done` (stopped at
   the goal by its own estimate) | `estop` | `error`. `/mission` is accepted in idle, ready and
   done; `/start` only in ready; `/stop` → `ready` (mission kept); `/estop` → `estop` from any
   state; `/reset` is the only way out of `estop` or `error`, back to `idle`.
6. **Deadman:** running with no heartbeat for 1.0 s (wall clock) → motors off, `estop`, reason
   `heartbeat_lost`. It latches: a heartbeat that returns does not restart the drive.
7. **Telemetry carries what the rover knows:** `t`, estimated `x, y, θ`, `v`, `ω`, `state`,
   `reason`, `index`, `battery_v`. The fake rover adds `truth` [x, y, θ], labelled fake-only;
   nothing on the real path depends on it.
8. **Rover deployments need a mission.** A real rover must be placed on a known start; the
   mission-less start chosen for the sim has no meaning on a floor. `/deploy` with `kind=rover`
   and no mission is 409 `mission_required`.
9. **Deployments are sessions** (`orchestrator/deployments.py`): one UDP listener (port
   `SCANBORN_TELEMETRY_PORT`, default 5005; `0` = ephemeral, for tests) dispatches datagrams by
   session; one heartbeat thread per running session; `subscribe(deployment_id, callback)` like
   `jobs.py`; `WS /ws/deployments/{id}` streams a snapshot then every telemetry frame. When a
   session ends (done / estop / error / stopped / telemetry silent 3 s) it writes the `runs` row:
   kind `rover` or `fake_rover`, reached, trace, and deviation = RMS distance of the reported trace
   from the mission's simulated median trace (from its training run).
10. **The fake rover is two things.** `python -m robot.adapters.fake_rover --port 8081
    [--navmesh navmesh.json]` for standalone protocol testing, and an in-process instance the
    orchestrator starts for `POST /robots {"kind": "fake_rover"}` so the dashboard can drive one
    with nothing else running. It needs the twin's map for collisions, which a real rover does not
    have: a fake-only `POST /sim/world` sets it, and the firmware answers that route 404.
11. **Time scale is for tests only.** The fake rover can run its plant faster than real time;
    heartbeat, deadman and telemetry timing stay wall-clock, so safety behaviour is tested as it
    will run.
12. **Calibration:** Phase 4 builds the transport (`POST /calibrate` straight | spin, reporting
    encoder counts and gyro yaw); the procedure that writes measured values into `rover.yaml` is
    Phase 6. The fake rover answers from its model and labels the result `simulated`.
13. **Toolchain on D:.** The global Arduino15 folder points at a deleted offload target and is
    left alone. `nox -s firmware` sets `ARDUINO_DIRECTORIES_DATA`, `_DOWNLOADS`, `_USER` from
    `SCANBORN_ARDUINO_DATA` (on this laptop `D:/Arduino15`) and the ESP32 board URL. ESP32 core 3.x
    APIs (`ledcAttach`); libraries ArduinoJson 7, ESP32Encoder, Adafruit MPU6050, Pololu VL53L0X.
14. **Firmware math is proven on the laptop.** `rover_math.h` is plain C++ with no Arduino
    headers; `firmware/tests/host_selftest.cpp` includes it with `golden.h` (generated from the
    fixture) and runs under the MinGW g++ already installed. That compiler is GCC 6.3 (32-bit
    MinGW.org, no C++17), so `rover_math.h` sticks to a C++14 subset both it and the ESP32's GCC
    accept, and the host build uses `-std=c++14 -msse2 -mfpmath=sse` so x87 extended precision
    cannot make a 32-bit result differ from the golden vectors. On the ESP32 the `SELFTEST` build
    runs the same checks and prints PASS or FAIL.
15. **No authentication on the rover's HTTP API**, stated plainly: it runs on a private network
    (§8), the session token only keeps stale telemetry out, and E-STOP is also on the rover's
    button and power switch. WiFi credentials live in a gitignored `secrets.h`.

## 3. Steps

### 4.1 Toolchain
- ESP32 core and libraries installed under `D:/Arduino15`; a check sketch including all four
  libraries and `ledcAttach` compiles.
- `noxfile.py`: `firmware` session (compile) and `firmware_host` session (host self-test).
- START-HERE and the master plan record the D: data folder and the env vars.

### 4.2 Shared step and protocol document
- `policy/controller.py:control_step`; `drive_outcome` rewritten on top of it (Phase 3 tests must
  still pass unchanged).
- `docs/robot/rover-protocol.md`: every route, payload, field, unit, state transition, error,
  timing and the telemetry/heartbeat datagrams — the contract both sides build against.
- `robot/protocol.py`: `PROTOCOL_VERSION`, `validate_mission(payload, caps) -> list[str]`
  (shared by the fake rover and tests), `mission_payload(mission, bundle, spec, session, port)`.
- **Tests** (`tests/test_protocol.py`): a valid payload passes; each bad field is named; a 257-point
  path and `v_max` above the cap are refused; `control_step` matches the golden rows.

### 4.3 Fake rover (`robot/adapters/fake_rover.py`)
- `FakeRover(spec, navmesh=None, noise=None, seed=0, time_scale=1.0, http_port=0, udp_port=0)`:
  `ThreadingHTTPServer` for the routes, a UDP socket for heartbeats, a control thread stepping
  `DiffDriveSim` at `control_hz × time_scale`, a telemetry thread at 20 Hz; `start()`, `close()`,
  `address`. Standard library only.
- CLI `python -m robot.adapters.fake_rover`.
- **Tests** (`tests/test_rover_protocol.py`, against a real socket): status in idle; mission
  accepted → ready; bad mission 400 naming the field; start → running → telemetry arrives with the
  session → done near the goal; stop → ready; E-STOP latches (start refused) until reset; no
  heartbeat → `estop`/`heartbeat_lost` within 1.2 s and stays stopped when heartbeats resume;
  `/sim/world` sets the map and a collision ends in `error`; calibrate answers `simulated`.

### 4.4 Rover adapter (`robot/adapters/rover.py`)
- `RoverRobot(address, timeout)`: `status()`, `send_mission()`, `start()`, `stop()`, `estop()`,
  `reset()`, `calibrate()`, `heartbeat(session, seq)`; HTTP via `httpx`, errors surfaced with the
  rover's reason. Registry adds `rover`.
- **Tests:** against `FakeRover` — every call round-trips; an unreachable rover reports offline
  instead of raising.

### 4.5 Orchestrator
- `orchestrator/deployments.py` (decision 9).
- `POST /robots` (`kind` rover with `address`, or `fake_rover`) → probes status, stores the robot;
  `GET /robots/{id}/status`; `POST /robots/{id}/estop`; `POST /robots/{id}/calibrate`;
  `GET /deployments/{id}`; `POST /deployments/{id}/stop`; `WS /ws/deployments/{id}`.
- `/deploy` with a rover robot: stale / outdated / footprint checks as today; mission required;
  sends the mission (and the twin map to a fake rover), starts it, returns `status: "running"`
  and the deployment id at once.
- `robots.config_json` holds `address` and `fake`; the in-process fake rovers are closed on
  shutdown.
- OpenAPI re-exported; SDK `register_robot`, `robot_status`, `estop`, `deployment`,
  `stop_deployment`.
- **Tests** (`tests/test_rover_deploy.py`): register a fake rover → deploy a trained mission →
  WS frames arrive → the deployment finishes `done` → a `runs` row with deviation from the
  simulated trace; `/robots/{id}/estop` mid-drive latches and the session ends `estop`; a rover
  wider than the twin's profile is refused; a rover deploy without a mission is 409; an
  unreachable address is reported, not raised.

### 4.6 Firmware (`firmware/scanborn_rover/`)
- `config.h` (pins from plan §4.4, caps, rates), `rover_math.h` (decision 2 in C++: wrap angle,
  lookahead index, observation, int8 policy, limits, wheel speeds, counts), `odometry.cpp/.h`
  (ESP32Encoder + MPU6050 yaw with a 2 s bias estimate), `motors.cpp/.h` (LEDC PWM + per-wheel PI
  on encoder speed), `mission.cpp/.h` (payload parse and validation with ArduinoJson, state
  machine, deadman), `net.cpp/.h` (WiFi, mDNS `scanborn-rover`, WebServer routes, UDP), `safety`
  (ToF stop under 0.15 m, battery cut-off 9.9 V, button), `secrets.example.h`; `.gitignore`
  adds `secrets.h`.
- `scripts/gen_rover_golden.py` also writes `firmware/scanborn_rover/golden.h`.
- `firmware/tests/host_selftest.cpp` (decision 14).
- **Checks:** `nox -s firmware` compiles for `esp32:esp32:esp32` (with and without `SELFTEST`);
  `nox -s firmware_host` builds and runs the host self-test — PASS on every golden row.

### 4.7 Dashboard
- Deploy stage: robot choice sim / fake rover / ESP32 rover (address field, "register" button);
  deploying a rover opens the live view.
- `api.js`: `registerRobot`, `robotStatus`, `estop`, `deployment`, `stopDeployment`;
  `live.js`: WebSocket client for `/ws/deployments/{id}` with reconnect.
- `MissionMap.jsx` draws the live trace (`realTrace`) and a rover marker with heading;
  `RobotViewer3D` moves the rover live.
- A persistent **E-STOP** button (and the Space key) whenever a rover deployment is running;
  **Stop** beside it; state and reason chips; the result row shows reached, deviation from the
  simulated trace, and `fake` when it was the fake rover.
- Vite proxy `/robots`, `/deployments`, `/ws/deployments`.
- **Tests** (vitest): the live client folds frames into a trace; E-STOP calls the API.

### 4.8 Verification
- Full Python suite, lint, types; dashboard tests and build; `nox -s firmware`, `nox -s firmware_host`.
- **Browser, done-when:** register the fake rover → deploy a trained ~2.5 m mission → the map and 3D
  view draw the live trace and rover → it finishes `done` with deviation shown → a second drive
  E-STOPped from the dashboard latches.

## 4. Order and done

Order: 4.1 → 4.2 → 4.3 → 4.4 → 4.5 → 4.6 → 4.7 → 4.8. The firmware (4.6) depends only on 4.2
and can be built while 4.5 is tested.

**Done when** (master plan): dashboard → orchestrator → fake rover completes a full drive with a
live trace, with no hardware attached, and the firmware compiles — plus the firmware math passes
the golden vectors on the laptop.

**Not verifiable in Phase 4:** anything physical — motor PI tuning, encoder counts, gyro bias,
ToF distance, WiFi range, battery readings. Those are Phase 6 on the real rover.

## 5. Outcome (2026-09-15)

Every step 4.1–4.8 is built. Measured results and the verification are in the master plan's
Phase 4 "Verified" block; this section records where the build departed from §2 and why.

| # | Change | Why |
|---|---|---|
| 1 | The ESP32 core is 3.3.11; libraries ArduinoJson 7.4.3, ESP32Encoder 5.0.0, Adafruit MPU6050 2.2.9 (with BusIO, Unified Sensor, GFX, SSD1306), Pololu VL53L0X 1.3.1, all under `D:/Arduino15` (`staging`, `user`) | Decision 13 as planned; the versions are what `arduino-cli` installed |
| 2 | `rover_math.h` names π `rover::MATH_PI` | Arduino.h defines `PI`, `TWO_PI`, `HALF_PI`, `DEG_TO_RAD`, `DEFAULT`, `bit()`, `sq()` as macros; `rover::PI` failed to compile |
| 3 | The golden vectors gained an odometry section: 40 commands through the limits, rate limits and the midpoint pose update, across the ±π wrap. `gen_rover_golden.py` also writes `golden.h` and `rover_spec.h` (the spec as the firmware's defaults), and a test fails if either header is stale | Decision 14 covered the control step only; the pose update and rate limits are what dead reckoning rests on, and the firmware must not keep its own copy of the spec |
| 4 | The self-test (`selftest.h`) is one implementation shared by the host build and the `SELFTEST` build, at the 1e-9 tolerance the Python golden test uses | Numpy's matrix product may fuse operations, so bit-exact equality is not a fair bar across compilers |
| 5 | Protocol v1 tightened: `protocol` must be the integer 1; `session` and `mission_id` at most 64 characters; a second E-STOP keeps the first reason (fake rover and firmware alike) | The firmware keeps ids in fixed buffers and cannot accept what Python's `1.0 == 1` would; an E-STOP's cause must not be overwritten by the next press |
| 6 | The rover refuses `/start` and `/calibrate` with `imu_missing`, `tof_missing` or `battery_absent`; it faults `imu_lost` and E-STOPs `tof_fault` mid-drive; the battery cut-off needs 2 s under 9.9 V | A rover that cannot see or measure must not move; motor starts dip the pack briefly |
| 7 | `/calibrate` blocks the rover's HTTP server for its move (≤ 25 s) while the ToF stop, cut-off, button and power switch stay active | The ESP32 `WebServer` is synchronous; the move's own safety stays local. Documented in the protocol |
| 8 | `/config` accepts any subset of its five fields; `calibrated` turns true once a wheel or encoder value is saved | Phase 6 writes geometry and PI gains at different times |
| 9 | A rover deploy with no robot id answers 400 `robot_required`; a second deploy while the rover drives 409 `robot_busy`; a rover that refuses the start leaves no `runs` row | Found while writing `tests/test_rover_deploy.py` |
| 10 | Results carry `deviation_from_sim_m`, `reported_miss_m` (where the rover believes it stopped) and, for the fake rover only, `true_miss_m` | A real rover can only report its estimate; the fake rover's truth is labelled as such |
| 11 | `WS /ws/deployments/{id}` subscribes before its snapshot and exits on the snapshot's `finished` or the `finished` message | The first version could close before sending `finished` (a pytest thread warning exposed it) |
| 12 | Console: Deploy is locked while a rover drives; the E-STOP key accepts `key === " "` as well as `code === "Space"`; each view sits in an error boundary and the 3D view survives a WebGL context that will not start | Found in the browser check: a refused second deploy erased the live drive's record; a WebGL failure unmounted the whole console, E-STOP included |
| 13 | The open checks were closed the same day: `scripts/check_live_drive.py` drives the console in headless Chrome over the DevTools protocol (real GPU, trusted key and mouse input); the firmware builds use DIO flash and `nox -s firmware_qemu` runs the `SELFTEST` image on Espressif's ESP32 emulator; a rover deploy keeps the shared empty `poseTrace` so starting a drive does not rebuild the 3D scene | The in-app browser pane had no WebGL and sent key events without a key name; no ESP32 was attached, and the emulator's flash initialisation rejects QIO images |

**Left for Phase 6, by design:** every `PLACEHOLDER` in `firmware/scanborn_rover/config.h`
(motor feed-forward, PI gains, encoder and gyro signs), the calibration procedure, running the
`SELFTEST` build on a real ESP32 (it is compiled, not run), and everything physical listed above.
