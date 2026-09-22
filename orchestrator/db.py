"""Shared metadata store (blueprint section 17.2), SQLite.

`SCHEMA` is the version-1 baseline and is never edited. Every later change is a migration in
MIGRATIONS, applied in order by `init_db` and recorded in `PRAGMA user_version`. A fresh
database and an old one take the same path, so every test run exercises the migrations too.
"""
import os
import re
import sqlite3
import threading
import uuid
from contextlib import closing

DB_PATH = os.environ.get("SCANBORN_DB", "data/scanborn.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams(
    id TEXT PRIMARY KEY, name TEXT, members_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS scans(
    id TEXT PRIMARY KEY, team_id TEXT REFERENCES teams(id),
    device TEXT, capture_frame_json TEXT,
    created_at TEXT DEFAULT (datetime('now')), status TEXT);
CREATE TABLE IF NOT EXISTS meshes(
    id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(id),
    mode TEXT CHECK (mode IN ('fast','fidelity')),
    glb_url TEXT, ply_url TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS semantic_objects(
    id TEXT PRIMARY KEY, mesh_id TEXT NOT NULL REFERENCES meshes(id),
    label TEXT, bbox3d_json TEXT, mask_url TEXT, confidence REAL,
    embedding_id TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS twins(
    id TEXT PRIMARY KEY, mesh_id TEXT NOT NULL REFERENCES meshes(id),
    unity_scene_url TEXT, navmesh_url TEXT, anchor_transform_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS task_graphs(
    id TEXT PRIMARY KEY, twin_id TEXT NOT NULL REFERENCES twins(id),
    source_text TEXT, lang TEXT,
    -- Matches SCANBORN_PLANNER, so the row records which tier answered.
    -- 'function_gemma' is the pre-profile name for 'local' and is kept so an
    -- existing dev database still validates.
    provider TEXT CHECK (provider IN ('local','groq','sarvam','function_gemma')),
    graph_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS demonstrations(
    id TEXT PRIMARY KEY, twin_id TEXT NOT NULL REFERENCES twins(id),
    task_graph_id TEXT REFERENCES task_graphs(id),
    trajectory_url TEXT, recorded_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS policies(
    id TEXT PRIMARY KEY, twin_id TEXT NOT NULL REFERENCES twins(id),
    task_graph_id TEXT REFERENCES task_graphs(id),
    base_checkpoint TEXT, finetuned_ckpt_url TEXT, sim_success_rate REAL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies(id),
    device_label TEXT, precision TEXT, op_coverage_pct REAL,
    est_latency_ms REAL, artifact_url TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS robots(
    id TEXT PRIMARY KEY, team_id TEXT REFERENCES teams(id),
    kind TEXT CHECK (kind IN ('sim','unoq')), config_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS deployments(
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    robot_id TEXT NOT NULL REFERENCES robots(id), status TEXT,
    deployed_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS sync_events(
    id TEXT PRIMARY KEY, twin_id TEXT NOT NULL REFERENCES twins(id),
    new_scan_id TEXT REFERENCES scans(id), diff_summary_json TEXT,
    triggered_by TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS telemetry(
    id TEXT PRIMARY KEY, deployment_id TEXT REFERENCES deployments(id),
    kind TEXT CHECK (kind IN ('image','log','failure')), payload_url TEXT,
    created_at TEXT DEFAULT (datetime('now')));
"""

# Version 2 — missions (a start, a goal and the path between them on one twin) and runs (one
# drive of a mission, simulated or real).
V2_TABLES = """
CREATE TABLE IF NOT EXISTS missions(
    id TEXT PRIMARY KEY, twin_id TEXT NOT NULL REFERENCES twins(id),
    task_graph_id TEXT REFERENCES task_graphs(id),
    start_json TEXT NOT NULL, goal_json TEXT NOT NULL, via_json TEXT NOT NULL DEFAULT '[]',
    waypoints_json TEXT NOT NULL, path_json TEXT NOT NULL,
    length_m REAL, min_clearance_m REAL,
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','stale')),
    stale_reason TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS runs(
    id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
    policy_id TEXT REFERENCES policies(id),
    deployment_id TEXT REFERENCES deployments(id),
    kind TEXT CHECK (kind IN ('sim','unoq','fake_rover','rover')),
    reached INTEGER, trace_json TEXT, metrics_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
"""

# Not IF NOT EXISTS, so TABLES below does not mistake the temporary name for a real table.
ROBOTS_V2 = """
CREATE TABLE robots_v2(
    id TEXT PRIMARY KEY, team_id TEXT REFERENCES teams(id),
    kind TEXT CHECK (kind IN ('sim','unoq','rover')), config_json TEXT,
    created_at TEXT DEFAULT (datetime('now')))
"""


def _rebuild_robots(conn: sqlite3.Connection) -> None:
    """Widen robots.kind to include 'rover'.

    SQLite cannot alter a CHECK constraint, so the table is rebuilt: SQLite's documented
    procedure, with foreign keys off for the swap (deployments reference robots) and checked
    before commit, so a broken reference rolls the whole rebuild back.
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction
    try:
        conn.execute("BEGIN")
        conn.execute(ROBOTS_V2)
        conn.execute("INSERT INTO robots_v2(id, team_id, kind, config_json, created_at) "
                     "SELECT id, team_id, kind, config_json, created_at FROM robots")
        conn.execute("DROP TABLE robots")
        conn.execute("ALTER TABLE robots_v2 RENAME TO robots")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise sqlite3.IntegrityError(f"robots rebuild broke {len(broken)} foreign keys")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _to_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(V2_TABLES)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(policies)")}
    if "mission_id" not in columns:
        conn.execute("ALTER TABLE policies ADD COLUMN mission_id TEXT REFERENCES missions(id)")
    _rebuild_robots(conn)


def _to_v3(conn: sqlite3.Connection) -> None:
    """twins.unity_scene_url -> scene_url. No Unity project exists; scene.json is a generic
    manifest (twin/generator.py), and the name said otherwise."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(twins)")}
    if "unity_scene_url" in columns:
        conn.execute("ALTER TABLE twins RENAME COLUMN unity_scene_url TO scene_url")


V4_TABLES = """
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, stage TEXT NOT NULL, status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0, detail_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS request_results(
    id TEXT PRIMARY KEY, route TEXT NOT NULL, payload_hash TEXT NOT NULL,
    response_json TEXT, status TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS artifact_validations(
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    validation_json TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS calibration_sessions(
    id TEXT PRIMARY KEY, robot_id TEXT NOT NULL REFERENCES robots(id),
    status TEXT NOT NULL, provenance TEXT NOT NULL, result_json TEXT,
    created_at TEXT DEFAULT (datetime('now')));
"""


def _to_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(V4_TABLES)


# Version 5 — provider provenance on stored results, and the persistent remote-request budget
# (orchestrator/providers.py). Additive only: existing rows keep NULL provenance.
V5_TABLES = """
CREATE TABLE IF NOT EXISTS provider_usage(
    id TEXT PRIMARY KEY, day TEXT NOT NULL, provider TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0, prompt_tokens INTEGER, completion_tokens INTEGER);
"""


def _to_v5(conn: sqlite3.Connection) -> None:
    conn.executescript(V5_TABLES)
    for table, column in (("task_graphs", "provenance_json"),
                          ("twins", "profile_provenance_json")):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


MIGRATIONS = ((2, _to_v2), (3, _to_v3), (4, _to_v4), (5, _to_v5))
SCHEMA_VERSION = MIGRATIONS[-1][0]

# The orchestrator opens a connection per request on a thread pool, so the first requests
# after an upgrade could otherwise race to migrate the same file. One process only: running
# several orchestrator processes on one database is not supported.
_MIGRATION_LOCK = threading.Lock()


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    # 0 is a database created before versioning existed, which is version 1.
    return max(1, int(conn.execute("PRAGMA user_version").fetchone()[0]))


def _holds_data(conn: sqlite3.Connection) -> bool:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return any(conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()  # nosec B608
               for name in tables if _IDENTIFIER.match(name))


def backup_before_migration(conn: sqlite3.Connection) -> str | None:
    """Copy a database that holds data before migrating it; returns the copy's path.

    Migrations only add, except v3, which renames twins.unity_scene_url: code older than v3
    cannot open an upgraded file, so the copy is the way back (docs/api/database.md). One copy
    per starting version; an existing copy is never overwritten.
    """
    path = next((row[2] for row in conn.execute("PRAGMA database_list")
                 if row[1] == "main"), "")
    if not path or not _holds_data(conn):
        return None
    target = f"{path}.v{schema_version(conn)}.bak"
    if not os.path.exists(target):
        with closing(sqlite3.connect(target)) as copy:
            conn.backup(copy)
    return target


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if schema_version(conn) >= SCHEMA_VERSION:
        return
    with _MIGRATION_LOCK:
        if schema_version(conn) < SCHEMA_VERSION:
            backup_before_migration(conn)
        for target, migrate in MIGRATIONS:
            if schema_version(conn) < target:
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {int(target)}")
                conn.commit()


def new_id() -> str:
    return uuid.uuid4().hex


# Table and column names cannot be bound as SQL parameters, so they are
# interpolated. Every identifier is checked against the schema below first —
# callers pass literals today, but this keeps a future caller from routing a
# request field into an identifier position.
TABLES = frozenset(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                              SCHEMA + V2_TABLES + V4_TABLES + V5_TABLES))
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_table(table: str) -> str:
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}")
    return table


def _check_columns(cols) -> None:
    bad = [c for c in cols if not _IDENTIFIER.match(c)]
    if bad:
        raise ValueError(f"invalid column name(s): {bad}")


def insert(conn: sqlite3.Connection, table: str, **cols) -> str:
    row_id = cols.pop("id", None) or new_id()
    cols["id"] = row_id
    _check_table(table)
    _check_columns(cols)
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table}({keys}) VALUES ({marks})",  # nosec B608 - identifiers checked
        list(cols.values()),
    )
    conn.commit()
    return row_id


def update(conn: sqlite3.Connection, table: str, row_id: str, **cols) -> None:
    _check_table(table)
    _check_columns(cols)
    if not cols:
        return
    assignments = ", ".join(f"{name} = ?" for name in cols)
    conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ?",  # nosec B608 - identifiers checked
        [*cols.values(), row_id],
    )
    conn.commit()


def get(conn: sqlite3.Connection, table: str, row_id: str):
    row = conn.execute(
        f"SELECT * FROM {_check_table(table)} WHERE id = ?",  # nosec B608 - table checked
        (row_id,),
    ).fetchone()
    return dict(row) if row else None
