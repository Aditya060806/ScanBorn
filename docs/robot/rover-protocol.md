# ScanBorn rover protocol — version 1

The contract between the orchestrator and a rover: the ESP32 firmware
(`firmware/scanborn_rover/`) or the fake rover (`robot/adapters/fake_rover.py`). Both sides are
built against this page; `robot/protocol.py` is its executable half and the tests hold them
together. Design rationale: `docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md`.

**Units:** metres, radians, seconds. **Frame:** the twin's map frame, Z up; heading is
counter-clockwise from +X. **Network:** one private 2.4 GHz network; no authentication (see
Safety).

---

## 1. Who runs what

The rover runs the whole control loop: it dead-reckons its pose from encoders and gyro, and every
tick (`control_hz`, 20 Hz by default; the motor PI loop underneath runs at 50 Hz) it does exactly
`policy/controller.py:control_step`:

1. if its estimate is within `stop_radius_m` of `goal` → stop, state `done`;
2. otherwise take the lookahead point on `path` (slide to the nearest path point ahead of the last
   index, then on to the first point at least `lookahead_m` away);
3. observation `[d·cos e, e]` — forward distance to that point in the rover's frame, heading error
   wrapped to [−π, π);
4. action `W·obs + b` with `W = W_int8 · W_scale`, `b = b_int8 · b_scale`;
5. clamp `v` to [0, `v_max`] and `ω` to ±`w_max`, then rate-limit by `a_max`, `alpha_max`;
6. wheel speeds `v ∓ ω·L/2`.

`tests/fixtures/rover_golden.json` (and `golden.h` generated from it) holds reference values for
steps 2–6; a rover implementation must reproduce them.

The orchestrator only loads the mission, starts it, sends heartbeats, listens to telemetry, and can
stop it.

## 2. States

| State | Meaning | Motors |
|---|---|---|
| `idle` | no mission loaded | off |
| `ready` | mission loaded, not started | off |
| `running` | driving the mission | on |
| `done` | stopped where its estimate reached the goal | off |
| `estop` | emergency stop, **latched** | off |
| `error` | a fault (sensor, collision in the fake rover), latched | off |

| Request | Allowed in | Result |
|---|---|---|
| `POST /mission` | idle, ready, done | `ready` |
| `POST /start` | ready | `running` (odometry reset to the mission's `start`) |
| `POST /stop` | running | `ready` (mission kept) |
| `POST /estop` | any | `estop` |
| `POST /reset` | estop, error, done, ready | `idle` (mission cleared) |
| heartbeat missing ≥ 1.0 s while running | running | `estop`, reason `heartbeat_lost` |
| ToF distance < 0.15 m while running | running | `estop`, reason `obstacle` |
| ToF stops answering while running | running | `estop`, reason `tof_fault` |
| IMU stops answering while running | running | `error`, reason `imu_lost` |
| battery < 9.9 V for 2 s | any, with a battery connected; running | `estop`, reason `battery_low` |
| on-board button pressed | any | `estop`, reason `button` |

A request in the wrong state answers `409` with `{"error": "...", "state": "<state>"}`.

An E-STOP while already in `estop` keeps the reason the rover latched on. The orchestrator's
E-STOP sends reason `dashboard`; the fake rover ends a collision in `error`, reason `collision`.

The rover also refuses `POST /start` (and `/calibrations`) with `409` and a `reason` when it cannot
drive safely: `imu_missing`, `tof_missing`, or `battery_absent` (USB power only). The fake rover has
no such hardware and never answers these.

## 3. HTTP routes (JSON, port 80 on the rover)

### `GET /status`
```json
{"protocol": 1, "firmware": "0.4.0", "fake": false, "state": "running", "reason": null,
 "mission_id": "…", "session": "…", "pose": [1.02, 0.40, 1.55], "v": 0.21, "w": 0.3,
 "index": 12, "battery_v": 11.6, "calibrated": false, "heartbeat_port": 4210}
```
`pose` is the rover's estimate. `fake` is `true` only for the fake rover, whose `battery_v` is
`null` — it has no battery to measure, so it reports none rather than a made-up number.
`calibrated` is `false` until Phase 6 writes measured values. `heartbeat_port` is where heartbeats
go (4210 on the rover; the fake rover may use another).

### `POST /mission`
| Field | Type | Notes |
|---|---|---|
| `protocol` | int | must be the integer `1` (not `1.0`, not `true`) |
| `session` | string | random per deployment, 1–64 ASCII characters; echoed in telemetry, checked on heartbeats |
| `mission_id` | string | 1–64 ASCII characters |
| `start` | [x, y, θ] | where the rover has been placed |
| `goal` | [x, y] | |
| `path` | [[x, y], …] | the dense path, 2..256 points |
| `policy` | `{"W": [[i8, i8], [i8, i8]], "W_scale": f, "b": [i8, i8], "b_scale": f}` | int8 values −127..127, scales > 0 |
| `limits` | `{"v_max", "w_max", "a_max", "alpha_max"}` | all > 0; `v_max` ≤ the rover's cap (0.30 m/s) |
| `controller` | `{"lookahead_m", "stop_radius_m"}` | > 0 |
| `telemetry_port` | int | UDP port on the requesting host |

`200 {"state": "ready"}`, or `400 {"errors": ["path: at most 256 points (got 300)", …]}` naming
every bad field.

### `POST /start`, `POST /stop`, `POST /estop`, `POST /reset`
Body `{}` (or `{"reason": "..."}` for `/estop`). Answer `200 {"state": "…"}` or `409`.

### Asynchronous calibration (capability `async_calibration_v1`)
Mission protocol remains v1. `GET /status` advertises `async_calibration_v1` and
`config_readback_v1`. `POST /calibrate` is obsolete and returns **410** with migration guidance.

`POST /calibrations` accepts `{"session":"client-session-id","mode":"straight"}` and returns
202 immediately. Modes: `straight` (about 1 m by encoder), `spin` (three turns by gyro), and
`wheel_step` (`duration_s` 0.1–3, `target_mps` positive and at most 0.2 m/s). Keep sending the normal
200 ms UDP heartbeat with the calibration session ID. Motion is tick-driven: HTTP status,
Stop, E-STOP, sensor checks and heartbeat expiry remain active. Timeout is 25 wall-clock seconds.

`GET /calibrations/{id}` returns status, mode, encoder counts, gyro rotation, elapsed seconds,
wheel speeds and `source` (`measured` on firmware, `simulated` on the fake). Terminal counts,
rotation and duration are frozen. `/stop` terminates calibration without latching E-STOP;
`/estop` and heartbeat loss stop it and require reset before subsequent motion.

The orchestrator exposes `/robots/{robot_id}/calibrations` and session GET, `/stop`, `/measurement`
and `/apply`. Measurement accepts `{"measured": number}`: metres for straight, radians for spin.
Apply is explicit, compares previous settings, verifies read-back and attempts rollback on failure.
Fake measurements write a separate simulated spec and never overwrite the physical rover spec.
Noise parameters are not calibrated by these experiments.

### `GET /config` and `POST /config`
GET returns `{"settings":{...},"source":"flash"}` (or `simulated`). POST accepts any of
`counts_per_rev`, `wheel_radius_m`, `wheelbase_m`, `pi_kp`, `pi_ki`; values must be finite positive
numbers and counts-per-revolution must be integral. Changes are refused while driving/calibrating.
Firmware persists settings in flash; the fake rover retains them for its lifetime and uses its
updated geometry. A successful response is followed by GET read-back before orchestration reports
application successful. Geometry read-back is checked before deployment when advertised; a policy
validated against different geometry is refused.

### `POST /sim/world` — fake rover only
`{"navmesh": {…}}`: the twin's navmesh, so the fake rover's model can collide. The firmware answers
404.

## 4. UDP streams

### Telemetry — rover → orchestrator, 20 Hz while not idle
Sent to the address that made the last `POST /mission`, port `telemetry_port`. One JSON object per
datagram, under 256 bytes:
```json
{"session": "…", "t": 12.35, "x": 1.021, "y": 0.402, "th": 1.553, "v": 0.21, "w": 0.30,
 "state": "running", "reason": null, "index": 12, "battery_v": 11.6}
```
`x, y, th` are the rover's **estimate**. The fake rover adds `"truth": [x, y, θ]`, which nothing on
the real path may depend on.

### Heartbeat — orchestrator → rover UDP 4210, every 200 ms while running
```json
{"session": "…", "seq": 42}
```
A heartbeat with a different session is ignored. No heartbeat for 1.0 s → `estop` /
`heartbeat_lost`, latched.

## 5. Safety

- The rover obeys the stricter of the mission's limits and its own caps.
- E-STOP is latched on the rover, reachable from the dashboard (button and Space key), the on-board
  button and the physical power switch.
- Losing the orchestrator stops the rover within 1 s; losing WiFi does the same.
- **No authentication.** Anyone on the network can command the rover: use a private network (§8 of
  the master plan). The session token keeps stale telemetry and heartbeats out; it is not a
  credential. WiFi credentials live in the gitignored `secrets.h`.
