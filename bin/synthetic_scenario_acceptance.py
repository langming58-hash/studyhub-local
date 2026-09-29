#!/usr/bin/env python3
"""Synthetic scenario acceptance checks for current StudyHub behavior."""

from __future__ import annotations

import importlib.util
import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from synthetic_scenarios import EXECUTABLE_SCENARIOS, build_scenario, write_text


ROOT = Path(__file__).resolve().parents[1]
STATE_ENV = (
    "DATABASE_PATH",
    "DEMO_MODE",
    "OPENAI_API_KEY",
    "OPENAI_VECTOR_STORE_ID",
    "STUDYHUB_CACHE_DIR",
    "STUDYHUB_CONFIG_PATH",
    "STUDYHUB_DATA_DIR",
    "STUDYHUB_DESKTOP_TEST_ROOT",
    "STUDYHUB_LOG_DIR",
    "STUDYHUB_RUNTIME_DIR",
    "STUDYHUB_RUNTIME_PROFILE",
    "STUDY_LIBRARY_PATH",
)


def load_server(scenario_name: str, tmp: Path, library: Path, database: Path):
    for key in STATE_ENV:
        os.environ.pop(key, None)
    runtime = database.parent
    os.environ.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": "production",
            "STUDY_LIBRARY_PATH": str(library),
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "DATABASE_PATH": str(database),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    module_name = f"studyhub_synthetic_{scenario_name}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = runtime / "data"
    server.CACHE_DIR = runtime / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = runtime / "logs"
    server.DB_PATH = database
    server.ENV_LOCAL_PATH = runtime / "settings.env"
    server.ENV_LOCAL_EXISTS = False
    server.DEFAULT_STUDY_ROOT = library
    server.ensure_dirs()
    return server


def connect(server: Any) -> sqlite3.Connection:
    conn = server.connect_db()
    server.init_db(conn)
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def active_file_count(conn: sqlite3.Connection) -> int:
    return int(scalar(conn, "SELECT COUNT(*) FROM files WHERE active=1 AND removed_at IS NULL"))


def active_course_count(conn: sqlite3.Connection) -> int:
    return int(
        scalar(
            conn,
            "SELECT COUNT(*) FROM courses WHERE active=1 AND archived=0 AND removed_at IS NULL AND source_kind!='system'",
        )
    )


def chunk_count(conn: sqlite3.Connection, file_id: int) -> int:
    return int(scalar(conn, "SELECT COUNT(*) FROM document_chunks WHERE file_id=?", (file_id,)))


def file_by_name(conn: sqlite3.Connection, filename: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE filename=? ORDER BY id LIMIT 1", (filename,)).fetchone()
    assert row is not None, filename
    return row


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_clean_empty(tmp: Path) -> bool:
    scenario = build_scenario("clean_empty", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    conn = connect(server)
    preflight = server.api_preflight(conn)
    ok = (
        active_course_count(conn) == scenario.expectations["courses"]
        and active_file_count(conn) == scenario.expectations["materials"]
        and preflight["firstLaunch"] is True
        and preflight["courseCount"] == 0
        and preflight["fileCount"] == 0
        and "demoMode" not in preflight
    )
    conn.close()
    return ok


def check_normal_small(tmp: Path) -> bool:
    scenario = build_scenario("normal_small", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    first = server.scan_library(scenario.library)
    second = server.scan_library(scenario.library)
    conn = connect(server)
    matches = server.search_local_context(
        conn,
        scenario.expectations["known_phrase"],
        {"course": scenario.expectations["known_course"], "week": scenario.expectations["known_week"]},
        limit=3,
    )
    ok = (
        first.new_files == scenario.expectations["materials"]
        and active_course_count(conn) == scenario.expectations["courses"]
        and active_file_count(conn) == scenario.expectations["materials"]
        and second.unchanged_files == scenario.expectations["materials"]
        and active_file_count(conn) == scenario.expectations["materials"]
        and any(scenario.expectations["known_phrase"] in row["text"].lower() for row in matches)
    )
    conn.close()
    return ok


def check_empty_course(tmp: Path) -> bool:
    scenario = build_scenario("empty_course", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    conn = connect(server)
    empty = server.manage_course(
        conn,
        {"action": "create", "course_code": scenario.expectations["course_code"], "display_name": "Synthetic Empty Course"},
    )
    other = server.manage_course(conn, {"action": "create", "course_code": "TEST4101", "display_name": "Synthetic Nonempty Course"})
    week = server.manage_week(conn, {"action": "create", "course_id": other["id"], "label": "Week 01", "kind": "week"})
    external = scenario.root / "external" / "Other.txt"
    write_text(external, "Synthetic unrelated material remains available.")
    added = server.register_material_paths(
        conn,
        {"paths": [str(external)], "course_id": other["id"], "week_id": week["id"], "material_type": "lecture", "is_official": True},
    )
    empty_row = server.course_row_with_counts(conn, empty["id"])
    other_row = server.course_row_with_counts(conn, other["id"])
    ok = empty_row["file_count"] == 0 and other_row["file_count"] == 1 and added["added"] == 1
    conn.close()
    return ok


def check_long_metadata(tmp: Path) -> bool:
    scenario = build_scenario("long_metadata", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    conn = connect(server)
    course = server.manage_course(
        conn,
        {
            "action": "create",
            "course_code": scenario.expectations["course_code"],
            "display_name": scenario.expectations["course_name"],
        },
    )
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": scenario.expectations["week_label"], "kind": "module"})
    external = next((scenario.root / "external").glob("*.txt"))
    result = server.register_material_paths(
        conn,
        {"paths": [str(external)], "course_id": course["id"], "week_id": week["id"], "material_type": "reading", "is_official": True},
    )
    row = conn.execute("SELECT * FROM files WHERE id=?", (result["items"][0]["id"],)).fetchone()
    public = server.public_file(row)
    ok = (
        course["code"] == scenario.expectations["course_code"]
        and course["name"] == scenario.expectations["course_name"]
        and week["week_label"] == scenario.expectations["week_label"]
        and row["filename"] == external.name
        and row["material_type"] == "reading"
        and "original_path" not in public
        and str(external.parent) not in str(public)
        and chunk_count(conn, row["id"]) > 0
    )
    conn.close()
    return ok


def check_large_library(tmp: Path) -> bool:
    scenario = build_scenario("large_library", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    first = server.scan_library(scenario.library)
    second = server.scan_library(scenario.library)
    conn = connect(server)
    matches = server.search_local_context(conn, scenario.expectations["known_phrase"], {}, limit=5)
    ok = (
        first.new_files == scenario.expectations["materials"]
        and active_course_count(conn) == scenario.expectations["courses"]
        and active_file_count(conn) == scenario.expectations["materials"]
        and second.unchanged_files == scenario.expectations["materials"]
        and active_file_count(conn) == scenario.expectations["materials"]
        and any(scenario.expectations["known_phrase"] in row["text"] for row in matches)
    )
    conn.close()
    return ok


def check_missing_original(tmp: Path) -> bool:
    scenario = build_scenario("missing_original", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    server.scan_library(scenario.library)
    conn = connect(server)
    missing_row = file_by_name(conn, scenario.expectations["missing_filename"])
    kept_row = file_by_name(conn, scenario.expectations["kept_filename"])
    Path(missing_row["original_path"]).unlink()
    conn.close()
    stats = server.scan_library(scenario.library)
    conn = connect(server)
    missing_after = conn.execute("SELECT * FROM files WHERE id=?", (missing_row["id"],)).fetchone()
    kept_after = conn.execute("SELECT * FROM files WHERE id=?", (kept_row["id"],)).fetchone()
    ok = (
        stats.removed_files == 1
        and missing_after["source_missing"] == 1
        and missing_after["missing_at"]
        and missing_after["active"] == 1
        and kept_after["source_missing"] == 0
        and kept_after["active"] == 1
        and active_file_count(conn) == scenario.expectations["materials"]
    )
    conn.close()
    return ok


def check_duplicate_content(tmp: Path) -> bool:
    scenario = build_scenario("duplicate_content", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    original_hashes = {path.name: sha256(path) for path in scenario.library.rglob("*.txt")}
    conn = connect(server)
    imported = server.import_course_folder(conn, {"path": str(scenario.library), "display_name": "Synthetic Duplicate Course", "is_official": True})
    original_hashes_after = {path.name: sha256(path) for path in scenario.library.rglob("*.txt")}
    ok = (
        imported["detected"] == scenario.expectations["detected"]
        and imported["added"] == scenario.expectations["added"]
        and imported["duplicates"] == scenario.expectations["duplicates"]
        and active_file_count(conn) == 1
        and original_hashes_after == original_hashes
    )
    conn.close()
    return ok


def check_modified_material(tmp: Path) -> bool:
    scenario = build_scenario("modified_material", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    server.scan_library(scenario.library)
    conn = connect(server)
    row = file_by_name(conn, scenario.expectations["filename"])
    first_id = row["id"]
    first_stable_id = row["stable_id"]
    first_sha = row["sha256"]
    material_path = Path(row["original_path"])
    conn.close()
    write_text(material_path, "Updated synthetic version with beta-content.", mtime=1_700_000_200)
    stats = server.scan_library(scenario.library)
    conn = connect(server)
    updated = file_by_name(conn, scenario.expectations["filename"])
    text = server.read_cached_text(updated, 1000)
    matches = server.search_local_context(conn, scenario.expectations["updated_phrase"], {"fileId": first_id}, limit=3)
    version_count = scalar(conn, "SELECT COUNT(*) FROM file_versions WHERE file_id=?", (first_id,))
    ok = (
        stats.updated_files == 1
        and updated["id"] == first_id
        and updated["stable_id"] == first_stable_id
        and updated["sha256"] != first_sha
        and version_count >= 2
        and scenario.expectations["updated_phrase"] in text
        and any(match["source_file_id"] == first_id and scenario.expectations["updated_phrase"] in match["text"] for match in matches)
    )
    conn.close()
    return ok


def check_cloud_unavailable(tmp: Path) -> bool:
    scenario = build_scenario("cloud_unavailable", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    server.scan_library(scenario.library)
    conn = connect(server)
    health = server.api_health(conn)
    preflight = server.api_preflight(conn)
    matches = server.search_local_context(conn, scenario.expectations["known_phrase"], {}, limit=3)
    ok = (
        health["openAI"] == "Not configured"
        and health["vectorStore"] == "Not configured"
        and any(item["code"] == "openai_optional" for item in preflight["items"])
        and bool(matches)
        and scenario.expectations["known_phrase"] in matches[0]["text"]
    )
    conn.close()
    return ok


def check_unreadable_material(tmp: Path) -> bool:
    scenario = build_scenario("unreadable_material", tmp)
    server = load_server(scenario.name, tmp, scenario.library, scenario.database)
    source = next(scenario.library.rglob("*.pdf"))
    original_hash = sha256(source)
    stats = server.scan_library(scenario.library)
    after_hash = sha256(source)
    conn = connect(server)
    row = file_by_name(conn, scenario.expectations["filename"])
    ok = (
        stats.suspicious == 1
        and stats.failed_files == 1
        and row["suspicious"] == scenario.expectations["suspicious"]
        and row["active"] == 1
        and Path(row["original_path"]).exists()
        and after_hash == original_hash
        and chunk_count(conn, row["id"]) == 0
        and row["ai_index_status"] == "not_indexed"
    )
    conn.close()
    return ok


CHECKS = {
    "clean_empty": check_clean_empty,
    "normal_small": check_normal_small,
    "empty_course": check_empty_course,
    "long_metadata": check_long_metadata,
    "large_library": check_large_library,
    "missing_original": check_missing_original,
    "duplicate_content": check_duplicate_content,
    "modified_material": check_modified_material,
    "cloud_unavailable": check_cloud_unavailable,
    "unreadable_material": check_unreadable_material,
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="studyhub-synthetic-scenarios-") as tmp_name:
        tmp = Path(tmp_name)
        results: dict[str, bool] = {}
        for name in EXECUTABLE_SCENARIOS:
            results[name] = CHECKS[name](tmp)

    for name, passed in results.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
