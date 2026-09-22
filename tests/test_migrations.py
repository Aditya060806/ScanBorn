"""Upgrading populated older databases (execution plan E0).

Each starting version is filled with rows the orchestrator of that time wrote, then opened by the
current code: every ID and every history row must survive, the upgrade must be repeatable, the
database must be copied first, and the old rows must still be served. docs/api/database.md
describes which older code can open an upgraded file.
"""
import os
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from orchestrator import db


def v1_rows(conn):
    """One of everything the version-1 orchestrator could store, as it stored it (the twin's
    scene column is named as the database's own version named it)."""
    twin_columns = {row[1] for row in conn.execute("PRAGMA table_info(twins)")}
    scene = "unity_scene_url" if "unity_scene_url" in twin_columns else "scene_url"
    statements = [
        "INSERT INTO teams(id, name, members_json) VALUES ('team', 'lab', '[]')",
        "INSERT INTO scans(id, team_id, device, status) VALUES ('scan', 'team', 'SM-S721B', "
        "'complete')",
        "INSERT INTO meshes(id, scan_id, mode, ply_url) VALUES ('mesh', 'scan', 'fast', 'm.ply')",
        "INSERT INTO semantic_objects(id, mesh_id, label, bbox3d_json, confidence) "
        "VALUES ('obj', 'mesh', 'table', '[0,0,0,1,1,1]', 0.6)",
        f"INSERT INTO twins(id, mesh_id, {scene}, navmesh_url) "
        "VALUES ('twin', 'mesh', 'twins/scene.json', 'twins/navmesh.json')",
        "INSERT INTO task_graphs(id, twin_id, source_text, lang, provider, graph_json) "
        "VALUES ('graph', 'twin', 'go to the table', 'en', 'function_gemma', '{\"nodes\": []}')",
        "INSERT INTO demonstrations(id, twin_id, task_graph_id, trajectory_url) "
        "VALUES ('demo', 'twin', 'graph', 'd.npz')",
        "INSERT INTO policies(id, twin_id, task_graph_id, finetuned_ckpt_url, sim_success_rate) "
        "VALUES ('policy', 'twin', 'graph', 'p.npz', 0.7)",
        "INSERT INTO artifacts(id, policy_id, device_label, precision) "
        "VALUES ('artifact', 'policy', 'Snapdragon X Elite CRD', 'int8')",
        "INSERT INTO robots(id, kind, config_json) VALUES ('uno', 'unoq', '{}')",
        "INSERT INTO robots(id, kind, config_json) VALUES ('sim', 'sim', '{}')",
        "INSERT INTO deployments(id, artifact_id, robot_id, status) "
        "VALUES ('deployment', 'artifact', 'uno', 'offline')",
        "INSERT INTO sync_events(id, twin_id, new_scan_id, diff_summary_json, triggered_by) "
        "VALUES ('sync', 'twin', 'scan', '{}', 'manual')",
        "INSERT INTO telemetry(id, deployment_id, kind, payload_url) "
        "VALUES ('tele', 'deployment', 'log', 'log.txt')",
    ]
    for statement in statements:
        conn.execute(statement)
    conn.commit()


def snapshot(conn) -> dict:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]
    return {name: sorted(row[0] for row in conn.execute(f"SELECT id FROM {name}"))  # nosec B608
            for name in tables}


def open_at(path, version: int):
    """A database file at `version`, built with the migrations as they were."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA)
    for target, migrate in db.MIGRATIONS:
        if target <= version:
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()
    return conn


def upgrade(path) -> sqlite3.Connection:
    conn = db.connect(str(path))
    db.init_db(conn)
    return conn


def test_a_populated_version_one_database_keeps_every_row(tmp_path):
    path = tmp_path / "v1.db"
    with closing(open_at(path, 1)) as old:
        v1_rows(old)
        before = snapshot(old)

    with closing(upgrade(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        after = snapshot(conn)
        for table, ids in before.items():
            assert after[table] == ids, table
        assert db.get(conn, "twins", "twin")["scene_url"] == "twins/scene.json"
        assert db.get(conn, "task_graphs", "graph")["provider"] == "function_gemma"
        assert db.get(conn, "robots", "uno")["kind"] == "unoq"
        assert db.get(conn, "deployments", "deployment")["robot_id"] == "uno"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # New tables start empty and new columns start NULL: nothing is invented for old rows.
        assert after["provider_usage"] == [] and after["calibration_sessions"] == []
        assert db.get(conn, "twins", "twin")["profile_provenance_json"] is None
        assert db.get(conn, "task_graphs", "graph")["provenance_json"] is None


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_the_database_is_copied_before_it_is_migrated(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    with closing(open_at(path, version)) as old:
        v1_rows(old)
    with closing(upgrade(path)):
        pass
    copy = f"{path}.v{version}.bak"
    assert os.path.exists(copy)
    with closing(sqlite3.connect(copy)) as backup:
        assert max(1, backup.execute("PRAGMA user_version").fetchone()[0]) == version
        columns = {row[1] for row in backup.execute("PRAGMA table_info(twins)")}
        assert ("unity_scene_url" in columns) == (version < 3)
        assert backup.execute("SELECT COUNT(*) FROM twins").fetchone()[0] == 1


def test_an_empty_database_is_not_copied_and_a_second_start_changes_nothing(tmp_path):
    empty = tmp_path / "empty.db"
    with closing(upgrade(empty)):
        pass
    assert not any(name.endswith(".bak") for name in os.listdir(tmp_path))

    path = tmp_path / "v3.db"
    with closing(open_at(path, 3)) as old:
        v1_rows(old)
        old.execute("INSERT INTO missions(id, twin_id, start_json, goal_json, waypoints_json, "
                    "path_json) VALUES ('mission', 'twin', '[0,0,0]', '[1,1]', '[]', '[]')")
        old.execute("INSERT INTO runs(id, mission_id, policy_id, kind, reached) "
                    "VALUES ('run', 'mission', 'policy', 'sim', 1)")
        old.commit()
    with closing(upgrade(path)) as conn:
        first = snapshot(conn)
    stamp = os.path.getmtime(f"{path}.v3.bak")
    with closing(upgrade(path)) as conn:
        assert snapshot(conn) == first
        assert db.get(conn, "runs", "run")["reached"] == 1
    assert os.path.getmtime(f"{path}.v3.bak") == stamp  # never overwritten


def test_old_rows_are_served_after_the_upgrade(tmp_path, monkeypatch):
    from orchestrator.service import app

    path = tmp_path / "scanborn.db"
    with closing(open_at(path, 1)) as old:
        v1_rows(old)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("SCANBORN_DATA", str(tmp_path / "data"))
    client = TestClient(app)
    assert [row["id"] for row in client.get("/deployments").json()["items"]] == ["deployment"]
    assert {row["id"] for row in client.get("/robots").json()["items"]} == {"uno", "sim"}
    assert client.get("/scans").json()["total"] == 1
    twin = client.get("/twins/twin").json()
    assert (twin["mesh_id"], twin["scan_id"]) == ("mesh", "scan")
