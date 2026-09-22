# The metadata database: versions, upgrades and going back

ScanBorn keeps its metadata in one SQLite file (`SCANBORN_DB`, default `data/scanborn.db`); scans,
meshes, twins and artifacts are files under `SCANBORN_DATA`. `orchestrator/db.py` holds the schema:
`SCHEMA` is version 1 and never changes, and every later change is a migration recorded in
`PRAGMA user_version`. A fresh file and an old one take the same path.

## Versions

| Version | Change | Kind |
|---|---|---|
| 1 | Baseline: scans, meshes, semantic objects, twins, task graphs, demonstrations, policies, artifacts, robots, deployments, sync events, telemetry | — |
| 2 | `missions`, `runs`; `policies.mission_id`; `robots.kind` widened to include `rover` (table rebuilt with foreign keys checked) | additive |
| 3 | `twins.unity_scene_url` renamed to `scene_url` | **rename** |
| 4 | `jobs`, `request_results`, `artifact_validations`, `calibration_sessions` | additive |
| 5 | `provider_usage`; `task_graphs.provenance_json`; `twins.profile_provenance_json` | additive |

Old values are kept as they were: `unoq` robots, `function_gemma` task graphs and the
`fidelity` mesh mode still pass their CHECK constraints so an old file opens, even though current
code no longer creates them. New columns start NULL and new tables start empty; nothing is
back-filled with invented history.

## Upgrading

Starting the orchestrator upgrades the file in place. **Before the first migration it copies a
file that holds any data** to `<database>.v<old version>.bak` using SQLite's backup API (one copy
per starting version, never overwritten). An empty file is not copied. Upgrading twice is a no-op.
`tests/test_migrations.py` fills version 1–4 files with one row of everything and checks that every
ID survives, the copy is taken, a second start changes nothing, and the old rows are still served.

## Going back

| Code you go back to | Opens a version-5 file? | Why |
|---|---|---|
| Version 3 or 4 code | Yes | Its `init_db` sees a newer `user_version` and changes nothing; it ignores the tables and columns it does not know |
| Version 1 or 2 code | **No** | It reads and writes `twins.unity_scene_url`, which version 3 renamed |

To go back past version 3, stop the orchestrator and restore the copy taken before the upgrade
(`scanborn.db.v2.bak` or `scanborn.db.v1.bak`) over `scanborn.db`. Work recorded after the upgrade is
not in that copy. Running several orchestrator processes against one file is not supported.
