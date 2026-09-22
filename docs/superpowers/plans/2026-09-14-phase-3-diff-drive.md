# Phase 3 — A simulation that behaves like the rover (implementation plan)

Detailed plan for Phase 3 of `2026-09-13-scanborn-scan-to-drive-plan.md` (§7), written
2026-09-14 after reading the code it changes. With Phase 2 it completes milestone M2.

**Outcome:** training and deployment stop teleporting a point robot. The policy drives a
two-wheel rover model with the reference rover's speed, turn and acceleration limits, wheel
slip, gyro bias and a one-tick delay, and it steers from the pose the rover can *estimate*
(encoders + gyro), not the true one. A mission deploys only if 30 noisy runs of that mission
reach the goal at least 90 % of the time. Everything the firmware will need to reproduce is
pinned by golden vectors.

Laptop CPU only. No rover parts needed.

---

## 1. What exists today (read before changing)

| Piece | Where | Today | Problem |
|---|---|---|---|
| Motion | `policy/evaluate.py` `rollout`, `record_demos` | holonomic point: `pos += cap(policy(target − pos))`, `MAX_STEP` 0.5 m per tick | nothing like a diff-drive rover; no heading, no limits, no noise |
| Observation / action | same, `service.OBS_DIM = ACT_DIM = 2` | obs = vector to a lookahead point; act = (dx, dy) step | the rover takes (v, ω), and knows only its own frame |
| Gates | `evaluate` (20 random starts ≥ 60 %) + Phase 2's single clean mission rollout | deterministic | a policy that survives one perfect run can still fail under slip |
| Collision | `is_blocked(navmesh, centre)` on the raw grid | a point | the rover has a 15 cm radius |
| Runner | `robot/policy_runner.run_policy` | same holonomic law, calls `robot.move(x, y)` | cannot drive a velocity-commanded robot |
| Adapters | `robot/adapters/base.py` (`move`, `execute_path`), `sim.py` (teleport), `unoq.py` (JSON waypoints) | waypoint teleports | no velocity interface |
| Rover numbers | constants in `dashboard/src/RobotViewer3D.jsx`; `MAX_LINEAR_SPEED` 0.5 in `base.py` | scattered, unmeasured | plan §4.5 wants one spec file |
| Export | `deployment/aihub_export/export_script.py` manifest | `obs_dim`, `act_dim` only | does not say what the numbers mean; old policies would load silently |
| Floor band | `twin/navmesh.py` `FLOOR_CLEARANCE = 0.08` | points under 8 cm are floor | the rover climbs about 2 cm |

Callers and tests that must keep passing (updated, not deleted): `tests/test_policy.py`,
`test_robot.py`, `test_orchestrator.py`, `test_missions.py`, `test_sdk.py`, `test_deployment.py`,
`test_profile.py`, the dashboard fixture tests.

---

## 2. Decisions

1. **One spec file.** `robot/specs/rover.yaml` holds wheel radius, wheelbase, counts per rev,
   footprint radius, height, step height, v/ω/a/α limits, control rate and noise. Every value
   is marked `source: placeholder` until Phase 6 calibration replaces it; the API and dashboard
   show that label.
2. **Plant model** (`policy/sim.py`), per tick of `dt = 1 / control_hz`:
   1. the command applied is the one from `latency_ticks` ago;
   2. `v` is clamped to `[0, v_max]` (no reversing — the ToF sensor faces forward), `ω` to
      `±w_max`;
   3. actual `v`, `ω` move toward the command by at most `a_max·dt`, `alpha_max·dt`;
   4. wheel speeds `v ∓ ω·L/2`; each wheel's ground speed gets `(1 + ε)`, `ε ~ N(0, slip)`;
   5. the **true** pose integrates the slipped wheel speeds (midpoint heading);
   6. the **estimated** pose integrates the encoder wheel speeds (no slip) for distance and the
      gyro (`true ω + bias`, bias drawn once per run) for heading.
   The controller only ever sees the estimated pose; collisions and success use the true pose.
3. **Collision** = true centre where `clearance_field ≤ footprint_radius`, or off the map — the
   same distance field the planner uses (Phase 2 §9).
4. **Controller** (`policy/controller.py`): pure pursuit on the mission's dense path. Lookahead
   0.35 m; the index only moves forward. `obs = [d·cos e, e]` (forward distance to the
   lookahead point in the rover's frame, heading error wrapped to (−π, π]). Demonstrator
   `v = K_v·d·cos e`, `ω = K_ω·e`, with `K_v = 1.0 s⁻¹`, `K_ω = 2.5 s⁻¹`. When the point is
   behind, `d·cos e < 0` and the plant clamps `v` to 0: it turns in place. The rover stops when
   its own estimate is within 2 cm of the goal — measured: 5 cm left a 5.9 cm median miss on a
   2.5 m route, 2 cm leaves 3.8 cm for 0.8 s more — and a run succeeds if its true position is
   then within 0.10 m (the plan's §11 bar).
5. **Labels are the unclamped demonstrator output.** Limits belong to the plant, so the linear
   fit is exact and behaviour cloning recovers the gains; `train_bc.py` is unchanged.
6. **Two gates.** With a mission: `evaluate_mission` — 30 runs from the mission start with a
   small start error (±2 cm, ±3°, the home marker's tolerance) and fresh noise each, success
   = true pose within 0.10 m of the goal before the time limit without a collision;
   `MISSION_GATE = 0.9`. Without a mission (the old path): 20 random starts on the diff-drive
   plant, `SIM_GATE = 0.6` as before.
7. **Metrics that mean something.** Each run ends `reached`, `stopped_off_goal` (stopped where
   its estimate put the goal, but drift had moved it — the controller's job done, the odometry's
   not), `collided` or `timeout`, and the counts are reported. Also: median time and RMS
   deviation from the route of the runs that arrived; median miss and median drift (estimate
   against truth) over all runs; closest approach; and **the median run's trace** — the
   representative drive, not the best-looking one. Demonstrations that stopped off the goal
   are kept (their labels are exact); collisions and timeouts are dropped.
8. **Noise values are assumptions until measured — taken from the plan's hardware procedure,
   not tuned to pass.** Slip 5 % (1σ per wheel); residual gyro bias σ 0.002 rad/s (~0.1°/s
   after the firmware's 2 s still calibration); one tick of latency; placement on the home marker
   ±1 cm and ±1° (the rover's rear edge against the marker's tape line). The first draft used
   0.01 rad/s and ±3°: the ideal controller then reached a 4.6 m goal in 0 of 10 runs, all of
   them drift. Labelled `assumed` everywhere; Phase 6 replaces them with calibrated numbers.
9. **Footprint must fit the twin** (plan §4.5): `/train` and `/deploy` refuse a mission when
   the twin's `profile.robot_radius` is smaller than the spec's `footprint_radius_m`. Every
   built-in profile is ≥ 0.20 m against the rover's 0.15 m, so this guards custom profiles.
10. **Old policies are refused, not misread.** The export manifest records `observation`,
    `action`, controller constants and spec name. `/deploy` refuses an artifact whose manifest
    lacks the current observation (409 `policy_outdated`: retrain) instead of feeding (dx, dy)
    weights (v, ω) inputs.
11. **Floor band lowered to 5 cm.** `FLOOR_CLEARANCE` 0.08 → 0.05 m: 2.5–5× fused depth noise,
    and three points still filter isolated spikes. Objects 2–5 cm tall (cables, thresholds) stay
    below what the navmesh can see while the rover climbs only ~2 cm — recorded as a known gap
    and added to the demo checklist ("clear floor"), not hidden.
12. **Velocity interface on the adapter seam.** `ScanBornRobot.drive(v, ω) -> bool` and
    `supports_drive`. `SimRobot` implements it with `DiffDriveSim`; `move()` stays for
    compatibility. `UnoQRobot` has no odometry, so it reports `supports_drive = False` and
    `/deploy` says so instead of pretending to close a loop.
13. **Spec over HTTP.** `GET /robot-specs/rover` serves the spec, so the 3D viewer draws the
    rover from the file instead of its own constants.
14. **Dead reckoning limits route length — a finding, recorded, not tuned away.** With the
    assumed noise and the ideal controller, runs reaching within 10 cm: 2.5 m route 30/30,
    3.7 m 25/30, 4.6 m 23/30 (median drift 3.5, 5.3, 7.0 cm). The 90 % gate therefore passes
    routes up to about 3 m and refuses longer ones — as the real rover would miss them.
    Consequences: tests and the fixture use ~2–2.5 m routes; a deploy without a mission starts
    ~2.5 m from its goal instead of as far away as possible; the 409 reports
    `stopped_off_goal` and `median_drift_m`, so a long route's failure reads as drift; the Train
    stage says so. Longer demo routes need a position fix near the goal — added to Phase 10.

---

## 3. Steps

### 3.1 Spec (`robot/spec.py`, `robot/specs/rover.yaml`)
- `RobotSpec` frozen dataclass; `load_spec(name="rover")` validates every number is finite and
  positive (latency ≥ 0) and raises `ValueError` naming the field; `as_dict()`.
- `robot/adapters/base.py` `MAX_LINEAR_SPEED` stays as the absolute safety ceiling; the spec's
  `v_max` must not exceed it (validated).
- **Tests** (`tests/test_spec.py`): the shipped spec loads; a bad value names its field;
  `v_max` above the ceiling is refused; footprint ≥ half the chassis diagonal of 20 × 20 cm.

### 3.2 Plant (`policy/sim.py`)
- `Noise(wheel_slip, gyro_bias, latency_ticks, start_xy, start_heading)` with `Noise.none()`
  and `Noise.from_spec(spec)`.
- `DiffDriveSim(navmesh, spec, noise, seed, field=None)`: `reset(pose)`, `step(v, ω) -> bool`
  (False on collision), `true_pose`, `estimated_pose`, `wheel_speeds`, `t`.
- **Tests** (`tests/test_sim.py`): commanded 5 m/s never exceeds `v_max`, and acceleration
  never exceeds `a_max` (checked tick by tick); negative `v` clamps to 0; noise-free straight
  drive covers `v·t` exactly; the same seed gives the same trace, different seeds differ;
  gyro bias alone makes estimated heading drift from true at the bias rate; slip alone leaves
  estimated ahead of true in distance; one tick of latency delays the first motion by one tick;
  the footprint collides with a wall the centre has not reached.

### 3.3 Controller (`policy/controller.py`)
- `wrap_angle`, `lookahead(pose, path, index, distance)`, `observe(pose, path, index)`,
  `demonstrate(obs)`, `at_goal(pose, goal)`.
- **Tests** (`tests/test_controller.py`): target straight ahead → `e = 0`, positive `v`; target
  behind → `v ≤ 0` (the plant turns in place); the index never moves backwards; angles wrap.

### 3.4 Training and evaluation (`policy/evaluate.py`, rewritten around the plant)
- `drive(policy, sim, path, goal, max_steps, trace)` — the one control loop used by demos,
  evaluation and the runner.
- `record_demos(navmesh, goal, spec, path=None, n, seed)`: mission demos start near the mission
  start; no-mission demos start from random traversable cells with their own A* path.
- `evaluate(policy, navmesh, goal, spec, episodes=20)` (no-mission gate) and
  `evaluate_mission(policy, navmesh, mission, spec, runs=30, seed)` (decision 7 metrics).
- Removed: `_cap`, `MAX_STEP`, `DEMO_GAIN`, the holonomic `rollout`. `inflate`, `plan_path`,
  `free_cells` keep their exports.
- **Tests** (`tests/test_policy.py` updated): behaviour cloning recovers `K_v`, `K_ω` within
  1 %; the zero policy fails every run; the cloned policy clears `MISSION_GATE` on a furnished
  room with a corner to turn; a policy with half the steering gain fails the gate more often
  (the gate discriminates); metrics are within their physical ranges.

### 3.5 Export (`deployment/aihub_export/export_script.py`)
- Manifest adds `observation`, `action`, `controller` (lookahead, goal tolerance, gains used for
  demos) and `spec`.
- **Tests** (`tests/test_deployment.py`): the manifest names the observation and action.

### 3.6 Runner and adapters
- `robot/policy_runner.run_policy(robot, policy, navmesh, goal, start, path, spec)`: estimated
  pose → observation → timed int8 inference → `robot.drive`; returns the trace, `reached`,
  latency percentiles, as today.
- `SimRobot(navmesh, spec=None, noise=None, seed=0)` gains `drive`; `move` and
  `execute_path` unchanged. `UnoQRobot.supports_drive = False`.
- **Tests** (`tests/test_robot.py` updated): the sim robot drives a mission to the goal under
  noise; it refuses to drive into a wall; the UNO Q adapter says it cannot drive.

### 3.7 Orchestrator (`orchestrator/service.py`, CRLF)
- `/train` with a mission: footprint check (decision 9), demos on the plant, `evaluate_mission`,
  409 `mission_gate` carrying the metrics; success returns `mission_metrics` and
  `mission_trace` (replacing Phase 2's `mission_reached`), and writes one `runs` row with the
  metrics and the median trace. Without a mission: `evaluate` on the plant, 409 `sim_gate`.
- `/deploy`: refuses `policy_outdated` (decision 10) and a footprint that does not fit; the sim
  robot drives with assumed noise and a fixed seed; unsupported adapters return
  `status: "connected"` with `error: "adapter cannot take velocity commands"`.
- `GET /robot-specs/{name}`; re-export `docs/api/openapi.yaml`.
- **Tests**: `test_orchestrator.py` and `test_missions.py` updated to the new fields; a
  footprint larger than the profile's radius is refused; an old-format artifact is refused.

### 3.8 Golden vectors (`scripts/gen_rover_golden.py`)
- Writes `tests/fixtures/rover_golden.json`: a quantized demonstrator policy (int8 weights and
  scales), then for sample poses on a sample path — observation, dequantized action, plant
  command after limits, left/right wheel speeds (m/s and rad/s), encoder counts per tick.
- `tests/test_rover_golden.py` recomputes every row with the Python functions and must match
  to 1e-9; Phase 4's firmware self-test checks the same file.

### 3.9 SDK and dashboard
- SDK: `robot_spec(name)`; `train()` returns the new fields (no signature change).
- Dashboard: `api.robotSpec`; Train stage shows `27/30 runs reached · 4.1 cm RMS off route ·
  closest 0.21 m · noise assumed`; the 409 renders the same metrics; the map draws the median
  simulated trace; `RobotViewer3D` takes dimensions from the spec; Vite proxy `/robot-specs`;
  fixture re-recorded; vitest updated.

### 3.10 Floor band
- `FLOOR_CLEARANCE = 0.05`; `tests/test_navmesh.py` adds a 6 cm box that now blocks and a 3 cm
  one that does not; the known gap goes into the master plan's demo checklist.

---

## 4. Order, verification, done

Order: 3.1 → 3.2 → 3.3 → 3.4 → 3.5 → 3.6 → 3.7 → 3.8 → 3.9 → 3.10 → browser check.

**Verification**
```
python -m pytest -q -o addopts=""
python -m flake8 sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests scripts
python -m mypy sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests
python scripts/gen_rover_golden.py
python scripts/export_openapi.py
cd dashboard; npm test; npm run build
```

**Browser check** (seeded synthetic room): plan a mission around the chair → Train shows the
run count, deviation and closest approach under assumed noise → the map shows the median
trace following the route → Deploy drives it → the rover in 3D has the spec's size.

**Done when** (master plan): limits are never exceeded; seeded noise is reproducible; the
controller turns in place when the target is behind; behaviour cloning recovers the gains
within 1 %; the gate passes on a furnished room; the golden fixture exists for Phase 4.

---

## 5. Outcome (2026-09-14)

Built as planned; verification is recorded in the master plan's Phase 3 block. Where the build
departed from this plan, and why:

| Planned | Built | Why |
|---|---|---|
| Noise: gyro bias σ 0.01 rad/s, placement ±2 cm / ±3° | σ 0.002 rad/s, ±1 cm / ±1° (still `assumed`) | The first values are an uncalibrated gyro and a careless placement; with them the ideal controller reached a 4.6 m goal in 0/10 runs. The new ones are what the plan's own procedure (2 s still calibration, marker tape line) should give |
| Lookahead index only moves forward | Slides to the nearest path point ahead first, then to the lookahead | A test caught a rover part way along its route steering back to the start |
| Command (0, 0) inside the goal tolerance | Stop when the *estimate* is within 2 cm; success if the *truth* is within 10 cm | That is what the real rover can do; 2 cm measured against 5 cm (median miss 3.8 vs 5.9 cm) |
| Metrics: rate, time, deviation, clearance, median trace | Plus outcome counts, median miss, median drift | A failed gate has to say whether the policy or the odometry failed |
| Drop demonstrations that fail | Drop collisions and timeouts; keep "stopped off goal" | Under drift every demo "failed" and cloning learned nothing, although every label was exact |
| — | Dead-reckoning route limit (decision 14): tests, fixture and mission-less deploys use ~2.5 m | Measured physics, not a tuning choice |
| — | One checkpoint folder per policy | Two trainings on one twin overwrote the first policy's weights |
| — | `robot/kinematics.py` shared by the golden vectors | The firmware repeats exactly these functions |
| `SimRobot.drive` | `reset` / `step` / `estimated_pose` / `true_pose`, so `drive_outcome` runs on the robot directly | One control loop for demos, gates and deployment |

Verified in the browser (master plan block): stage 04 explains its grid; a 2.55 m mission trains
to 30/30 runs, 2 cm RMS off route; the deployed int8 policy stops 2.7 cm from the goal; the 3D
view draws the rover from the spec.

Open from this phase: every spec number and all noise are placeholders until Phase 6
calibration; the firmware self-test against the golden vectors is Phase 4.
