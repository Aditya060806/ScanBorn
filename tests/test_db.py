import sqlite3

import pytest

from orchestrator import db

TABLES = {
    "teams", "scans", "meshes", "semantic_objects", "twins", "task_graphs",
    "demonstrations", "policies", "artifacts", "robots", "deployments",
    "sync_events", "telemetry", "missions", "runs",
}


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_all_tables_exist(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert TABLES <= {r["name"] for r in rows}


def test_insert_get_roundtrip(conn):
    scan_id = db.insert(conn, "scans", device="SM-S721B", status="uploading")
    row = db.get(conn, "scans", scan_id)
    assert row["device"] == "SM-S721B"
    assert row["status"] == "uploading"
    assert row["created_at"]


def test_fk_violation_raises(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "meshes", scan_id="nope", mode="fast")


def test_schema_tables_match_constant():
    assert db.TABLES == TABLES | {
        "jobs", "request_results", "artifact_validations", "calibration_sessions",
        "provider_usage"}


def test_a_fresh_database_is_at_the_latest_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(policies)")}
    assert "mission_id" in columns
    twin_columns = {r["name"] for r in conn.execute("PRAGMA table_info(twins)")}
    assert "scene_url" in twin_columns and "unity_scene_url" not in twin_columns
    assert "profile_provenance_json" in twin_columns
    graph_columns = {r["name"] for r in conn.execute("PRAGMA table_info(task_graphs)")}
    assert "provenance_json" in graph_columns
    db.insert(conn, "robots", kind="rover")


def _version_one_file(path: str) -> None:
    """A database as the orchestrator wrote it before versioning: a robot with a deployment."""
    old = sqlite3.connect(path)
    old.execute("PRAGMA foreign_keys = ON")
    old.executescript(db.SCHEMA)
    for sql in (
        "INSERT INTO scans(id) VALUES ('s')",
        "INSERT INTO meshes(id, scan_id, mode) VALUES ('m', 's', 'fast')",
        "INSERT INTO twins(id, mesh_id, unity_scene_url) VALUES ('t', 'm', 'twins/m/scene.json')",
        "INSERT INTO policies(id, twin_id) VALUES ('p', 't')",
        "INSERT INTO artifacts(id, policy_id) VALUES ('a', 'p')",
        "INSERT INTO robots(id, kind, config_json) VALUES ('r', 'unoq', '{}')",
        "INSERT INTO deployments(id, artifact_id, robot_id, status) VALUES ('d', 'a', 'r', 'ok')",
    ):
        old.execute(sql)
    old.commit()
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    old.close()


def test_a_version_one_database_file_migrates(tmp_path):
    path = str(tmp_path / "v1.db")
    _version_one_file(path)

    conn = db.connect(path)
    db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert db.get(conn, "twins", "t")["scene_url"] == "twins/m/scene.json"
    assert db.get(conn, "twins", "t")["profile_provenance_json"] is None  # additive column
    assert db.get(conn, "robots", "r")["kind"] == "unoq"
    assert db.get(conn, "robots", "r")["config_json"] == "{}"
    assert db.get(conn, "deployments", "d")["robot_id"] == "r"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    db.insert(conn, "robots", id="rover-1", kind="rover")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "robots", kind="bogus")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "deployments", artifact_id="a", robot_id="no-such-robot")
    conn.rollback()

    mission = db.insert(conn, "missions", twin_id="t", start_json="[0, 0, 0]",
                        goal_json="[1, 1]", waypoints_json="[]", path_json="[]")
    db.insert(conn, "runs", mission_id=mission, policy_id="p", kind="sim", reached=1)
    db.update(conn, "policies", "p", mission_id=mission)
    assert db.get(conn, "policies", "p")["mission_id"] == mission

    db.init_db(conn)  # a second start-up is a no-op
    assert conn.execute("SELECT COUNT(*) FROM robots").fetchone()[0] == 2
    conn.close()


def test_update_changes_only_the_named_columns(conn):
    scan_id = db.insert(conn, "scans", device="phone", status="uploading")
    db.update(conn, "scans", scan_id, status="complete")
    row = db.get(conn, "scans", scan_id)
    assert row["status"] == "complete" and row["device"] == "phone"
    with pytest.raises(ValueError):
        db.update(conn, "scans", scan_id, **{"status = 'x' --": "y"})


def test_identifiers_are_rejected_not_interpolated(conn):
    """Table and column names cannot be bound as parameters, so they are guarded."""
    with pytest.raises(ValueError):
        db.get(conn, "scans; DROP TABLE scans", "x")
    with pytest.raises(ValueError):
        db.insert(conn, "nonexistent", device="x")
    with pytest.raises(ValueError):
        db.insert(conn, "scans", **{"device) VALUES ('x'); --": "x"})
    # the guard did not take the table down with it
    assert db.get(conn, "scans", "missing") is None
