#!/usr/bin/env python3
"""Synthetic acceptance checks for Canvas CourseOffering selection.

The checks use only temporary SQLite databases and synthetic Canvas metadata.
They do not contact Canvas, use real credentials, or import academic content.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


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

TOKEN = "syn-token"
BIG_ID = "9007199254740995"


def load_server(tmp: Path):
    for key in STATE_ENV:
        os.environ.pop(key, None)
    runtime = tmp / "runtime"
    library = tmp / "library"
    database = runtime / "studyhub.sqlite"
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
    module_name = f"studyhub_canvas_selection_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
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


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(scalar(conn, f"SELECT COUNT(*) FROM {table}"))


def course(server: Any, remote_id: str, name: str, code: str = "TEST1001", term_id: str | None = "2026S2", term_name: str | None = "Synthetic Term"):
    return server.CanvasCourse(
        remote_course_id=remote_id,
        name=name,
        course_code=code,
        workflow_state="available",
        start_at="2026-02-01T00:00:00Z",
        end_at=None,
        term_id=term_id,
        term_name=term_name,
    )


def raises(expected: str, action) -> bool:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - acceptance wants exact safe code
        return str(exc) == expected and TOKEN not in str(exc) and TOKEN not in repr(exc)
    return False


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-canvas-selection-") as raw_tmp:
        tmp = Path(raw_tmp)
        server = load_server(tmp)
        conn = connect(server)
        authority = server.canvas_authority_id("https://canvas.example.edu")
        other_authority = server.canvas_authority_id("https://canvas-alt.example.edu")
        c1 = course(server, BIG_ID, "Synthetic Calculus", "TEST1001")
        c2 = course(server, "42", "Synthetic Economics", "TEST2001")

        migration_rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        migration_count = table_count(conn, "schema_migrations")
        server.run_schema_migrations(conn)
        check("migration_is_additive_and_recorded_once", (2, "phase2_canvas_course_selection") in [(r["version"], r["name"]) for r in migration_rows], failures)
        check("migration_rerun_is_idempotent", table_count(conn, "schema_migrations") == migration_count, failures)

        before_legacy = {table: table_count(conn, table) for table in ("courses", "terms", "files", "sources", "materials", "material_versions")}
        stable_one = server.canvas_course_offering_stable_id(authority, BIG_ID)
        stable_again = server.canvas_course_offering_stable_id(authority, BIG_ID)
        stable_other_origin = server.canvas_course_offering_stable_id(other_authority, BIG_ID)
        check("identity_uses_provider_authority_and_remote_id", stable_one == stable_again and stable_one != stable_other_origin, failures)
        check("canvas_id_greater_than_js_safe_integer_exact", BIG_ID in stable_one or isinstance(BIG_ID, str), failures)

        initial_revision = server.canvas_selection_revision(conn, authority)
        preview = server.canvas_course_selection_plan(conn, authority, [c1, c2], [BIG_ID])
        check("preview_performs_zero_db_mutation", server.canvas_selection_revision(conn, authority) == initial_revision and table_count(conn, "course_offerings") == 0, failures)
        check("browser_selects_only_authoritative_discovered_ids", [item["remote_course_id"] for item in preview["select"]] == [BIG_ID], failures)
        check("unknown_requested_remote_id_identified", server.canvas_course_selection_plan(conn, authority, [c1], ["missing"])["unknown_remote_course_ids"] == ["missing"], failures)

        result = server.apply_canvas_course_selection(conn, authority, [c1, c2], [BIG_ID], preview["selection_revision"])
        conn.commit()
        selected = server.remembered_canvas_course_selections(conn, authority_id=authority)
        row = conn.execute("SELECT * FROM course_offerings WHERE stable_id=?", (stable_one,)).fetchone()
        check("apply_persists_selected_offerings_atomically", len(result["selected"]) == 1 and len(selected) == 1 and selected[0]["remote_course_id"] == BIG_ID, failures)
        check("remote_ids_stored_as_text", isinstance(row["remote_course_id"], str) and row["remote_course_id"] == BIG_ID, failures)
        check("existing_local_course_material_data_survives", before_legacy == {table: table_count(conn, table) for table in before_legacy}, failures)

        renamed = course(server, BIG_ID, "Synthetic Calculus Renamed", "TEST1001B", term_id="2026S3", term_name="Synthetic Term Renamed")
        before_stable = row["stable_id"]
        refresh_preview = server.canvas_course_selection_plan(conn, authority, [renamed], [BIG_ID])
        check("course_rename_course_code_and_term_change_refresh_metadata", len(refresh_preview["metadata_refresh"]) == 1, failures)
        server.apply_canvas_course_selection(conn, authority, [renamed], [BIG_ID], refresh_preview["selection_revision"])
        conn.commit()
        refreshed = conn.execute("SELECT * FROM course_offerings WHERE remote_course_id=?", (BIG_ID,)).fetchone()
        still_selected = scalar(conn, "SELECT selected FROM course_offering_selections WHERE offering_id=?", (refreshed["id"],))
        check("metadata_refresh_keeps_identity_and_selection", refreshed["stable_id"] == before_stable and refreshed["remote_name"] == "Synthetic Calculus Renamed" and refreshed["remote_course_code"] == "TEST1001B" and still_selected == 1, failures)

        fake_metadata_ignored_preview = server.canvas_course_selection_plan(conn, authority, [renamed], [BIG_ID])
        server.apply_canvas_course_selection(conn, authority, [renamed], [BIG_ID], fake_metadata_ignored_preview["selection_revision"])
        conn.commit()
        persisted_name = scalar(conn, "SELECT remote_name FROM course_offerings WHERE remote_course_id=?", (BIG_ID,))
        check("browser_supplied_fake_metadata_cannot_persist", persisted_name != "Attacker Supplied Name", failures)

        missing_preview = server.canvas_course_selection_plan(conn, authority, [], [])
        check("missing_discovery_result_does_not_delete_or_deselect", BIG_ID in missing_preview["remembered_missing_from_discovery"] and scalar(conn, "SELECT selected FROM course_offering_selections WHERE offering_id=?", (refreshed["id"],)) == 1, failures)

        stale_preview = server.canvas_course_selection_plan(conn, authority, [renamed], [BIG_ID])
        alt_preview = server.canvas_course_selection_plan(conn, authority, [renamed, c2], [BIG_ID, "42"])
        server.apply_canvas_course_selection(conn, authority, [renamed, c2], [BIG_ID, "42"], alt_preview["selection_revision"])
        conn.commit()
        check("stale_preview_revision_rejected", raises("stale_canvas_course_selection_preview", lambda: server.apply_canvas_course_selection(conn, authority, [renamed], [], stale_preview["selection_revision"])), failures)

        deselect_preview = server.canvas_course_selection_plan(conn, authority, [renamed, c2], ["42"])
        server.apply_canvas_course_selection(conn, authority, [renamed, c2], ["42"], deselect_preview["selection_revision"])
        conn.commit()
        check("deselect_changes_user_owned_selection_only", scalar(conn, "SELECT selected FROM course_offering_selections sel JOIN course_offerings co ON co.id=sel.offering_id WHERE co.remote_course_id=?", (BIG_ID,)) == 0, failures)
        check("deselect_does_not_delete_course_offering", scalar(conn, "SELECT COUNT(*) FROM course_offerings WHERE remote_course_id=?", (BIG_ID,)) == 1, failures)

        rollback_authority = server.canvas_authority_id("https://rollback.example.edu")
        rollback_preview = server.canvas_course_selection_plan(conn, rollback_authority, [course(server, "rollback", "Rollback Course")], ["rollback"])
        before_rollback = table_count(conn, "course_offerings")
        check("unknown_apply_rejected", raises("unknown_canvas_course_id", lambda: server.apply_canvas_course_selection(conn, rollback_authority, [], ["unknown"], rollback_preview["selection_revision"])), failures)
        check("failed_apply_rolls_back_all_changes", raises("synthetic_apply_failure", lambda: server.apply_canvas_course_selection(conn, rollback_authority, [course(server, "rollback", "Rollback Course")], ["rollback"], rollback_preview["selection_revision"], fail_after_offerings=True)) and table_count(conn, "course_offerings") == before_rollback, failures)

        offline = server.remembered_canvas_course_selections(conn)
        check("remembered_selected_offerings_list_offline", any(item["remote_course_id"] == "42" and item["fresh"] is False for item in offline), failures)
        check("no_legacy_courses_auto_linked", table_count(conn, "courses") == before_legacy["courses"], failures)
        check("no_legacy_terms_auto_linked", table_count(conn, "terms") == before_legacy["terms"], failures)
        check("no_source_blob_material_version_created", all(table_count(conn, table) == before_legacy[table] for table in ("sources", "materials", "material_versions")), failures)
        check("no_files_or_file_versions_created", table_count(conn, "files") == before_legacy["files"] and table_count(conn, "file_versions") == 0, failures)

        db_bytes = Path(server.DB_PATH).read_bytes()
        logs = (server.LOG_DIR / "studyhub.log").read_text(encoding="utf-8", errors="ignore") if (server.LOG_DIR / "studyhub.log").exists() else ""
        public_payload = json.dumps({"preview": preview, "selected": offline}, ensure_ascii=False)
        check("canvas_token_never_enters_sqlite", TOKEN.encode() not in db_bytes, failures)
        check("canvas_token_never_enters_logs", TOKEN not in logs, failures)
        check("canvas_token_never_enters_public_api_payloads", TOKEN not in public_payload, failures)

        source = (ROOT / "server.py").read_text(encoding="utf-8")
        check("selection_api_is_post_only", 'parsed.path == "/api/canvas/course-selection/preview"' in source and 'parsed.path == "/api/canvas/course-selection/apply"' in source and "/api/canvas/course-selection/preview" not in source.split("def handle_api_get", 1)[1].split("def handle_notes_get", 1)[0], failures)
        check("no_canvas_write_or_content_endpoints", all(term not in source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0] for term in ['method="POST"', "/api/v1/files", "/api/v1/modules", "/api/v1/pages", "/api/v1/assignments"]), failures)
        check("production_manual_token_enrollment_remains_blocked", "canvas_manual_token_development_only" in (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8"), failures)
        check("demo_test_never_accesses_native_credentials", "RuntimeProfile::DemoTest => CredentialStoreBackend::DemoDenied" in (ROOT / "src-tauri/src/credential_store.rs").read_text(encoding="utf-8"), failures)

        conn.close()

    if failures:
        print("Canvas course selection acceptance failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Canvas course selection acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
