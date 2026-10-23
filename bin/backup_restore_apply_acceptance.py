#!/usr/bin/env python3
"""Synthetic acceptance for transactional backup restore apply."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import LOCAL_BYTES, archive_members, load_server, seed_state  # noqa: E402
from backup_restore_preview_acceptance import http_post, mutate_dataset, stage  # noqa: E402


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in plan["sections"] for item in section["actions"]]


def decisions_for(plan: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for action in actions(plan):
        if action["classification"] == "CONFLICT":
            decision = "KEEP_LOCAL"
        elif action["classification"] in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
            decision = "SKIP"
        else:
            continue
        result.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return result


def logical_dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def clear_restore_state(conn: sqlite3.Connection) -> None:
    for table in ("notes", "stars", "attempts", "wrong_questions", "bookmarks", "study_sessions", "academic_change_event_state"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def export_archive(server: Any, conn: sqlite3.Connection, created_at: str = "2026-05-01T00:00:00+00:00") -> Path:
    result = server.export_user_owned_backup(conn, created_at=created_at)
    return server.EXPORT_DIR / result["filename"]


def write_archive(server: Any, path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    server.validate_backup_archive(path)
    return path


def structural_archive(server: Any, source: Path, output: Path) -> Path:
    members = archive_members(source)
    for member in server.BACKUP_DATA_MEMBERS:
        payload = json.loads(members[member])
        records = payload["records"]
        if member == "data/courses.json":
            records = [record for record in records if record["course_kind"] == "local"]
        elif member == "data/learning_units.json":
            records = [record for record in records if record["course_ref"] == "course_syn_alpha"]
        elif member != "data/terms.json":
            records = []
        payload["records"] = records
        members[member] = server.canonical_backup_json(payload)
    manifest = json.loads(members["manifest.json"])
    manifest["entries"] = {
        member: {
            "sha256": hashlib.sha256(members[member]).hexdigest(),
            "byte_size": len(members[member]),
            "record_count": len(json.loads(members[member])["records"]),
        }
        for member in server.BACKUP_DATA_MEMBERS
    }
    manifest["total_records"] = sum(item["record_count"] for item in manifest["entries"].values())
    members["manifest.json"] = server.canonical_backup_json(manifest)
    return write_archive(server, output, members)


def consistency_archive(server: Any, source: Path, output: Path) -> Path:
    members = archive_members(source)
    course_payload = json.loads(members["data/courses.json"])
    course_payload["records"].append(
        {
            "stable_id": "course_syn_beta",
            "term_ref": "term_syn_alpha",
            "display_name": "Synthetic Course B",
            "course_code": "SYN9300",
            "archived": False,
            "active": True,
            "sort_order": 5,
            "metadata_locked": True,
            "course_kind": "local",
            "created_at": "2026-05-01T00:00:00+00:00",
            "updated_at": "2026-05-01T00:00:00+00:00",
        }
    )
    members["data/courses.json"] = server.canonical_backup_json(course_payload)
    unit_payload = json.loads(members["data/learning_units.json"])
    unit_payload["records"].append(
        {
            "stable_id": "unit_syn_beta",
            "course_ref": "course_syn_beta",
            "label": "Module Beta",
            "number": 2,
            "kind": "module",
            "archived": False,
            "created_at": "2026-05-01T00:00:00+00:00",
            "updated_at": "2026-05-01T00:00:00+00:00",
        }
    )
    members["data/learning_units.json"] = server.canonical_backup_json(unit_payload)
    overlay_payload = json.loads(members["data/material_overlays.json"])
    overlay = next(record for record in overlay_payload["records"] if record["material_ref"] == "material_syn_local")
    overlay.update(
        {
            "course_ref": "course_syn_beta",
            "learning_unit_ref": "unit_syn_beta",
            "display_name": "Restored Name",
            "material_type": "quiz",
            "category": "Quiz",
            "exercise_type": "Quiz",
            "updated_at": "2026-05-02T00:00:00+00:00",
        }
    )
    members["data/material_overlays.json"] = server.canonical_backup_json(overlay_payload)
    manifest = json.loads(members["manifest.json"])
    for member in server.BACKUP_DATA_MEMBERS:
        payload = json.loads(members[member])
        manifest["entries"][member] = {
            "sha256": hashlib.sha256(members[member]).hexdigest(),
            "byte_size": len(members[member]),
            "record_count": len(payload["records"]),
        }
    manifest["total_records"] = sum(item["record_count"] for item in manifest["entries"].values())
    members["manifest.json"] = server.canonical_backup_json(manifest)
    return write_archive(server, output, members)


def seed_search_metadata(conn: sqlite3.Connection) -> int:
    file_id = int(conn.execute("SELECT id FROM files WHERE stable_id='material_syn_local'").fetchone()["id"])
    timestamp = "2026-04-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO questions(file_id, course_code, week_label, exercise_type, question_number, question_text, source_location, created_at) VALUES (?, 'SYN9200', 'Module Alpha', '', 'Q1', 'Synthetic alpha question', 'p.1', ?)",
        (file_id, timestamp),
    )
    conn.execute(
        "INSERT INTO solutions(file_id, course_code, week_label, exercise_type, solution_label, source_location, created_at) VALUES (?, 'SYN9200', 'Module Alpha', '', 'Official Solution', 'p.2', ?)",
        (file_id, timestamp),
    )
    file_row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    conn.execute(
        "INSERT INTO files_fts(rowid, filename, course_code, week_label, category, source_label, extracted_text) VALUES (?, ?, 'SYN9200', 'Module Alpha', 'Lecture', ?, 'searchable synthetic content')",
        (file_id, file_row["filename"], file_row["source_label"]),
    )
    conn.commit()
    return file_id


def expect_error(call: Any, code: str) -> bool:
    try:
        call()
    except Exception as exc:  # acceptance deliberately observes the sanitized category
        return getattr(exc, "code", str(exc)) == code
    return False


def prepare_target(base: Path, archive: Path, suffix: str, restore_id: str) -> tuple[Any, sqlite3.Connection, Path, dict[str, Any]]:
    server = load_server(base / suffix)
    conn = seed_state(server, base / suffix, 500, reverse_insert_order=True)
    clear_restore_state(conn)
    staged = stage(server, archive, restore_id)
    return server, conn, staged, server.preview_staged_restore(restore_id)


def mutate_record(
    payload: dict[str, Any],
    predicate: Any,
    mutation: Any,
) -> None:
    record = next(item for item in payload["records"] if predicate(item))
    mutation(record)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-restore-apply-") as temporary:
        base = Path(temporary)
        source = load_server(base / "source")
        source_conn = seed_state(source, base / "source", 100)
        archive = export_archive(source, source_conn)
        original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        structure = structural_archive(source, archive, base / "structure.zip")
        consistency = consistency_archive(source, archive, base / "consistency.zip")

        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "static/app.js").read_text(encoding="utf-8")
        catalog = (ROOT / "static/i18n/catalog.js").read_text(encoding="utf-8")
        check("pr29_merge_foundation_present", "read_backup_archive_snapshot" in server_source and "plan_restore_state" in server_source, failures)
        check("apply_endpoint_present", 'parsed.path == "/api/backup/restore-apply"' in server_source, failures)
        check("browser_arbitrary_path_absent", "Restore apply accepts only restore_id, expected_plan_revision, and decisions" in server_source, failures)
        check("expected_revision_required", 'expected_plan_revision' in server_source and "RESTORE_PLAN_REVISION_RE" in server_source, failures)
        check("begin_immediate_present", 'conn.execute("BEGIN IMMEDIATE")' in server_source, failures)
        check("process_local_lock_present", "_RESTORE_APPLY_LOCK = threading.Lock()" in server_source, failures)
        check("caller_owned_planner_present", "transaction_owned_by_caller=True" in server_source, failures)
        helper_body = server_source.split("def apply_restore_plan(", 1)[1].split("def verify_restore_source_candidates", 1)[0]
        check("apply_helper_has_no_commit", ".commit(" not in helper_body, failures)
        check("apply_helper_has_no_rollback", ".rollback(" not in helper_body, failures)
        check("no_schema_migration_added", server_source.count("phase2_academic_change_journal_migration),") == 1, failures)
        check("no_backup_mcp_tool", "backup/restore-apply" not in json.dumps(source.mcp_tool_descriptors()), failures)
        check("ui_has_apply_control", 'data-apply-restore="1"' in frontend, failures)
        check("ui_has_semantic_radio", 'type="radio"' in frontend and "<fieldset" in frontend, failures)
        check("ui_has_no_backup_wins", "RESTORE_BACKUP" not in frontend and "Use backup value" not in frontend, failures)
        check("ui_has_stale_review", "restore.localStateChanged" in frontend and "restore.reviewAgain" in frontend, failures)
        check("english_and_chinese_apply_strings", catalog.count('"restore.apply"') == 2 and "应用恢复" in catalog, failures)

        invalid = load_server(base / "invalid-archives")
        invalid_conn = seed_state(invalid, base / "invalid-archives", 400, reverse_insert_order=True)
        clear_restore_state(invalid_conn)
        invalid_before = logical_dump(invalid_conn)
        malformed_cases = (
            ("term_name_type", "data/terms.json", lambda r: True, lambda r: r.update(name=["bad"])),
            ("term_boolean_type", "data/terms.json", lambda r: True, lambda r: r.update(archived=1)),
            ("sort_order_bool", "data/terms.json", lambda r: True, lambda r: r.update(sort_order=True)),
            ("course_name_bound", "data/courses.json", lambda r: r["course_kind"] == "local", lambda r: r.update(display_name="x" * (invalid.BACKUP_COURSE_NAME_MAX + 1))),
            ("course_code_control", "data/courses.json", lambda r: r["course_kind"] == "local", lambda r: r.update(course_code="SYN\x00BAD")),
            ("course_kind_enum", "data/courses.json", lambda r: r["course_kind"] == "local", lambda r: r.update(course_kind="unknown")),
            ("unit_label_bound", "data/learning_units.json", lambda r: True, lambda r: r.update(label="x" * (invalid.BACKUP_UNIT_LABEL_MAX + 1))),
            ("unit_number_type", "data/learning_units.json", lambda r: True, lambda r: r.update(number="2")),
            ("material_name_bound", "data/material_overlays.json", lambda r: True, lambda r: r.update(display_name="x" * (invalid.BACKUP_MATERIAL_NAME_MAX + 1))),
            ("material_boolean_type", "data/material_overlays.json", lambda r: True, lambda r: r.update(active="true")),
            ("material_type_enum", "data/material_overlays.json", lambda r: True, lambda r: r.update(material_type="video-lecture")),
            ("source_negative_size", "data/local_sources.json", lambda r: True, lambda r: r.update(byte_size=-1)),
            ("source_sha", "data/local_sources.json", lambda r: True, lambda r: r.update(sha256="not-a-sha")),
            ("source_path_control", "data/local_sources.json", lambda r: True, lambda r: r["location"].update(relative_path="folder/bad\x00name.txt")),
            ("note_body_bound", "data/notes.json", lambda r: True, lambda r: r.update(body="x" * (invalid.BACKUP_NOTE_BODY_MAX + 1))),
            ("note_target_enum", "data/notes.json", lambda r: True, lambda r: r.update(target_type="file")),
            ("attempt_answer_bound", "data/study_records.json", lambda r: r["record_type"] == "attempt", lambda r: r.update(answer_text="x" * (invalid.BACKUP_ANSWER_TEXT_MAX + 1))),
            ("attempt_result_type", "data/study_records.json", lambda r: r["record_type"] == "attempt", lambda r: r.update(result=["bad"])),
            ("wrong_attempt_negative", "data/study_records.json", lambda r: r["record_type"] == "wrong_question_state", lambda r: r.update(attempt_count=-1)),
            ("bookmark_label_bound", "data/study_records.json", lambda r: r["record_type"] == "bookmark", lambda r: r.update(label="x" * (invalid.BACKUP_BOOKMARK_LABEL_MAX + 1))),
            ("session_mode_type", "data/study_records.json", lambda r: r["record_type"] == "study_session", lambda r: r.update(mode=7)),
            ("whats_new_seen_type", "data/whats_new_state.json", lambda r: True, lambda r: r.update(seen_at=["bad"])),
            ("timestamp_object", "data/terms.json", lambda r: True, lambda r: r.update(created_at={"bad": True})),
            ("integer_bool", "data/learning_units.json", lambda r: True, lambda r: r.update(number=True)),
        )
        malformed_results: dict[str, bool] = {}
        for index, (name, member, predicate, mutation) in enumerate(malformed_cases, start=1):
            malformed = mutate_dataset(
                invalid,
                archive,
                member,
                lambda payload, predicate=predicate, mutation=mutation: mutate_record(payload, predicate, mutation),
            )
            restore_id = f"{index + 100:032x}"
            stage(invalid, malformed, restore_id)
            preview_rejected = False
            apply_rejected = False
            try:
                invalid.preview_staged_restore(restore_id)
            except ValueError:
                preview_rejected = True
            try:
                invalid.apply_staged_restore(restore_id, "restore_" + "a" * 24, [])
            except ValueError:
                apply_rejected = True
            unchanged = logical_dump(invalid_conn) == invalid_before
            malformed_results[name] = preview_rejected and apply_rejected and unchanged
            check(f"malformed_{name}_rejected_without_mutation", malformed_results[name], failures)
        check("strict_type_validation", all(malformed_results[name] for name in ("term_name_type", "unit_number_type", "attempt_result_type", "session_mode_type", "whats_new_seen_type", "timestamp_object")), failures)
        check("strict_boolean_validation", all(malformed_results[name] for name in ("term_boolean_type", "material_boolean_type")), failures)
        check("strict_integer_validation", all(malformed_results[name] for name in ("sort_order_bool", "source_negative_size", "wrong_attempt_negative", "integer_bool")), failures)
        check("strict_text_bounds", all(malformed_results[name] for name in ("course_name_bound", "unit_label_bound", "material_name_bound", "note_body_bound", "attempt_answer_bound", "bookmark_label_bound")), failures)
        check("strict_control_character_validation", all(malformed_results[name] for name in ("course_code_control", "source_path_control")), failures)
        check("strict_enum_validation", all(malformed_results[name] for name in ("course_kind_enum", "material_type_enum", "note_target_enum")), failures)
        check("strict_sha_validation", malformed_results["source_sha"], failures)
        check("malformed_archives_zero_mutation", all(malformed_results.values()) and logical_dump(invalid_conn) == invalid_before, failures)

        server, conn, staged, plan = prepare_target(base, archive, "target", "a" * 32)
        before_archive_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        blocked = {"canvas": 0, "openai": 0, "credentials": 0}

        def deny(kind: str):
            def blocked_call(*_args: Any, **_kwargs: Any) -> Any:
                blocked[kind] += 1
                raise AssertionError(f"{kind} must remain unused")
            return blocked_call

        server.with_canvas_connector = deny("canvas")
        server.openai_responses_request = deny("openai")
        server.initialize_credential_client = deny("credentials")
        uncommitted_seen: list[int] = []

        def inspect_before_commit(_writer: sqlite3.Connection) -> None:
            observer = server.connect_backup_db_readonly()
            try:
                uncommitted_seen.append(int(observer.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]))
            finally:
                observer.close()

        result = server.apply_staged_restore(
            "a" * 32,
            plan["plan_revision"],
            decisions_for(plan),
            before_commit=inspect_before_commit,
        )
        check("safe_restore_succeeds", result["ok"] is True, failures)
        check("valid_exporter_roundtrip", result["ok"] is True and result["applied"]["created"] > 0, failures)
        check("note_multiplicity_restored", conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"] == 4, failures)
        check("star_created", conn.execute("SELECT COUNT(*) AS c FROM stars").fetchone()["c"] == 1, failures)
        check("attempt_multiplicity_restored", conn.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"] == 3, failures)
        check("wrong_question_state_restored", conn.execute("SELECT COUNT(*) AS c FROM wrong_questions").fetchone()["c"] == 3, failures)
        check("session_multiplicity_restored", conn.execute("SELECT COUNT(*) AS c FROM study_sessions").fetchone()["c"] == 3, failures)
        check("bookmark_created", conn.execute("SELECT COUNT(*) AS c FROM bookmarks").fetchone()["c"] == 1, failures)
        check("whats_new_marked_seen", conn.execute("SELECT COUNT(*) AS c FROM academic_change_event_state WHERE seen_at IS NOT NULL").fetchone()["c"] == 1, failures)
        check("second_connection_cannot_see_uncommitted", uncommitted_seen == [0], failures)
        check("all_rows_visible_after_commit", result["applied"]["created"] == 15 and result["applied"]["updated_user_state"] == 1, failures)
        check("network_and_credentials_unused", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)
        check("staged_copy_retained", staged.is_file() and hashlib.sha256(staged.read_bytes()).hexdigest() == before_archive_hash, failures)
        check("external_archive_untouched", hashlib.sha256(archive.read_bytes()).hexdigest() == original_hash, failures)
        post_plan = server.preview_staged_restore("a" * 32)
        check("post_apply_preview_noop", post_plan["summary"]["create"] == 0 and post_plan["summary"]["update_user_state"] == 0, failures)
        check("post_apply_all_actions_present", post_plan["summary"]["noop"] == post_plan["summary"]["total"], failures)
        check("old_revision_rejected", expect_error(lambda: server.apply_staged_restore("a" * 32, plan["plan_revision"], []), "stale_restore_plan"), failures)
        counts_before_idempotent = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("notes", "attempts", "study_sessions"))
        idempotent = server.apply_staged_restore("a" * 32, post_plan["plan_revision"], decisions_for(post_plan))
        counts_after_idempotent = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("notes", "attempts", "study_sessions"))
        check("fresh_noop_apply_reports_zero_writes", idempotent["applied"]["created"] == 0 and idempotent["applied"]["updated_user_state"] == 0, failures)
        check("fresh_noop_apply_creates_no_duplicates", counts_before_idempotent == counts_after_idempotent, failures)

        fresh = load_server(base / "structural-target")
        fresh_conn = fresh.connect_db()
        fresh.init_db(fresh_conn)
        fresh_conn.close()
        stage(fresh, structure, "b" * 32)
        structure_plan = fresh.preview_staged_restore("b" * 32)
        structure_result = fresh.apply_staged_restore("b" * 32, structure_plan["plan_revision"], [])
        verify = fresh.connect_db()
        check("term_create", verify.execute("SELECT name FROM terms WHERE stable_id='term_syn_alpha'").fetchone()["name"] == "Synthetic Term", failures)
        check("local_course_create", verify.execute("SELECT source_kind FROM courses WHERE stable_id='course_syn_alpha'").fetchone()["source_kind"] == "managed", failures)
        check("learning_unit_create", verify.execute("SELECT origin FROM weeks WHERE stable_id='unit_syn_alpha'").fetchone()["origin"] == "managed", failures)
        check("structural_apply_count", structure_result["applied"]["created"] == 3, failures)
        check("remote_overlay_not_created", verify.execute("SELECT 1 FROM courses WHERE stable_id='course_syn_remote'").fetchone() is None, failures)
        verify.close()

        overlay, overlay_conn, _overlay_copy, _overlay_plan = prepare_target(base, archive, "overlay", "4" * 32)
        overlay_conn.execute("UPDATE files SET metadata_locked=0, display_name='Uncustomized' WHERE stable_id='material_syn_local'")
        overlay_conn.commit()
        overlay_plan = overlay.preview_staged_restore("4" * 32)
        overlay_action = next(item for item in actions(overlay_plan) if item["dataset"] == "material_overlays")
        overlay.apply_staged_restore("4" * 32, overlay_plan["plan_revision"], decisions_for(overlay_plan))
        overlay_file = overlay_conn.execute("SELECT display_name, metadata_locked FROM files WHERE stable_id='material_syn_local'").fetchone()
        check("material_overlay_classified_update_user_state", overlay_action["classification"] == "UPDATE_USER_STATE", failures)
        check("material_overlay_applied_to_resolved_material", overlay_file["display_name"] == "My guide" and overlay_file["metadata_locked"] == 1, failures)

        domain = load_server(base / "domain-consistency")
        domain_conn = seed_state(domain, base / "domain-consistency", 900, reverse_insert_order=True)
        clear_restore_state(domain_conn)
        domain_file_id = seed_search_metadata(domain_conn)
        domain_conn.execute("UPDATE files SET metadata_locked=0 WHERE id=?", (domain_file_id,))
        domain_conn.commit()
        before_material = domain_conn.execute(
            "SELECT stable_id, current_version_id FROM materials WHERE legacy_file_id=?",
            (domain_file_id,),
        ).fetchone()
        before_domain_counts = tuple(
            domain_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("materials", "material_versions", "blobs", "sources")
        )
        before_version_state = tuple(
            tuple(row)
            for row in domain_conn.execute(
                "SELECT stable_id, sha256, blob_id, source_id FROM material_versions WHERE legacy_file_id=? ORDER BY id",
                (domain_file_id,),
            ).fetchall()
        )
        stage(domain, consistency, "7" * 32)
        domain_plan = domain.preview_staged_restore("7" * 32)
        writer_alignment: list[bool] = []
        observer_old_state: list[bool] = []

        def inspect_domain_before_commit(writer: sqlite3.Connection) -> None:
            file_row = writer.execute("SELECT * FROM files WHERE id=?", (domain_file_id,)).fetchone()
            material_row = writer.execute("SELECT * FROM materials WHERE legacy_file_id=?", (domain_file_id,)).fetchone()
            writer_alignment.append(
                all(
                    file_row[field] == material_row[field]
                    for field in (
                        "course_id", "week_id", "course_code", "course_name", "week_label",
                        "week_number", "material_type", "display_name", "active", "removed_at",
                    )
                )
                and file_row["section"] == "02 Exercises"
            )
            observer = domain.connect_backup_db_readonly()
            try:
                old = observer.execute("SELECT course_code, display_name FROM files WHERE id=?", (domain_file_id,)).fetchone()
                observer_old_state.append(old["course_code"] == "SYN9200" and old["display_name"] != "Restored Name")
            finally:
                observer.close()

        domain.apply_staged_restore(
            "7" * 32,
            domain_plan["plan_revision"],
            decisions_for(domain_plan),
            before_commit=inspect_domain_before_commit,
        )
        domain_file = domain_conn.execute("SELECT * FROM files WHERE id=?", (domain_file_id,)).fetchone()
        domain_material = domain_conn.execute("SELECT * FROM materials WHERE legacy_file_id=?", (domain_file_id,)).fetchone()
        check("material_domain_projection_synchronized_before_commit", writer_alignment == [True], failures)
        check("material_domain_projection_atomic_visibility", observer_old_state == [True], failures)
        check(
            "material_domain_projection_synchronized_after_commit",
            domain_file["course_code"] == domain_material["course_code"] == "SYN9300"
            and domain_file["week_label"] == domain_material["week_label"] == "Module Beta"
            and domain_file["display_name"] == domain_material["display_name"] == "Restored Name"
            and domain_file["material_type"] == domain_material["material_type"] == "quiz",
            failures,
        )
        check("section_classification_synchronized", domain_file["section"] == "02 Exercises" and domain_file["category"] == "Quiz" and domain_file["exercise_type"] == "Quiz", failures)
        search_rows = {
            table: domain_conn.execute(
                f"SELECT course_code, week_label, {field} AS classification FROM {table} WHERE {id_field}=?",
                (domain_file_id,),
            ).fetchall()
            for table, field, id_field in (
                ("document_chunks", "category", "file_id"),
                ("questions", "exercise_type", "file_id"),
                ("solutions", "exercise_type", "file_id"),
                ("files_fts", "category", "rowid"),
            )
        }
        check(
            "search_metadata_synchronized",
            all(
                rows and all(row["course_code"] == "SYN9300" and row["week_label"] == "Module Beta" and row["classification"] == "Quiz" for row in rows)
                for rows in search_rows.values()
            ),
            failures,
        )
        fts_result = domain_conn.execute(
            "SELECT f.course_code, f.week_label, f.category FROM files_fts JOIN files f ON files_fts.rowid=f.id WHERE files_fts MATCH 'searchable'"
        ).fetchone()
        check("normal_local_search_sees_restored_metadata", fts_result is not None and tuple(fts_result) == ("SYN9300", "Module Beta", "Quiz"), failures)
        after_domain_counts = tuple(
            domain_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("materials", "material_versions", "blobs", "sources")
        )
        after_version_state = tuple(
            tuple(row)
            for row in domain_conn.execute(
                "SELECT stable_id, sha256, blob_id, source_id FROM material_versions WHERE legacy_file_id=? ORDER BY id",
                (domain_file_id,),
            ).fetchall()
        )
        after_material = domain_conn.execute(
            "SELECT stable_id, current_version_id FROM materials WHERE legacy_file_id=?",
            (domain_file_id,),
        ).fetchone()
        check("no_new_domain_identity_rows_from_overlay", before_domain_counts == after_domain_counts, failures)
        check("material_stable_identity_unchanged", tuple(before_material) == tuple(after_material), failures)
        check("material_version_blob_source_unchanged", before_version_state == after_version_state, failures)

        atomic = load_server(base / "overlay-atomic")
        atomic_conn = seed_state(atomic, base / "overlay-atomic", 1000, reverse_insert_order=True)
        clear_restore_state(atomic_conn)
        atomic_file_id = seed_search_metadata(atomic_conn)
        atomic_conn.execute("UPDATE files SET metadata_locked=0 WHERE id=?", (atomic_file_id,))
        atomic_conn.commit()
        stage(atomic, consistency, "8" * 32)
        atomic_plan = atomic.preview_staged_restore("8" * 32)
        atomic_before = logical_dump(atomic_conn)
        atomic_failed = False
        try:
            atomic.apply_staged_restore(
                "8" * 32,
                atomic_plan["plan_revision"],
                decisions_for(atomic_plan),
                failure_point="after_overlay",
            )
        except RuntimeError:
            atomic_failed = True
        check("overlay_sync_atomic_rollback", atomic_failed and logical_dump(atomic_conn) == atomic_before, failures)

        conflict, conflict_conn, _conflict_stage, conflict_plan = prepare_target(base, archive, "conflict", "c" * 32)
        conflict_conn.execute("INSERT INTO bookmarks(target_type, target_id, label, created_at) SELECT 'file', id, 'Different label', '2026-01-01' FROM files WHERE stable_id='material_syn_local'")
        conflict_conn.commit()
        conflict_plan = conflict.preview_staged_restore("c" * 32)
        required = decisions_for(conflict_plan)
        check("bookmark_conflict_requires_keep_local", any(item["dataset"] == "study_records" and item["decision"] == "KEEP_LOCAL" for item in required), failures)
        check("incomplete_decisions_rejected", expect_error(lambda: conflict.apply_staged_restore("c" * 32, conflict_plan["plan_revision"], []), "restore_decisions_incomplete"), failures)
        check("unknown_decision_rejected", expect_error(lambda: conflict.apply_staged_restore("c" * 32, conflict_plan["plan_revision"], [{"dataset": "courses", "stable_id": "unknown", "decision": "KEEP_LOCAL"}]), "unknown_restore_decision_target"), failures)
        duplicate = required + required[:1]
        check("duplicate_decision_rejected", expect_error(lambda: conflict.apply_staged_restore("c" * 32, conflict_plan["plan_revision"], duplicate), "duplicate_restore_decision"), failures)
        unsupported = [dict(item, decision="RESTORE_BACKUP") for item in required]
        check("backup_wins_rejected", expect_error(lambda: conflict.apply_staged_restore("c" * 32, conflict_plan["plan_revision"], unsupported), "unsupported_restore_decision"), failures)
        kept = conflict.apply_staged_restore("c" * 32, conflict_plan["plan_revision"], required)
        check("keep_local_preserves_bookmark", conflict_conn.execute("SELECT label FROM bookmarks").fetchone()["label"] == "Different label", failures)
        check("keep_local_reported", kept["applied"]["kept_local"] >= 1, failures)

        wrong = load_server(base / "wrong-conflict")
        wrong_conn = seed_state(wrong, base / "wrong-conflict", 700, reverse_insert_order=True)
        wrong_conn.execute("UPDATE wrong_questions SET mastery='local-newer' WHERE id=(SELECT MIN(id) FROM wrong_questions)")
        wrong_conn.commit()
        stage(wrong, archive, "5" * 32)
        wrong_plan = wrong.preview_staged_restore("5" * 32)
        wrong_conflicts = [item for item in actions(wrong_plan) if item["classification"] == "CONFLICT" and item["label"] == "Wrong question"]
        wrong.apply_staged_restore("5" * 32, wrong_plan["plan_revision"], decisions_for(wrong_plan))
        check("wrong_question_conflict_detected", len(wrong_conflicts) >= 1, failures)
        check("wrong_question_keep_local_preserves_progress", wrong_conn.execute("SELECT COUNT(*) FROM wrong_questions WHERE mastery='local-newer'").fetchone()[0] == 1, failures)

        missing_event, missing_event_conn, _missing_event_copy, _missing_event_plan = prepare_target(base, archive, "missing-event", "6" * 32)
        missing_event_conn.execute("DELETE FROM academic_change_events")
        missing_event_conn.commit()
        missing_event_plan = missing_event.preview_staged_restore("6" * 32)
        event_action = next(item for item in actions(missing_event_plan) if item["dataset"] == "whats_new_state")
        missing_event_decisions = decisions_for(missing_event_plan)
        check("missing_event_is_unsupported", event_action["classification"] == "UNSUPPORTED_BY_V1", failures)
        check("missing_event_requires_skip", any(item["stable_id"] == event_action["stable_id"] and item["decision"] == "SKIP" for item in missing_event_decisions), failures)
        missing_event.apply_staged_restore("6" * 32, missing_event_plan["plan_revision"], missing_event_decisions)
        check("missing_event_not_created", missing_event_conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0, failures)

        relink, relink_conn, _relink_copy, _relink_plan = prepare_target(base, archive, "relink", "2" * 32)
        relink_source = base / "relink" / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "synthetic-guide.txt"
        relink_source.unlink()
        relink_plan = relink.preview_staged_restore("2" * 32)
        relink_decisions = decisions_for(relink_plan)
        check("relink_requires_explicit_skip", any(item["decision"] == "SKIP" for item in relink_decisions), failures)
        check("relink_incomplete_rejected", expect_error(lambda: relink.apply_staged_restore("2" * 32, relink_plan["plan_revision"], []), "restore_decisions_incomplete"), failures)
        relink_result = relink.apply_staged_restore("2" * 32, relink_plan["plan_revision"], relink_decisions)
        check("relink_skip_does_not_create_material_records", relink_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0, failures)
        check("relink_skip_reported", relink_result["applied"]["skipped"] > 0, failures)

        malformed_revision = expect_error(lambda: conflict.apply_staged_restore("c" * 32, "bad", []), "invalid_restore_plan_revision")
        check("malformed_revision_rejected", malformed_revision, failures)
        status, arbitrary = http_post(conflict, "/api/backup/restore-apply", {"path": str(archive)})
        check("http_path_payload_rejected", status == 400 and "path" not in json.dumps(arbitrary), failures)

        stale, stale_conn, stale_copy, stale_plan = prepare_target(base, archive, "stale", "d" * 32)
        stale_conn.execute("UPDATE terms SET name='Changed locally' WHERE stable_id='term_syn_alpha'")
        stale_conn.commit()
        check("stale_target_revision_rejected", expect_error(lambda: stale.apply_staged_restore("d" * 32, stale_plan["plan_revision"], decisions_for(stale_plan)), "stale_restore_plan"), failures)
        check("stale_target_writes_nothing", stale_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0, failures)
        stale_copy.write_bytes(structure.read_bytes())
        check("changed_archive_rejected", expect_error(lambda: stale.apply_staged_restore("d" * 32, stale_plan["plan_revision"], decisions_for(stale_plan)), "stale_restore_plan"), failures)
        stale_copy.write_bytes(b"not-a-zip")
        malformed_before = stale_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        malformed_rejected = False
        try:
            stale.apply_staged_restore("d" * 32, stale_plan["plan_revision"], decisions_for(stale_plan))
        except (ValueError, zipfile.BadZipFile):
            malformed_rejected = True
        check("malformed_archive_rejected", malformed_rejected, failures)
        check("malformed_archive_zero_mutation", stale_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == malformed_before, failures)

        dependency = load_server(base / "dependency")
        dependency_conn = dependency.connect_db()
        dependency.init_db(dependency_conn)
        dependency_conn.close()
        stage(dependency, structure, "3" * 32)
        snapshot = dependency.read_backup_archive_snapshot(dependency.staged_restore_path("3" * 32))
        _validation, dep_manifest, dep_datasets = dependency.validate_backup_snapshot(snapshot)
        dependency_conn = dependency.connect_restore_db_writable()
        dependency_conn.execute("BEGIN IMMEDIATE")
        dep_plan, dep_context = dependency.plan_restore_state(
            dependency_conn, dep_manifest, dep_datasets, transaction_owned_by_caller=True,
        )
        for section in dep_plan["sections"]:
            for action in section["actions"]:
                if action["stable_id"] == "course_syn_alpha":
                    action["classification"] = "CONFLICT"
        dependency_rejected = expect_error(
            lambda: dependency.apply_restore_plan(dependency_conn, dep_datasets, dep_plan, dep_context, {}),
            "restore_dependency_unresolved",
        )
        check("dependency_inconsistency_rejected_before_mutation", dependency_rejected, failures)
        check("dependency_rejection_writes_nothing", dependency_conn.execute("SELECT 1 FROM courses WHERE stable_id='course_syn_alpha'").fetchone() is None, failures)
        dependency_conn.rollback()
        dependency_conn.close()

        for index, failure_point in enumerate(("after_first_create", "midway_notes", "after_study_records", "before_whats_new", "before_commit"), start=1):
            rollback_server, rollback_conn, _copy, rollback_plan = prepare_target(base, archive, f"rollback-{index}", f"{index + 4:032x}")
            before = logical_dump(rollback_conn)
            failed = False
            try:
                rollback_server.apply_staged_restore(
                    f"{index + 4:032x}", rollback_plan["plan_revision"], decisions_for(rollback_plan), failure_point=failure_point,
                )
            except RuntimeError:
                failed = True
            check(f"rollback_{failure_point}", failed and logical_dump(rollback_conn) == before, failures)
            rollback_conn.close()

        source_stale, source_stale_conn, _source_copy, source_stale_plan = prepare_target(base, archive, "source-stale", "e" * 32)
        source_path = base / "source-stale" / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "synthetic-guide.txt"
        before_source_apply = logical_dump(source_stale_conn)
        check(
            "source_toctou_rejected",
            expect_error(
                lambda: source_stale.apply_staged_restore(
                    "e" * 32,
                    source_stale_plan["plan_revision"],
                    decisions_for(source_stale_plan),
                    before_commit=lambda _conn: source_path.write_bytes(b"changed during apply"),
                ),
                "stale_restore_source_state",
            ),
            failures,
        )
        check("source_toctou_rolls_back", logical_dump(source_stale_conn) == before_source_apply, failures)

        snapshot_server, snapshot_conn, snapshot_copy, snapshot_plan = prepare_target(base, archive, "snapshot", "f" * 32)
        snapshot_result = snapshot_server.apply_staged_restore(
            "f" * 32,
            snapshot_plan["plan_revision"],
            decisions_for(snapshot_plan),
            after_snapshot=lambda: snapshot_copy.write_bytes(structure.read_bytes()),
        )
        check("immutable_snapshot_survives_path_replacement", snapshot_result["applied"]["created"] == 15, failures)
        check("next_operation_observes_replacement", snapshot_server.preview_staged_restore("f" * 32)["plan_revision"] != snapshot_plan["plan_revision"], failures)

        concurrent, concurrent_conn, _concurrent_copy, concurrent_plan = prepare_target(base, archive, "concurrent", "1" * 32)
        outcomes: list[str] = []

        def run_concurrent() -> None:
            try:
                concurrent.apply_staged_restore("1" * 32, concurrent_plan["plan_revision"], decisions_for(concurrent_plan))
                outcomes.append("success")
            except Exception as exc:
                outcomes.append(getattr(exc, "code", str(exc)))

        threads = [threading.Thread(target=run_concurrent) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        check("concurrent_old_plan_one_success", outcomes.count("success") == 1, failures)
        check("concurrent_old_plan_second_stale", outcomes.count("stale_restore_plan") == 1, failures)
        check("concurrent_apply_no_duplicates", concurrent_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 4, failures)

        check("ai_history_untouched", conn.execute("SELECT COUNT(*) FROM ai_conversations").fetchone()[0] == 1, failures)
        check("canvas_state_untouched", conn.execute("SELECT COUNT(*) FROM course_offering_selections").fetchone()[0] == 1, failures)
        check("original_study_file_unmodified", hashlib.sha256((base / "target" / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "synthetic-guide.txt").read_bytes()).hexdigest() == hashlib.sha256(LOCAL_BYTES).hexdigest(), failures)

        for open_conn in (
            source_conn, conn, overlay_conn, conflict_conn, wrong_conn, missing_event_conn,
            relink_conn, stale_conn, source_stale_conn, snapshot_conn, concurrent_conn,
            domain_conn, atomic_conn,
            invalid_conn,
        ):
            open_conn.close()

    if failures:
        print("Backup restore apply acceptance: FAIL")
        return 1
    print("Backup restore apply acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
