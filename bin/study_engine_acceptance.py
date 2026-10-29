#!/usr/bin/env python3
"""Synthetic acceptance checks for Study Engine V1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-study-engine-") as temporary:
        base = Path(temporary)
        server, root = load_server(base, "primary")
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
        legacy_file_count = conn.execute("SELECT COUNT(*) AS count FROM files").fetchone()["count"]
        conn.execute("DROP INDEX idx_material_study_queue")
        conn.execute("DROP TABLE material_study_state")
        conn.execute("DELETE FROM schema_migrations WHERE version=7")
        conn.commit()
        server.run_schema_migrations(conn)
        server.run_schema_migrations(conn)
        check(
            "post_34_upgrade_is_additive_and_idempotent",
            conn.execute("SELECT COUNT(*) AS count FROM files").fetchone()["count"] == legacy_file_count
            and conn.execute(
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
