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
from backup_restore_preview_acceptance import http_post, stage  # noqa: E402


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


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-restore-apply-") as temporary:
        base = Path(temporary)
        source = load_server(base / "source")
        source_conn = seed_state(source, base / "source", 100)
        archive = export_archive(source, source_conn)
        original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        structure = structural_archive(source, archive, base / "structure.zip")

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
        ):
            open_conn.close()

    if failures:
        print("Backup restore apply acceptance: FAIL")
        return 1
    print("Backup restore apply acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
