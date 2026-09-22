# Phase 2 — Mission control: start, goal, path (implementation plan)

Detailed plan for Phase 2 of `2026-09-13-scanborn-scan-to-drive-plan.md` (§7). Written
2026-09-14 after reading the code it changes. Phase 2 + Phase 3 form milestone M2; §9 below
says exactly what Phase 3 inherits.

**Outcome:** on the dashboard you click a goal on a map of free / blocked / unknown space, and
within a second you see a short, clear path that keeps its distance from obstacles. Start, via
points and goal can never sit on a blocked or unknown cell. Training and deployment follow that
mission, and a rescan that changes the room along the path blocks deployment until the mission
is planned again.

Everything runs on the laptop CPU. No phone and no rover.

---

## 1. What exists today (read before changing)

| Piece | Where | Today | Problem |
|---|---|---|---|
| Navmesh | `twin/generator.py:_navmesh` | two states; floor = floor bbox's `zmin`; **one** point in the sweep band blocks a cell; unseen floor = free; Python loop over every point | a single flying pixel blocks; an unscanned hole is drivable; min-z floor breaks on one outlier; slow on millions of points |
| Lookups | `is_blocked`, `nearest_free` (same file) | `nearest_free` loops over every cell in Python | O(cells) per call |
| Floor in segmentation | `semantic/service/inference.py:161` | `points[:, 2].min()` | same outlier problem; outliers below the floor become "objects" |
| Planner | `policy/evaluate.py:plan_path` | 8-way BFS, diagonal cost 1, no clearance | zig-zag paths that hug inflated walls |
| Inflation | `policy/evaluate.py:inflate` | Python quadruple loop | slow on large grids |
| Goal / start | `orchestrator/service.py:_goal_for`, `_start_pose` | goal beside the first `navigate_to` target; start = farthest free cell | the operator cannot choose either |
| DB | `orchestrator/db.py` | one `SCHEMA`, `CREATE IF NOT EXISTS`, no versioning; `robots.kind IN ('sim','unoq')` | nowhere to store a mission; cannot add `rover` |
| Plan view | `dashboard/src/Floorplan.jsx` | object boxes + pose trace, `place()` projection | no occupancy, no interaction |
| OpenAPI | `docs/api/openapi.yaml` | 17-line stub with routes that do not exist | wrong |
| SDK | `sdk/scanborn/client.py` | no navmesh or mission; `generate_twin` cannot pass `environment` | gaps |

Callers that must keep working: `robot/adapters/sim.py` (`is_blocked`),
`robot/policy_runner.py` (`inflate`, `plan_path`, `_lookahead`, `_cap`, `GOAL_TOLERANCE`),
`tests/test_profile.py` (`free_cells`, `inflate`, `ROBOT_RADIUS`, `ROBOT_HEIGHT`),
`tests/test_twin.py`, `tests/test_policy.py`, `tests/test_robot.py`.

No real scan is on disk; `data/` holds only the seeded synthetic room (16,384 points).

---

## 2. Decisions

1. **Cell states:** `0` free, `1` blocked, `2` unknown. Every consumer that asks "can the robot
   be here?" treats any non-zero value as not traversable, so `is_blocked` and old callers stay
   correct without change. Old two-state `navmesh.json` files are still valid (no `2`s).
2. **Thresholds from sampling, not guesses.** Reconstruction fuses at a 2 cm voxel
   (`reconstruct(voxel_size=0.02)`), so a real surface puts ~25 points in a 10 cm cell.
   `MIN_POINTS = 3` is a noise floor: a cell is *blocked* with ≥ 3 points in the robot's
   height band, or ≥ 3 points more than 10 cm below the floor (a step down — a stair riser is
   15–18 cm, fused ARCore depth is within 1–2 cm). Otherwise it is *free* with ≥ 3 floor
   points, and *unknown* if neither.
3. **Floor = densest 2 cm height bin near the bottom** (`reconstruction/floor.py`), shared by
   segmentation and the navmesh. ARCore and Scaniverse clouds are gravity-aligned, so the floor
   is a spike in the z histogram; min-z moves with a single outlier.
4. **Gap fill:** a morphological close (3×3) on the free mask, applied only to unknown cells —
   fills gaps one cell wide, never overwrites blocked, never reaches into the unseen shadow
   beside furniture (a blocked neighbour stops the close).
5. **Bounding-box fallback stays two-state.** Without a cloud there is no observation, so
   "unknown" has no meaning; the fallback is unchanged and says so in its docstring.
6. **Cell size is chosen per scan, from measurements.** `scripts/bench_mission_planning.py`
   (12 × 10 m synthetic furnished room, 698,314 points, i5-13450HX, `benchmarks/planning.json`):
   a cold plan (clearance field + A\*) takes p95 8.0 ms at 0.1 m and 27.4 ms at 0.05 m, and the
   navmesh builds in ~60 ms at either — compute allows 5 cm easily. Density decides: a cell
   counts as seen with three points, so 5 cm needs a surface sampled about every 3 cm.
   `twin/navmesh.py:choose_cell` measures it — over the 10 cm cells where floor was seen, the
   25th percentile of floor points must reach 12 (four 5 cm cells' worth) — and picks 5 cm for a
   dense scan, 10 cm otherwise (the pipeline's synthetic room, sampled every 3.9 cm, stays at
   10 cm). The choice and its evidence are stored as the navmesh's `resolution`, returned by
   `/generate-twin` and `GET /twins/{id}/navmesh`, and shown on stage 04; `cell_m` (0.05 or 0.1)
   overrides it. Whether real phone scans reach the threshold is checked at M4.
7. **A\* cost = step length × (1 + 4 · clip((robot_radius + 0.30 − d) / 0.30, 0, 1))**, where
   `d` is the distance to the nearest non-free raw cell (`scipy.ndimage.distance_transform_edt`,
   map edge counts as non-free): five times the length at the inflation edge, no penalty past
   0.30 m of margin. Traversable = `d > robot_radius` (strict, like the inflation loop it
   replaces). Heuristic = octile distance (admissible, since every step costs at least its
   length). Diagonals cost √2 and may not cut a corner. Reachability uses 4-connected
   components, which is exactly what 8-way moves without corner cutting can reach.
8. **Shortcutting never lowers clearance.** A segment from waypoint *i* to *j* is accepted
   only if every cell it touches (supercover line) is traversable **and** has clearance ≥ the
   minimum clearance of the A\* path between *i* and *j*. So the reported minimum clearance
   of the final path equals the A\* path's.
9. **Two path forms:** `waypoints` (sparse corners, for display and the rover protocol) and
   `path` (resampled every cell length, for the current point-follower). Following sparse
   corners with a 0.6 m lookahead would cut corners by more than the inflation allows.
10. **Missions are immutable.** Each plan request inserts a row; a policy references the exact
    geometry it was trained on. Planning happens on discrete events (click, drag end, button),
    never per mouse move, so rows stay few.
11. **Named errors are HTTP 422** with an object detail
    `{"error": "<sentence>", "code": "start_blocked|goal_blocked|via_blocked|unreachable|no_start", "nearest": [x, y] | null, "index": i?}`.
    FastAPI validation errors are also 422 but arrive as a list; `api.js` already tells the two apart.
12. **Start defaults to the scan's home pose** — `meta.json` `home_pose: [x, y, θ]` in map
    frame — which Phase 5 writes. Until then there is none, and a mission without `start` is a
    422 `no_start`. The dashboard pre-places a start at the free cell nearest the room centre.
13. **Missions can take a goal from language:** `POST /missions` accepts `task_graph_id`
    instead of `goal`, resolved with the existing `_goal_for` (beside the first `navigate_to`
    target). Stage 05 Plan therefore feeds Stage 06 Mission without Phase 8.
14. **Stale means the path is no longer safe.** `/sync` rasterises the rescan onto the old
    twin's grid; cells the rescan did not see keep their old state. A mission is stale if its
    path no longer clears the robot's radius on that grid — something now stands in the way.
    (A corridor-flip rule cannot work: paths keep more than the radius from obstacles, so a
    radius-wide corridor never contains one.) A removed obstacle never makes a path unsafe, so
    it marks nothing stale. Each mission's report also counts corridor cells (radius + margin)
    that flipped, and cells the rescan did not see (`unverified`). The remedy is to generate a
    twin from the new scan and plan again — `/sync` does not modify the old twin — and the 409
    says exactly that. Rescans must share the first scan's frame; registration is Phase 5.
15. **Phase 2 training with a mission:** demonstrations and the 60 % random-start gate are
    unchanged, plus one rollout from the mission start along the mission path that must reach
    the goal (`mission_reached`). Phase 3 replaces this with 30 noisy mission runs at 90 %.
16. **Grids travel as base64 `uint8`, row-major, row 0 = minimum y.** A 200 × 200 grid is 53 KB
    as base64 against ~240 KB as nested JSON, and a canvas draws it in one `putImageData`.

---

## 3. Step 2.1 — Robust floor (`reconstruction/floor.py`)

**Build**
- `estimate_floor_z(points, bin_size=0.02, search=0.5) -> float`: take z values up to
  `percentile(z, 1) + search`; histogram at `bin_size`; return the median z of the points in the
  densest bin ±1 bin. Empty or all-NaN input raises `ValueError`.
- `semantic/service/inference.py:segment_points`: `floor_z = estimate_floor_z(points)`; drop
  points below `floor_z − FLOOR_SLAB` before clustering (they are outliers or a drop, never
  furniture); the floor slab is `|z − floor_z| ≤ FLOOR_SLAB`.
- `requirements.txt`: add `scipy>=1.11` (already installed: 1.15.2), with a comment naming its
  two users.

**Tests** (`tests/test_floor.py`, and `tests/test_semantic.py` extended)
- 500 outliers 1 m below a flat floor: estimate within 1 cm; min-z would be off by 1 m.
- A tilted-noise floor (σ = 5 mm) plus a table top covering 30 % of the area: floor, not the table.
- Segmentation of a room with injected low outliers: same labels as without them, and no
  object whose bbox lies below the floor.

---

## 4. Step 2.2 — Three-state navmesh (`twin/navmesh.py`)

**Build**
- `FREE, BLOCKED, UNKNOWN = 0, 1, 2`; `MIN_POINTS = 3`; `DROP = 0.10`; `FLOOR_CLEARANCE`
  and `ROBOT_HEIGHT` move here (re-exported from `generator.py` for `tests/test_profile.py`).
- `grid_frame(objects, points, cell) -> (origin, width, height)`: floor objects' xy extent,
  else all objects', else the cloud's xy bounds.
- `rasterise(points, origin, cell, width, height, floor_z, sweep_height) -> np.ndarray[uint8]`:
  vectorised — `row*width+col` indices, three `np.bincount`s (floor band, sweep band, drop),
  states as in decision 2, then gap fill (decision 4). Used by both twin generation and `/sync`.
- `build_navmesh(objects, cell, points=None, profile=None) -> dict`: replaces
  `generator._navmesh` (which becomes a one-line call). Writes
  `{"version": 2, "origin", "cell", "width", "height", "grid" (nested lists, states),
  "floor_z", "counts": {"free", "blocked", "unknown"}, "profile"}`. The bbox fallback writes
  the same keys with `floor_z: null` and `unknown: 0`.
- `as_array(navmesh) -> np.ndarray`, `is_blocked` (unchanged contract), `nearest_free`
  (vectorised over free cells, same signature and return), `encode_cells(array) -> str`
  (base64), `decode_cells(text, width, height)`.
- `twin/generator.py`: import from `twin.navmesh` and re-export `is_blocked`, `nearest_free`,
  `FLOOR_CLEARANCE`, `ROBOT_HEIGHT`; `generate_twin` also returns `floor_z` and `counts`.
- `robot/adapters/sim.py`, `orchestrator/service.py`: import from `twin.navmesh`.

**Tests** (`tests/test_navmesh.py`; `tests/test_profile.py` updated)
- Vectorised rasteriser equals a slow reference implementation written in the test, on a
  seeded random cloud with walls, furniture and noise.
- A scan with a 0.5 m unscanned hole: hole cells `UNKNOWN`, `is_blocked` true there.
- A gap one cell wide is filled; a 3 × 3 hole is not; the unseen strip at an obstacle's foot
  stays unknown and the obstacle stays blocked.
- One or two stray points in the sweep band do not block; three do.
- A 20 cm drop in the floor blocks its cells.
- Legacy two-state navmesh: `is_blocked`, `nearest_free` unchanged.
- `nearest_free` never returns an unknown cell; matches the old loop on a random grid.
- Encode/decode round-trips; row 0 is minimum y.
- `tests/test_profile.py`: the synthetic `open_room` and shelf clouds are resampled at 5 cm,
  cell-centred (0.025 m offset, so no point sits on a cell edge), which gives real surfaces
  ≥ 3 points per cell. Assertions stay as they are.

---

## 5. Step 2.3 — Planning (`policy/planning.py`) and the benchmark

**Build**
- `robot_radius(navmesh)` — from its profile, else `ROBOT_RADIUS` (moved here, re-exported
  from `evaluate.py`).
- `clearance_field(navmesh) -> np.ndarray` metres to the nearest non-free cell (outside the
  grid counts as non-free: pad by one blocked ring before the transform).
- `inflate(navmesh, radius=None) -> dict` — same contract as today (a navmesh dict whose grid
  marks every cell with `d < radius` as 1, keeping `2` where the raw grid was unknown),
  computed from the field instead of loops.
- `astar(navmesh, start, goal, field=None) -> list[(row, col)]` per decision 7.
- `shortcut(cells, field, radius) -> list[(row, col)]` per decision 8, with a supercover line.
- `densify(points, step) -> list[(x, y)]`.
- `nearest_valid(field, radius, x, y, reachable_from=None)` — nearest traversable cell centre,
  optionally restricted to the start's connected component (`scipy.ndimage.label`, 8-connected).
- `class MissionError(ValueError)` with `code`, `nearest`, `index`.
- `plan_mission(navmesh, start, goal, via=()) -> dict`:
  `{"start": [x, y, θ], "goal": [x, y], "via": [...], "waypoints": [[x, y], …],
  "path": [[x, y], …], "length_m", "min_clearance_m", "plan_ms"}`. Legs are planned in
  order and joined. `θ` defaults to the first segment's heading. The exact start and goal
  replace their cell centres.
- A per-process cache of `clearance_field` keyed by `(navmesh path, mtime, radius)` in the
  service, so repeated clicks on one twin skip the distance transform.
- `policy/evaluate.py`: import `inflate`, `ROBOT_RADIUS` from planning; `plan_path(navmesh,
  start, goal)` keeps its signature and returns `densify` of the A\* path (so every rollout
  benefits from clearance); `rollout(..., path=None, trace=None)` follows a fixed route and can
  record its trace. Demonstrations still plan per random start, so they take no path.
- `robot/policy_runner.py`: `run_policy(..., path=None)` — use the mission path when given.
- `scripts/bench_mission_planning.py`: builds a 12 × 10 m furnished room (walls, 8 pieces of
  furniture, a doorway, a 0.4 m unscanned hole, 1 % outliers) at 2 cm sampling (~0.4 M points);
  times floor estimate, rasterise, clearance field and 20 seeded `plan_mission` calls at
  0.1 m and 0.05 m; writes `benchmarks/planning.json` with the CPU model, Python and NumPy
  versions, and `"source": "synthetic room, not a scan"`.

**Tests** (`tests/test_planning.py`; `tests/test_policy.py`, `tests/test_robot.py` still pass)
- Open room: path length within one cell of the straight-line distance; shortcut reduces it to
  2 waypoints.
- A 1 m pillar between start and goal: the mission's minimum clearance ≥ 0.45 m; a BFS route on
  the same inflated grid, computed in the test, comes within 0.35 m.
- Shortcut never crosses an inflated cell and never lowers minimum clearance — 50 seeded random
  maps.
- No corner cutting on diagonals.
- Via points are visited in order.
- Errors: start on a wall → `start_blocked` with a traversable `nearest`; goal on unknown →
  `goal_blocked`; goal inside a closed ring → `unreachable` with `nearest` in the start's
  component; start == goal → a one-point path.
- 200 × 200 grid with obstacles plans in < 1 s (loose bound for CI; the benchmark records
  the real number).

---

## 6. Step 2.4 — Database version 2 (`orchestrator/db.py`)

**Build**
- `SCHEMA` stays the version-1 baseline. `MIGRATIONS = [(2, _to_v2)]`. `init_db` runs `SCHEMA`,
  reads `PRAGMA user_version` (0 counts as 1, which is today's dev database), applies each
  later migration and sets the version. Fresh and old databases take the same path, so the
  migration is exercised by every test.
- `_to_v2(conn)`:
  1. `CREATE TABLE missions(id, twin_id NOT NULL → twins, task_graph_id → task_graphs,
     start_json, goal_json, via_json DEFAULT '[]', waypoints_json, path_json, length_m,
     min_clearance_m, status CHECK IN ('ready','stale') DEFAULT 'ready', stale_reason,
     created_at)`.
  2. `CREATE TABLE runs(id, mission_id NOT NULL → missions, policy_id → policies,
     deployment_id → deployments, kind CHECK IN ('sim','unoq','fake_rover','rover'), reached INTEGER,
     trace_json, metrics_json, created_at)`.
  3. `ALTER TABLE policies ADD COLUMN mission_id TEXT REFERENCES missions(id)`.
  4. Rebuild `robots` with `kind IN ('sim','unoq','rover')`: `PRAGMA foreign_keys=OFF`,
     `BEGIN`, create `robots_v2`, copy, drop, rename, `PRAGMA foreign_key_check` (must be
     empty, else roll back and raise), `COMMIT`, `PRAGMA foreign_keys=ON` — SQLite's
     documented 12-step table rebuild.
- `TABLES` is computed from `SCHEMA` plus the migration SQL (the temporary `robots_v2` uses
  `CREATE TABLE` without `IF NOT EXISTS`, so it is not picked up).
- `update(conn, table, row_id, **cols)` helper (identifier-checked like `insert`).

**Tests** (`tests/test_db.py`)
- A fresh database is at version 2 with `missions` and `runs`.
- A version-1 file (created with `SCHEMA` only, holding a robot and a deployment that
  references it) migrates: rows preserved, `deployments → robots` still enforced,
  `kind='rover'` accepted, `kind='bogus'` rejected, `foreign_key_check` empty.
- `init_db` twice is a no-op. `db.TABLES` includes the new tables.

---

## 7. Step 2.5 — Endpoints (`orchestrator/service.py`, CRLF)

Request bodies for new routes are Pydantic models, so the generated OpenAPI documents them.

| Route | Request | Response |
|---|---|---|
| `GET /twins/{id}/navmesh` | — | `{twin_id, version, origin, cell, width, height, floor_z, counts, profile, home_pose, cells_b64, band_b64}` — `band` marks free cells closer than the robot radius |
| `POST /missions` | `{twin_id, goal?: [x,y], task_graph_id?, start?: [x,y] or [x,y,θ], via?: [[x,y]…]}` (exactly one of `goal`, `task_graph_id`) | 200 `{mission_id, twin_id, status, start, goal, via, waypoints, path, length_m, min_clearance_m, plan_ms}`; 404 unknown twin; 422 named error (decision 11) |
| `GET /missions/{id}` | — | same shape + `stale_reason` |
| `POST /train` | adds `mission_id?` | adds `mission_id`, `mission_reached`, `mission_trace`; 409 `{error, code: "mission_failed", …}` if the mission rollout fails; 400 if the mission belongs to another twin |
| `POST /deploy` | — | a policy with a mission drives from the mission start along its path; 409 `{code: "mission_stale"}` for a stale mission; a `runs` row per deploy |
| `POST /sync` | — | adds `stale_missions: [{mission_id, stale, min_clearance_m, changed_cells, unverified_cells}]` |

- `home_pose`: `twins.mesh_id → meshes.scan_id → store.scan_dir/meta.json`, validated as three
  finite numbers, else `null`.
- `/train` with a mission writes a `runs` row (`kind='sim'`) with the mission rollout's trace.
- `_start_pose` and `_goal_for` stay for the no-mission path, which the existing tests and the
  fixture use.

**Tests** (`tests/test_missions.py`; `tests/test_orchestrator.py` unchanged and passing)
- Navmesh endpoint on the synthetic room: decoded cells match `navmesh.json`; the table's cells
  are blocked; counts add up; `home_pose` is null.
- A mission from a free start to a free goal: path starts at start, ends at goal,
  `min_clearance_m ≥ robot_radius`.
- Goal on the table → 422 `goal_blocked` with a free `nearest`; missing start → 422
  `no_start`; unknown twin → 404; `goal` and `task_graph_id` together → 422.
- `task_graph_id` "go to the table" resolves to a goal beside the table.
- `home_pose` in `meta.json` is used when `start` is omitted.
- Train with `mission_id` → `policies.mission_id` set, `mission_reached` true, a `runs` row.
- Deploy of that policy → trace begins at the mission start.
- Rescan with a new box on a mission's path → stale; deploy → 409 `mission_stale`; a mission
  away from the box stays ready; a rescan that only removes the table marks nothing stale.
- Evaluation never starts an episode inside the goal tolerance (it scored a do-nothing policy
  at 5 % once starts were sampled from the edge-aware inflation).

---

## 8. Steps 2.6–2.8 — OpenAPI, SDK, dashboard

**2.6 OpenAPI**
- `scripts/export_openapi.py` writes `docs/api/openapi.yaml` from `app.openapi()`
  (`yaml.safe_dump(sort_keys=False)`), with a header comment naming the command.
- `tests/test_openapi.py`: the committed file parses and lists exactly the app's paths and
  methods. Paths are compared rather than the whole document, so a FastAPI upgrade does not
  fail the test on formatting.

**2.7 SDK** (`sdk/scanborn/client.py`, `tests/test_sdk.py`)
- `navmesh(twin_id)`, `mission(twin_id, goal=None, start=None, via=None,
  task_graph_id=None)`, `get_mission(mission_id)`, `train(..., mission_id=None)`,
  `generate_twin(..., environment=None)`, module helper `decode_cells(navmesh, key="cells_b64")`.
- `run_pipeline(..., goal=None, start=None)` plans a mission when a goal is given.

**2.8 Dashboard**
- `api.js`: `navmesh`, `createMission`, `mission`; `train(twinId, graphId, missionId)`.
- New `navmesh.js` (pure, tested): `STATE`, `decodeCells`, `cellOf`, `stateAt` (outside =
  blocked), `gridPixels(nav, cells, band)` → RGBA array, `headingFromDrag`.
- `pipeline.js`: `toPipe.navmesh`, `toPipe.mission`; `EMPTY_PIPE` gains `navmesh`, `mission`;
  `STAGE_ORDER` becomes capture, reconstruct, segment, twin, navmesh, plan, mission, train,
  optimize, deploy. The twin stage fetches the navmesh right after generating.
- `MissionMap.jsx` replaces `Floorplan.jsx` (deleted). `place()` is unchanged. Layers, bottom
  up: the occupancy image (`<image>` from a canvas: free / blocked / unknown hatched /
  inflation band), object outlines and labels, planned path with waypoint dots, simulated trace,
  real trace (empty until Phase 4), start marker with heading arrow, via markers (numbered),
  goal marker. Interaction: click = goal; drag from the start marker = move start, drag
  direction = heading; Alt-click = add via; click a via = remove. A click on a blocked or
  unknown cell is refused on the spot with a note, and no request is sent. Pointer-up triggers
  planning; mouse moves never do. Keyboard alternative: the mission panel's numeric fields.
- `Console.jsx`: new Stage **06 Mission** (Plan becomes 05, Train 07, Optimize 08,
  Deploy 09). Fields: start x / y / θ, goal x / y, via list, "Goal from the plan" (uses
  `task_graph_id`), "Plan path". Output: `path 4.2 m · clearance 0.31 m · 3 waypoints · 38 ms`.
  A 422 with `nearest` shows "Use nearest valid point". Train sends `mission_id` when set, and
  its hint describes the mission rollout. The view tab "Plan view (2D)" becomes "Mission map",
  and generating a twin switches to it.
- `RobotViewer3D.jsx`: the occupancy image as a floor texture and the planned path as a tube
  (radius 2 cm) — this also closes Phase 1's item 1.12 (route legibility).
- `vite.config.ts`: proxy `/twins` and `/missions`.
- Fixture: `record_dashboard_fixture.py` records `navmesh` and `mission` (goal beside the
  table, start at the free cell nearest the room corner) and trains with the mission;
  re-record `fixtures.json`.
- `styles.css`: map layers and markers using the existing tokens; `svg.plan` rules carried over.
- Vitest: `navmesh.test.js` (decode, orientation, `stateAt` outside, pixels), `pipeline.test.js`
  (new mappers, order), `api.test.js` (new calls; a 422 object detail's message is its
  `error`, and `nearest` survives).

---

## 9. What Phase 3 inherits

- `plan_mission` output: `start` with heading, sparse `waypoints` (the rover protocol's
  payload in Phase 4), dense `path`, `min_clearance_m`.
- `clearance_field` — Phase 3's collision check (robot centre vs grid grown by the footprint
  radius) is `field < footprint_radius`.
- `missions` and `runs` tables — Phase 3 adds noisy mission runs as `runs` rows and replaces
  decision 15 with `MISSION_GATE = 0.9`.
- `run_policy(..., path=)` and `rollout(..., path=)` are the seams the differential-drive
  simulator replaces.

---

## 10. Order, verification, done

Order: 2.1 → 2.2 → 2.3 (benchmark, decide cell size) → 2.4 → 2.5 → 2.6 → 2.7 → 2.8 →
browser check. Each step ends with its tests green and `flake8` clean before the next.

**Verification**
```
python -m pytest -q -o addopts=""
python -m flake8 sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests scripts
python -m mypy sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests
python scripts/bench_mission_planning.py
cd dashboard; npm test; npm run build
```

**Browser check** (orchestrator + dashboard preview servers, `scripts/seed_synthetic_scans.py`):
run stages 01–04; the map shows free / blocked / unknown; clicking the table is refused;
clicking open floor draws a path within a second (timed from the network panel); dragging the
start re-plans and turns the heading arrow; Alt-click adds a via point; Train with the mission
passes; Deploy's trace starts at the start; rescan without the table → the mission is marked
stale → Deploy is refused with the remedy message; the offline fixture shows the map and path.

**Done when** (master plan): tapping a goal shows a path in under a second, and a start or goal
cannot be placed on a blocked or unknown cell.
- Verified in Phase 2 on the synthetic room and the benchmark room, with the timings recorded.
- **Not verifiable in Phase 2: "on a real scan".** No real scan exists yet. This box stays
  open until M4 (phone capture) or a Scaniverse export of a real room is imported, and the
  master plan says so.

---

## 11. Outcome (2026-09-14)

Built as planned, verified as recorded in the master plan's Phase 2 "Verified" block. Where the
build departed from this plan, and why:

| Planned | Built | Why |
|---|---|---|
| Stale if a corridor cell flips | Stale if the path no longer clears the robot's radius on the rescan (unseen cells keep their old state) | Paths keep more than the radius from obstacles, so a radius-wide corridor never contains one: the planned rule could not fire |
| `DROP` 5 cm | 10 cm | A stair riser is 15–18 cm; fused depth noise is 1–2 cm |
| A\* penalty normalised by `radius + 0.3` | Normalised by the 0.3 m margin (×5 at the inflation edge) | A clear, bounded weight; the old form capped at ×3.2 |
| `record_demos(path=)` | Only `rollout(path=, trace=)` | Demonstrations start from random cells, each needing its own route |
| `runs.kind` sim / fake_rover / rover | Adds `unoq` | A mission deployed to the existing UNO Q adapter would otherwise fail the CHECK |
| — | Evaluation skips starts inside the goal tolerance | Edge-aware inflation changed the sampled starts and exposed a do-nothing policy scoring 5 % |
| — | Segmentation uses the same robust floor | Its min-z floor had the same outlier failure |
| — | `/deploy` answers 400 for a robot kind with no adapter | The v2 schema accepts `rover` before its adapter exists |
| Cell size decided by the benchmark alone | Chosen per scan from floor density; `cell_m` overrides (decision 6) | Compute allows 5 cm everywhere; the three-point threshold only allows it where the scan is dense |
| Live rover marker on the map | Phase 4 | Needs streamed telemetry |

Open from this phase: the 3D pixel check (pane was not drawing) and "on a real scan" (M4).
