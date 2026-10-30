#!/usr/bin/env python3
"""Synthetic acceptance checks for Study Engine V1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POST_34_RESTORE_ENTITY_KINDS = (
    "term",
    "course",
    "learning_unit",
    "file",
    "legacy_file_version",
    "blob",
    "source",
    "material",
    "material_version",
    "note",
    "star",
    "attempt",
    "wrong_question",
    "bookmark",
    "study_session",
    "academic_change_event_state",
    "text_cache_artifact",
)


def load_server(base: Path, suffix: str):
    root = base / suffix / "StudyLibrary"
    runtime = base / suffix / "runtime"
    os.environ["STUDY_LIBRARY_PATH"] = str(root)
    os.environ["DATABASE_PATH"] = str(runtime / "data" / "studyhub.sqlite")
    os.environ["STUDYHUB_RUNTIME_DIR"] = str(runtime)
    os.environ.pop("OPENAI_API_KEY", None)
    spec = importlib.util.spec_from_file_location(f"studyhub_study_{suffix}", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = runtime / "data"
    server.EXPORT_DIR = server.DATA_DIR / "exports"
    server.RESTORE_INBOX_DIR = server.DATA_DIR / "restore-inbox"
    server.RESTORE_RELINK_INBOX_DIR = server.DATA_DIR / "restore-relink-inbox"
    server.CACHE_DIR = runtime / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = runtime / "logs"
    server.DB_PATH = runtime / "data" / "studyhub.sqlite"
    server.DEFAULT_STUDY_ROOT = root
    return server, root


def seed_library(server: Any, root: Path) -> tuple[Any, list[int]]:
    for week, names in {
        "Week 01": ("Foundations.txt", "Tutorial 1.txt"),
        "Week 02": ("Applications.txt",),
    }.items():
        folder = root / "TEST1001 - Synthetic Study" / week / "01 Course Materials" / "Lecture"
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_text(f"Synthetic content for {name}\n", encoding="utf-8")
    server.scan_library(root)
    conn = server.connect_db()
    server.init_db(conn)
    ids = [int(row["id"]) for row in conn.execute("SELECT id FROM files ORDER BY id").fetchall()]
    return conn, ids


def actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in plan["sections"] for item in section["actions"]]


def decisions_for(plan: dict[str, Any], *, restore_study: bool = False) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for action in actions(plan):
        classification = action["classification"]
        if classification == "CONFLICT":
            decision = (
                "RESTORE_BACKUP"
                if restore_study
                and action["label"] == "Material study state"
                and "RESTORE_BACKUP" in action.get("allowed_decisions", [])
                else "KEEP_LOCAL"
            )
        elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
            decision = "SKIP"
        else:
            continue
        result.append(
            {"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision}
        )
    return result


def make_v1_archive(server: Any, v2_path: Path, output: Path) -> Path:
    with zipfile.ZipFile(v2_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    for member in server.BACKUP_DATA_MEMBERS:
        payload = json.loads(members[member])
        if member == "data/study_records.json":
            payload["records"] = [
                record for record in payload["records"]
                if record.get("record_type") != "material_study_state"
            ]
        payload["format_version"] = 1
        members[member] = server.canonical_backup_json(payload)
    manifest = json.loads(members["manifest.json"])
    manifest["format_version"] = 1
    manifest["minimum_restore_format_version"] = 1
    for member in server.BACKUP_DATA_MEMBERS:
        records = json.loads(members[member])["records"]
        manifest["entries"][member] = {
            "sha256": hashlib.sha256(members[member]).hexdigest(),
            "byte_size": len(members[member]),
            "record_count": len(records),
        }
    manifest["total_records"] = sum(item["record_count"] for item in manifest["entries"].values())
    members["manifest.json"] = server.canonical_backup_json(manifest)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in server.BACKUP_MEMBERS:
            archive.writestr(server.safe_backup_zip_info(name), members[name])
    return output


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def install_exact_post_34_restore_journal(server: Any, conn: sqlite3.Connection) -> None:
    """Initialize through migration 5, then install the exact merged-PR34 migration-6 DDL."""
    current_migrations = server.SCHEMA_MIGRATIONS
    server.SCHEMA_MIGRATIONS = current_migrations[:5]
    try:
        server.init_db(conn)
    finally:
        server.SCHEMA_MIGRATIONS = current_migrations

    change_kinds = ", ".join(f"'{value}'" for value in server.RESTORE_CHANGE_KINDS)
    entity_kinds = ", ".join(f"'{value}'" for value in POST_34_RESTORE_ENTITY_KINDS)

    def migration_6(post_34_conn: sqlite3.Connection) -> None:
        post_34_conn.execute(
            """
            CREATE TABLE restore_operations (
              id INTEGER PRIMARY KEY,
              operation_id TEXT NOT NULL UNIQUE,
              format_version INTEGER NOT NULL,
              restore_plan_revision TEXT NOT NULL,
              schema_fingerprint TEXT NOT NULL,
              changeset_digest TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('APPLIED', 'UNDONE')),
              change_count INTEGER NOT NULL,
              insert_count INTEGER NOT NULL,
              update_count INTEGER NOT NULL,
              artifact_count INTEGER NOT NULL,
              committed_at TEXT NOT NULL,
              undone_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        post_34_conn.execute(
            f"""
            CREATE TABLE restore_operation_changes (
              id INTEGER PRIMARY KEY,
              restore_operation_id INTEGER NOT NULL REFERENCES restore_operations(id) ON DELETE RESTRICT,
              sequence INTEGER NOT NULL,
              change_kind TEXT NOT NULL CHECK(change_kind IN ({change_kinds})),
              entity_kind TEXT NOT NULL CHECK(entity_kind IN ({entity_kinds})),
              locator_json TEXT NOT NULL,
              before_json TEXT,
              after_json TEXT,
              before_token TEXT NOT NULL,
              after_token TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{{}}',
              UNIQUE(restore_operation_id, sequence)
            )
            """
        )
        post_34_conn.execute(
            "CREATE INDEX idx_restore_operations_status ON restore_operations(status, committed_at DESC)"
        )
        post_34_conn.execute(
            "CREATE INDEX idx_restore_operation_changes_operation "
            "ON restore_operation_changes(restore_operation_id, sequence)"
        )

    server.apply_schema_migration(
        conn, 6, "phase2_restore_operation_changeset", migration_6
    )
    timestamp = "2026-05-31T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO restore_operations(
          id, operation_id, format_version, restore_plan_revision, schema_fingerprint,
          changeset_digest, status, change_count, insert_count, update_count,
          artifact_count, committed_at, undone_at, created_at, updated_at
        ) VALUES (9001, ?, 1, ?, ?, ?, 'UNDONE', 1, 0, 1, 0, ?, ?, ?, ?)
        """,
        (
            "restoreop_" + "1" * 32,
            "post34-plan-revision",
            "post34-schema-fingerprint",
            "2" * 64,
            timestamp,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """
        INSERT INTO restore_operation_changes(
          id, restore_operation_id, sequence, change_kind, entity_kind, locator_json,
          before_json, after_json, before_token, after_token, metadata_json
        ) VALUES (9101, 9001, 0, 'UPDATE', 'note', ?, ?, ?, ?, ?, ?)
        """,
        (
            '{"stable_id":"post34-note"}',
            '{"body":"before"}',
            '{"body":"after"}',
            "before-token",
            "after-token",
            '{"source":"synthetic-post34-fixture"}',
        ),
    )
    conn.commit()


def journal_contract(conn: sqlite3.Connection) -> dict[str, Any]:
    columns = [tuple(row) for row in conn.execute("PRAGMA table_info(restore_operation_changes)")]
    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_list(restore_operation_changes)")]
    indexes: list[tuple[Any, ...]] = []
    for row in conn.execute("PRAGMA index_list(restore_operation_changes)"):
        index_name = str(row[1])
        index_columns = tuple(item[2] for item in conn.execute(f'PRAGMA index_info("{index_name}")'))
        indexes.append((index_name.startswith("sqlite_autoindex_"), row[2], row[3], row[4], index_columns))
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restore_operation_changes'"
    ).fetchone()
    sql = str(sql_row[0]) if sql_row else ""
    return {
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": sorted(indexes, key=repr),
        "allowed_entities": tuple(
            entity for entity in server_entity_kinds(conn) if f"'{entity}'" in sql
        ),
    }


def server_entity_kinds(conn: sqlite3.Connection) -> tuple[str, ...]:
    del conn
    return (
        *POST_34_RESTORE_ENTITY_KINDS[:15],
        "material_study_state",
        *POST_34_RESTORE_ENTITY_KINDS[15:],
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-study-engine-") as temporary:
        base = Path(temporary)
        fresh_server, _fresh_root = load_server(base, "fresh")
        fresh_conn = fresh_server.connect_db()
        fresh_server.init_db(fresh_conn)
        fresh_contract = journal_contract(fresh_conn)
        fresh_conn.close()

        server, root = load_server(base, "post34-upgrade")
        conn = server.connect_db()
        install_exact_post_34_restore_journal(server, conn)
        old_sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='restore_operation_changes'"
            ).fetchone()["sql"]
        )
        migrations_before = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
        operations_before = [dict(row) for row in conn.execute("SELECT * FROM restore_operations ORDER BY id")]
        changes_before = [dict(row) for row in conn.execute("SELECT * FROM restore_operation_changes ORDER BY id")]

        server.run_schema_migrations(conn)
        conn.commit()
        upgraded_contract = journal_contract(conn)
        operations_after = [dict(row) for row in conn.execute("SELECT * FROM restore_operations ORDER BY id")]
        changes_after = [dict(row) for row in conn.execute("SELECT * FROM restore_operation_changes ORDER BY id")]
        migration_snapshot = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
        schema_snapshot = [
            tuple(row)
            for row in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name IN ('restore_operations', 'restore_operation_changes', "
                "'idx_restore_operations_status', 'idx_restore_operation_changes_operation', "
                "'material_study_state', 'idx_material_study_queue') ORDER BY type, name"
            )
        ]
        server.run_schema_migrations(conn)
        conn.commit()

        check("real_post_34_fixture_has_migrations_1_through_6", [row[0] for row in migrations_before] == list(range(1, 7)), failures)
        check("old_journal_check_excludes_material_study_state", "'material_study_state'" not in old_sql, failures)
        check("post_34_existing_operation_rows_preserved", operations_before == operations_after, failures)
        check("post_34_existing_change_rows_preserved", changes_before == changes_after, failures)
        check("post_34_change_primary_key_preserved", changes_after[0]["id"] == 9101, failures)
        check(
            "post_34_journal_foreign_key_integrity",
            conn.execute("PRAGMA foreign_key_check").fetchone() is None,
            failures,
        )
        check("fresh_and_upgrade_journal_contract_converge", fresh_contract == upgraded_contract, failures)
        check(
            "upgraded_journal_preserves_index_unique_and_fk_contract",
            upgraded_contract["indexes"] == fresh_contract["indexes"]
            and upgraded_contract["foreign_keys"] == fresh_contract["foreign_keys"],
            failures,
        )
        check(
            "post_34_migration_is_idempotent",
            migration_snapshot == [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
            and schema_snapshot
            == [
                tuple(row)
                for row in conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE name IN ('restore_operations', 'restore_operation_changes', "
                    "'idx_restore_operations_status', 'idx_restore_operation_changes_operation', "
                    "'material_study_state', 'idx_material_study_queue') ORDER BY type, name"
                )
            ],
            failures,
        )

        timestamp = "2026-06-01T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO restore_operations(
              id, operation_id, format_version, restore_plan_revision, schema_fingerprint,
              changeset_digest, status, change_count, insert_count, update_count,
              artifact_count, committed_at, undone_at, created_at, updated_at
            ) VALUES (9002, ?, 1, 'synthetic-plan', 'synthetic-schema', ?, 'UNDONE', 1, 0, 1, 0, ?, ?, ?, ?)
            """,
            ("restoreop_" + "3" * 32, "4" * 64, timestamp, timestamp, timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO restore_operation_changes(
              id, restore_operation_id, sequence, change_kind, entity_kind, locator_json,
              before_json, after_json, before_token, after_token, metadata_json
            ) VALUES (9102, 9002, 0, 'UPDATE', 'material_study_state', '{}', '{}', '{}', 'a', 'b', '{}')
            """
        )
        invalid_kind_rejected = False
        try:
            conn.execute(
                """
                INSERT INTO restore_operation_changes(
                  restore_operation_id, sequence, change_kind, entity_kind, locator_json,
                  before_json, after_json, before_token, after_token, metadata_json
                ) VALUES (9002, 1, 'UPDATE', 'arbitrary_kind', '{}', '{}', '{}', 'a', 'b', '{}')
                """
            )
        except sqlite3.IntegrityError:
            invalid_kind_rejected = True
        duplicate_sequence_rejected = False
        try:
            conn.execute(
                """
                INSERT INTO restore_operation_changes(
                  restore_operation_id, sequence, change_kind, entity_kind, locator_json,
                  before_json, after_json, before_token, after_token, metadata_json
                ) VALUES (9002, 0, 'UPDATE', 'note', '{}', '{}', '{}', 'a', 'b', '{}')
                """
            )
        except sqlite3.IntegrityError:
            duplicate_sequence_rejected = True
        delete_restrict_preserved = False
        try:
            conn.execute("DELETE FROM restore_operations WHERE id=9002")
        except sqlite3.IntegrityError:
            delete_restrict_preserved = True
        conn.commit()
        check(
            "upgraded_journal_accepts_material_study_state",
            conn.execute(
                "SELECT COUNT(*) FROM restore_operation_changes "
                "WHERE id=9102 AND entity_kind='material_study_state'"
            ).fetchone()[0]
            == 1,
            failures,
        )
        check("upgraded_journal_rejects_arbitrary_entity_kind", invalid_kind_rejected, failures)
        check("upgraded_journal_preserves_unique_sequence", duplicate_sequence_rejected, failures)
        check("upgraded_journal_preserves_delete_restrict", delete_restrict_preserved, failures)

        conn.close()

        failure_server, _failure_root = load_server(base, "post34-failure")
        failure_conn = failure_server.connect_db()
        install_exact_post_34_restore_journal(failure_server, failure_conn)
        failure_rows_before = [
            dict(row) for row in failure_conn.execute("SELECT * FROM restore_operation_changes ORDER BY id")
        ]
        failure_sql_before = str(
            failure_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='restore_operation_changes'"
            ).fetchone()["sql"]
        )
        normal_ddl = failure_server.restore_operation_changes_table_ddl

        def rejecting_copy_ddl(table_name: str) -> str:
            ddl = normal_ddl(table_name)
            if table_name == "restore_operation_changes_v7":
                ddl = ddl.replace(
                    "          UNIQUE(restore_operation_id, sequence)",
                    "          CHECK(entity_kind = 'material_study_state'),\n"
                    "          UNIQUE(restore_operation_id, sequence)",
                )
            return ddl

        failure_server.restore_operation_changes_table_ddl = rejecting_copy_ddl
        migration_failed = False
        try:
            failure_server.run_schema_migrations(failure_conn)
        except sqlite3.IntegrityError:
            migration_failed = True
        finally:
            failure_server.restore_operation_changes_table_ddl = normal_ddl
        check(
            "failed_journal_rebuild_rolls_back_original_table",
            migration_failed
            and failure_sql_before
            == str(
                failure_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='restore_operation_changes'"
                ).fetchone()["sql"]
            )
            and failure_rows_before
            == [
                dict(row)
                for row in failure_conn.execute("SELECT * FROM restore_operation_changes ORDER BY id")
            ]
            and failure_conn.execute("SELECT 1 FROM schema_migrations WHERE version=7").fetchone()
            is None
            and failure_conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restore_operation_changes_v7'"
            ).fetchone()
            is None
            and failure_conn.execute("PRAGMA foreign_key_check").fetchone() is None,
            failures,
        )
        failure_conn.close()

        conn, file_ids = seed_library(server, root)
        fixed = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
        first, second, _third = file_ids

        check(
            "fresh_db_records_study_engine_migration",
            conn.execute(
                "SELECT COUNT(*) AS count FROM schema_migrations WHERE version=7 AND name='phase3_study_engine'"
            ).fetchone()["count"] == 1,
            failures,
        )
        check(
            "post_34_upgrade_is_additive_and_idempotent",
            conn.execute(
                "SELECT COUNT(*) AS count FROM schema_migrations WHERE version=7 AND name='phase3_study_engine'"
            ).fetchone()["count"] == 1
            and conn.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name='material_study_state'"
            ).fetchone()["count"] == 1,
            failures,
        )

        initial = server.study_overview(conn, at=fixed)
        opened = server.update_material_study_state(conn, {"file_id": first, "action": "open"}, reviewed_at=fixed)
        completed = server.update_material_study_state(
            conn, {"file_id": second, "action": "set_progress", "progress_state": "COMPLETED"}, reviewed_at=fixed
        )
        flagged = server.update_material_study_state(
            conn, {"file_id": second, "action": "set_needs_review", "needs_review": True}, reviewed_at=fixed
        )
        reviewed = server.update_material_study_state(
            conn, {"file_id": second, "action": "review", "grade": "good"}, reviewed_at=fixed
        )
        deterministic_one = server.study_overview(conn, at=fixed)
        deterministic_two = server.study_overview(conn, at=fixed)
        due = server.study_overview(conn, at=fixed + timedelta(minutes=11))

        check("new_materials_are_not_started", initial["summary"]["not_started"] == 3, failures)
        check("open_marks_in_progress", opened["progress_state"] == "IN_PROGRESS", failures)
        check("completion_persists_independently", completed["progress_state"] == "COMPLETED", failures)
        check("needs_review_is_independent", flagged["progress_state"] == "COMPLETED" and flagged["needs_review"] == 1, failures)
        check("fsrs_review_clears_explicit_flag", reviewed["needs_review"] == 0 and bool(reviewed["fsrs_due_at"]), failures)
        check("fsrs_fixed_time_is_deterministic", reviewed["fsrs_due_at"] == "2026-06-01T09:10:00+00:00", failures)
        check("queue_order_is_deterministic", deterministic_one["queue"] == deterministic_two["queue"], failures)
        check("due_review_precedes_continue", due["queue"][0]["id"] == second and due["queue"][0]["queue_reason"] == "FSRS_DUE", failures)
        check("course_rollup_is_correct", deterministic_one["courses"][0]["completed"] == 1 and deterministic_one["courses"][0]["in_progress"] == 1, failures)
        check("learning_unit_rollups_are_derived", len(deterministic_one["learning_units"]) == 2, failures)

        conn.close()
        restarted = server.connect_db()
        server.init_db(restarted)
        persisted = restarted.execute(
            "SELECT progress_state, fsrs_due_at FROM material_study_state ORDER BY material_id"
        ).fetchall()
        check("study_state_survives_restart", len(persisted) == 2 and persisted[1]["progress_state"] == "COMPLETED", failures)

        blocked = {"canvas": 0, "openai": 0, "credentials": 0}
        server.with_canvas_connector = lambda *_args, **_kwargs: blocked.__setitem__("canvas", blocked["canvas"] + 1)
        server.openai_responses_request = lambda *_args, **_kwargs: blocked.__setitem__("openai", blocked["openai"] + 1)
        server.initialize_credential_client = lambda *_args, **_kwargs: blocked.__setitem__("credentials", blocked["credentials"] + 1)

        exported = server.export_user_owned_backup(restarted, created_at="2026-06-01T10:00:00+00:00")
        archive = server.EXPORT_DIR / exported["filename"]
        validation, _manifest, datasets = server.read_validated_backup_archive(archive)
        state_records = [item for item in datasets["data/study_records.json"] if item["record_type"] == "material_study_state"]
        check("backup_v2_exports_study_state", validation["format_version"] == 2 and len(state_records) == 2, failures)
        v1 = make_v1_archive(server, archive, base / "legacy-v1.zip")
        check("legacy_v1_backup_remains_readable", server.validate_backup_archive(v1)["format_version"] == 1, failures)

        local_before = server.update_material_study_state(
            restarted, {"file_id": first, "action": "set_progress", "progress_state": "NOT_STARTED"}, reviewed_at=fixed + timedelta(hours=2)
        )
        restore_id = "a" * 32
        server.ensure_dirs()
        server.RESTORE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, server.staged_restore_path(restore_id))
        plan = server.preview_staged_restore(restore_id)
        study_conflicts = [item for item in actions(plan) if item["label"] == "Material study state" and item["classification"] == "CONFLICT"]
        applied = server.apply_staged_restore(
            restore_id, plan["plan_revision"], decisions_for(plan, restore_study=True)
        )["applied"]
        restarted.close()
        restarted = server.connect_db()
        server.init_db(restarted)
        restored_state = restarted.execute(
            "SELECT state.progress_state FROM material_study_state state JOIN materials material ON material.id=state.material_id WHERE material.legacy_file_id=?",
            (first,),
        ).fetchone()
        check("restore_preview_reports_study_conflict", bool(study_conflicts), failures)
        check("restore_backup_applies_study_state", restored_state["progress_state"] == "IN_PROGRESS", failures)
        operation_id = applied.get("restore_operation_id")
        recorded_study_changes = restarted.execute(
            """
            SELECT COUNT(*) FROM restore_operation_changes change
            JOIN restore_operations operation ON operation.id=change.restore_operation_id
            WHERE operation.operation_id=? AND change.entity_kind='material_study_state'
            """,
            (operation_id,),
        ).fetchone()[0]
        check("upgraded_restore_records_material_study_changeset", recorded_study_changes > 0, failures)
        undo = server.preview_restore_undo(operation_id)
        server.apply_restore_undo(operation_id, undo["undo_revision"])
        undone_state = restarted.execute(
            "SELECT state.progress_state FROM material_study_state state JOIN materials material ON material.id=state.material_id WHERE material.legacy_file_id=?",
            (first,),
        ).fetchone()
        check("undo_restores_exact_local_study_state", local_before["progress_state"] == "NOT_STARTED" and undone_state["progress_state"] == "NOT_STARTED", failures)

        plan_again = server.preview_staged_restore(restore_id)
        applied_again = server.apply_staged_restore(
            restore_id, plan_again["plan_revision"], decisions_for(plan_again, restore_study=True)
        )["applied"]
        operation_again = applied_again.get("restore_operation_id")
        server.update_material_study_state(
            restarted, {"file_id": first, "action": "set_progress", "progress_state": "COMPLETED"}, reviewed_at=fixed + timedelta(hours=3)
        )
        stale_undo = server.preview_restore_undo(operation_again)
        check("later_study_work_blocks_unsafe_undo", not stale_undo["undo_available"] and stale_undo["reason"] == "ROW_CHANGED", failures)
        check("study_engine_remains_offline", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)

        course_id = int(restarted.execute("SELECT id FROM courses ORDER BY id LIMIT 1").fetchone()["id"])
        timestamp = "2026-06-01T00:00:00+00:00"
        restarted.executemany(
            "INSERT INTO files(id, stable_id, course_id, course_code, course_name, filename, display_name, rel_path, original_path, source, source_label, hash, size, modified_at, active, source_missing, material_type, material_created_at, material_updated_at, indexed_at) VALUES (?, ?, ?, 'TEST1001', 'Synthetic Study', ?, ?, ?, ?, 'synthetic', 'Synthetic acceptance fixture', ?, ?, ?, 1, 0, 'reading', ?, ?, ?)",
            [
                (
                    10_000 + i,
                    f"perf_file_{i}",
                    course_id,
                    f"Synthetic {i}.txt",
                    f"Synthetic {i}",
                    f"TEST1001/Week_01/Reading/Synthetic {i}.txt",
                    str(root / f"Synthetic {i}.txt"),
                    hashlib.sha256(f"Synthetic performance material {i}".encode("utf-8")).hexdigest(),
                    len(f"Synthetic performance material {i}"),
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                )
                for i in range(1_200)
            ],
        )
        restarted.executemany(
            "INSERT INTO materials(id, stable_id, legacy_file_id, course_id, course_code, course_name, material_type, display_name, active, created_at, updated_at) VALUES (?, ?, ?, ?, 'TEST1001', 'Synthetic Study', 'reading', ?, 1, ?, ?)",
            [(10_000 + i, f"perf_material_{i}", 10_000 + i, course_id, f"Synthetic {i}", timestamp, timestamp) for i in range(1_200)],
        )
        restarted.commit()
        statements: list[str] = []
        restarted.set_trace_callback(statements.append)
        started = time.monotonic()
        large = server.study_overview(restarted, at=fixed)
        elapsed = time.monotonic() - started
        restarted.set_trace_callback(None)
        select_count = sum(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        check("thousand_material_queue_is_bounded", large["summary"]["total"] >= 1_203 and select_count == 1 and elapsed < 5, failures)

        source = (ROOT / "server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        check("raw_secret_surfaces_unchanged", "/api/study/overview" in source and "get_for_trusted_native_use" not in frontend, failures)
        check(
            "study_ui_is_integrated",
            "studyOverviewPanel" in frontend
            and "data-study-review" in frontend
            and "aria-pressed" in frontend
            and "displayCourseCode" not in frontend,
            failures,
        )
        check("study_tabs_are_mobile_safe", ".segmented-tabs {\n    grid-template-columns: repeat(2" in styles, failures)
        check("source_files_are_untouched", all(path.read_text(encoding="utf-8").startswith("Synthetic") for path in root.rglob("*.txt") if path.is_file()), failures)
        restarted.close()

    print(f"Study Engine acceptance: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
