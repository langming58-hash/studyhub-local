#!/usr/bin/env python3
"""Synthetic acceptance for explicit backup conflict recovery and durable Undo."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import load_server, seed_state  # noqa: E402
from backup_restore_apply_acceptance import export_archive, logical_dump, seed_search_metadata  # noqa: E402
from backup_restore_preview_acceptance import http_post, stage  # noqa: E402
from backup_restore_relink_apply_acceptance import fixture as relink_fixture  # noqa: E402


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in plan["sections"] for item in section["actions"]]


def decisions_for(
    plan: dict[str, Any],
    *,
    backup: bool = False,
    keep_stable_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    keep_stable_ids = keep_stable_ids or set()
    result: list[dict[str, str]] = []
    for action in actions(plan):
        classification = action["classification"]
        allowed = action.get("allowed_decisions", [])
        if classification == "CONFLICT":
            decision = "RESTORE_BACKUP" if backup and "RESTORE_BACKUP" in allowed else "KEEP_LOCAL"
            if action["stable_id"] in keep_stable_ids:
                decision = "KEEP_LOCAL"
        elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
            decision = "SKIP"
        else:
            continue
        result.append(
            {
                "dataset": action["dataset"],
                "stable_id": action["stable_id"],
                "decision": decision,
            }
        )
    return result


def expect_code(call: Any, code: str) -> bool:
    try:
        call()
    except Exception as exc:  # acceptance observes only the sanitized error category
        return getattr(exc, "code", str(exc)) == code
    return False


def mutate_eligible_state(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE terms SET name='Local Term' WHERE stable_id='term_syn_alpha'")
    conn.execute(
        "UPDATE courses SET name='Local Course', display_name='Local Course' "
        "WHERE stable_id='course_syn_alpha'"
    )
    conn.execute("UPDATE weeks SET kind='week', archived=1 WHERE stable_id='unit_syn_alpha'")
    conn.execute(
        "UPDATE files SET display_name='Local Material', category='Reading', "
        "exercise_type='Reading', metadata_locked=1 WHERE stable_id='material_syn_local'"
    )
    conn.execute(
        "UPDATE document_chunks SET category='Reading', exercise_type='Reading' "
        "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute(
        "UPDATE files_fts SET category='Reading' "
        "WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute("UPDATE notes SET body='Local Note', updated_at='2026-05-01T00:00:00+00:00'")
    conn.execute(
        "UPDATE wrong_questions SET mistake='Local Mistake', mastery='local'"
    )
    conn.execute(
        "UPDATE study_sessions SET mode='local-mode', ended_at='2026-05-01T01:00:00+00:00'"
    )
    conn.execute("UPDATE bookmarks SET label='Local Bookmark'")
    conn.commit()


def user_state(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "term": tuple(conn.execute(
            "SELECT name, archived, sort_order FROM terms WHERE stable_id='term_syn_alpha'"
        ).fetchone()),
        "course": tuple(conn.execute(
            "SELECT display_name, archived, active, sort_order, metadata_locked "
            "FROM courses WHERE stable_id='course_syn_alpha'"
        ).fetchone()),
        "unit": tuple(conn.execute(
            "SELECT week_label, kind, archived FROM weeks WHERE stable_id='unit_syn_alpha'"
        ).fetchone()),
        "file": tuple(conn.execute(
            "SELECT display_name, category, exercise_type, metadata_locked "
            "FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()),
        "notes": [tuple(row) for row in conn.execute(
            "SELECT body, created_at, updated_at FROM notes ORDER BY id"
        ).fetchall()],
        "wrong": [tuple(row) for row in conn.execute(
            "SELECT mistake, mastery, created_at FROM wrong_questions ORDER BY id"
        ).fetchall()],
        "sessions": [tuple(row) for row in conn.execute(
            "SELECT mode, ended_at, started_at FROM study_sessions ORDER BY id"
        ).fetchall()],
        "bookmarks": [tuple(row) for row in conn.execute(
            "SELECT label, created_at FROM bookmarks ORDER BY id"
        ).fetchall()],
    }


def prepare_target(base: Path, archive: Path, name: str, restore_id: str) -> tuple[Any, sqlite3.Connection, dict[str, Any]]:
    server = load_server(base / name)
    conn = seed_state(server, base / name, 500, reverse_insert_order=True)
    seed_search_metadata(conn)
    mutate_eligible_state(conn)
    stage(server, archive, restore_id)
    return server, conn, server.preview_staged_restore(restore_id)


def prepare_mixed_restore(
    base: Path,
    suffix: str,
) -> tuple[Any, sqlite3.Connection, Path, dict[str, Any], dict[str, Any]]:
    target, conn, restore_id, source_id, selected, initial = relink_fixture(base, suffix)
    conn.execute("DELETE FROM notes WHERE id=(SELECT MIN(id) FROM notes)")
    conn.execute("UPDATE bookmarks SET label='Local Bookmark'")
    conn.execute("UPDATE terms SET name='Local Term' WHERE stable_id='term_syn_alpha'")
    conn.execute("DELETE FROM academic_change_events")
    conn.commit()
    plan = target.preview_staged_restore(restore_id)
    relink_id = initial["relinks"][0]["relink_id"]
    relink = target.preview_restore_relink_candidate(
        restore_id,
        plan["plan_revision"],
        source_id,
        relink_id,
    )
    decisions = decisions_for(
        relink["plan"],
        backup=True,
        keep_stable_ids={"term_syn_alpha"},
    )
    source_decision = next(
        item for item in decisions
        if item["dataset"] == "local_sources" and item["stable_id"] == source_id
    )
    source_decision["decision"] = "RELINK"
    request = {
        "restore_id": restore_id,
        "expected_plan_revision": relink["plan"]["plan_revision"],
        "decisions": decisions,
        "relinks": [{
            "source_stable_id": source_id,
            "relink_id": relink_id,
            "relink_revision": relink["relink_revision"],
        }],
    }
    return target, conn, selected, relink["plan"], request


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-backup-closure-") as temporary:
        base = Path(temporary)
        source = load_server(base / "source")
        source_conn = seed_state(source, base / "source", 100)
        archive = export_archive(source, source_conn)
        source_conn.close()

        server, conn, plan = prepare_target(base, archive, "target", "a" * 32)
        original_path = Path(conn.execute(
            "SELECT original_path FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()["original_path"])
        original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
        before = user_state(conn)
        domain_counts = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "materials", "material_versions", "blobs")
        )
        plan_json = json.dumps(plan, ensure_ascii=False)
        conflicts = [item for item in actions(plan) if item["classification"] == "CONFLICT"]
        eligible = [item for item in conflicts if "RESTORE_BACKUP" in item.get("allowed_decisions", [])]
        datasets = {item["dataset"] for item in eligible}
        check("eligible_conflict_datasets", {"terms", "courses", "learning_units", "material_overlays", "notes", "study_records"}.issubset(datasets), failures)
        check("no_private_targets_in_preview", "private_conflict_targets" not in plan_json and "original_path" not in plan_json, failures)
        note_conflicts = [item for item in eligible if item["dataset"] == "notes"]
        check("note_body_changes_are_conflicts", len(note_conflicts) == 4, failures)
        check("note_conflict_occurrences_are_distinct", len({item["stable_id"] for item in note_conflicts}) == 4, failures)

        blocked = {"canvas": 0, "openai": 0, "credentials": 0}

        def deny(kind: str):
            def denied(*_args: Any, **_kwargs: Any) -> Any:
                blocked[kind] += 1
                raise AssertionError(f"{kind} must remain unused")
            return denied

        server.with_canvas_connector = deny("canvas")
        server.openai_responses_request = deny("openai")
        server.initialize_credential_client = deny("credentials")
        mixed = decisions_for(plan, backup=True, keep_stable_ids={"term_syn_alpha"})
        result = server.apply_staged_restore("a" * 32, plan["plan_revision"], mixed)
        operation_id = result["applied"].get("restore_operation_id", "")
        check("mixed_restore_succeeds", result["ok"] is True and result["applied"]["restored_from_backup"] >= 1, failures)
        check("explicit_keep_preserved", conn.execute(
            "SELECT name FROM terms WHERE stable_id='term_syn_alpha'"
        ).fetchone()["name"] == "Local Term", failures)
        check("course_backup_value_applied", conn.execute(
            "SELECT display_name FROM courses WHERE stable_id='course_syn_alpha'"
        ).fetchone()["display_name"] == "My Synthetic Course", failures)
        check("unit_backup_value_applied", conn.execute(
            "SELECT kind FROM weeks WHERE stable_id='unit_syn_alpha'"
        ).fetchone()["kind"] == "module", failures)
        check("overlay_backup_value_applied", conn.execute(
            "SELECT display_name FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()["display_name"] == "My guide", failures)
        check("notes_backup_values_applied", conn.execute(
            "SELECT COUNT(*) FROM notes WHERE body='Local Note'"
        ).fetchone()[0] == 0, failures)
        check("study_state_backup_values_applied", conn.execute(
            "SELECT COUNT(*) FROM wrong_questions WHERE mastery='local'"
        ).fetchone()[0] == 0 and conn.execute(
            "SELECT COUNT(*) FROM study_sessions WHERE mode='local-mode'"
        ).fetchone()[0] == 0, failures)
        check("bookmark_backup_value_applied", conn.execute(
            "SELECT COUNT(*) FROM bookmarks WHERE label='Local Bookmark'"
        ).fetchone()[0] == 0, failures)
        check("canonical_domain_identity_preserved", domain_counts == tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "materials", "material_versions", "blobs")
        ), failures)
        check("search_projection_consistent", conn.execute(
            "SELECT category FROM files_fts WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
        ).fetchone()["category"] == "Lecture", failures)
        check("original_file_untouched", hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash, failures)
        check("network_and_credentials_unused", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)

        undo = server.preview_restore_undo(operation_id)
        check("undo_available_after_restart_safe_record", undo.get("undo_available") is True, failures)
        restarted = load_server(base / "target")
        latest = restarted.latest_restore_undo_operation().get("operation") or {}
        restarted_undo = restarted.preview_restore_undo(operation_id)
        check("latest_restore_persists_restart", latest.get("operation_id") == operation_id, failures)
        check("undo_preview_persists_restart", restarted_undo.get("undo_available") is True, failures)
        restarted.apply_restore_undo(operation_id, restarted_undo["undo_revision"])
        check("undo_restores_exact_pre_restore_user_state", user_state(conn) == before, failures)
        check("undo_preserves_original_file", hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash, failures)
        check("second_undo_rejected", expect_code(
            lambda: restarted.apply_restore_undo(operation_id, restarted_undo["undo_revision"]),
            "ALREADY_UNDONE",
        ), failures)
        conn.close()

        rollback, rollback_conn, rollback_plan = prepare_target(base, archive, "rollback", "b" * 32)
        rollback_before = logical_dump(rollback_conn)
        check("failure_injected_restore_rolls_back", expect_code(
            lambda: rollback.apply_staged_restore(
                "b" * 32,
                rollback_plan["plan_revision"],
                decisions_for(rollback_plan, backup=True),
                failure_point="after_restore_backup",
            ),
            "synthetic_restore_failure_after_restore_backup",
        ) and logical_dump(rollback_conn) == rollback_before, failures)
        rollback_conn.close()

        for index, failure_point in enumerate((
            "before_restore_operation_recorded",
            "after_restore_operation_recorded",
            "before_restore_commit",
        ), start=1):
            failure_restore_id = f"{index + 5:032x}"
            failed_server, failed_conn, failed_plan = prepare_target(
                base,
                archive,
                f"journal-failure-{index}",
                failure_restore_id,
            )
            failed_before = logical_dump(failed_conn)
            failed_original = Path(failed_conn.execute(
                "SELECT original_path FROM files WHERE stable_id='material_syn_local'"
            ).fetchone()["original_path"])
            failed_hash = hashlib.sha256(failed_original.read_bytes()).hexdigest()
            check(
                f"backup_restore_{failure_point}_rolls_back",
                expect_code(
                    lambda point=failure_point, restore_id=failure_restore_id: failed_server.apply_staged_restore(
                        restore_id,
                        failed_plan["plan_revision"],
                        decisions_for(failed_plan, backup=True),
                        failure_point=point,
                    ),
                    f"synthetic_restore_failure_{failure_point}",
                )
                and logical_dump(failed_conn) == failed_before
                and failed_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 0
                and hashlib.sha256(failed_original.read_bytes()).hexdigest() == failed_hash,
                failures,
            )
            failed_conn.close()

        stale, stale_conn, stale_plan = prepare_target(base, archive, "stale", "c" * 32)
        stale_conn.execute("UPDATE bookmarks SET label='Changed after preview'")
        stale_conn.commit()
        check("local_change_after_preview_rejected", expect_code(
            lambda: stale.apply_staged_restore(
                "c" * 32, stale_plan["plan_revision"], decisions_for(stale_plan, backup=True)
            ),
            "stale_restore_plan",
        ), failures)
        stale_conn.close()

        undo_guard, undo_guard_conn, undo_guard_plan = prepare_target(
            base, archive, "undo-guard", "9" * 32
        )
        undo_guard_result = undo_guard.apply_staged_restore(
            "9" * 32,
            undo_guard_plan["plan_revision"],
            decisions_for(undo_guard_plan, backup=True),
        )
        undo_guard_operation = undo_guard_result["applied"]["restore_operation_id"]
        undo_guard_conn.execute("UPDATE bookmarks SET label='Changed after restore'")
        undo_guard_conn.commit()
        changed_preview = undo_guard.preview_restore_undo(undo_guard_operation)
        check(
            "backup_restore_postimage_change_blocks_undo",
            changed_preview.get("undo_available") is False
            and changed_preview.get("reason") == "ROW_CHANGED",
            failures,
        )
        undo_guard_conn.close()

        stale_undo, stale_undo_conn, stale_undo_plan = prepare_target(
            base, archive, "stale-undo", "8" * 32
        )
        stale_undo_result = stale_undo.apply_staged_restore(
            "8" * 32,
            stale_undo_plan["plan_revision"],
            decisions_for(stale_undo_plan, backup=True),
        )
        stale_undo_operation = stale_undo_result["applied"]["restore_operation_id"]
        refreshed_undo = stale_undo.preview_restore_undo(stale_undo_operation)
        check(
            "backup_restore_stale_undo_revision_rejected",
            refreshed_undo.get("undo_available") is True
            and expect_code(
                lambda: stale_undo.apply_restore_undo(
                    stale_undo_operation,
                    "undo_" + "0" * 24,
                ),
                "STALE_UNDO_REVISION",
            ),
            failures,
        )
        stale_undo_conn.close()

        archive_race, race_conn, race_plan = prepare_target(base, archive, "archive-race", "d" * 32)
        archive_race.staged_restore_path("d" * 32).write_bytes(b"changed archive")
        race_before = logical_dump(race_conn)
        archive_rejected = False
        try:
            archive_race.apply_staged_restore(
                "d" * 32, race_plan["plan_revision"], decisions_for(race_plan, backup=True)
            )
        except Exception:
            archive_rejected = True
        check("archive_change_after_preview_rejected", archive_rejected and logical_dump(race_conn) == race_before, failures)
        race_conn.close()

        protected = load_server(base / "source-conflict")
        protected_conn = seed_state(protected, base / "source-conflict", 700, reverse_insert_order=True)
        protected_path = Path(protected_conn.execute(
            "SELECT original_path FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()["original_path"])
        protected_path.write_bytes(b"SYNTHETIC-CHANGED-USER-OWNED-BYTES")
        protected_hash = hashlib.sha256(protected_path.read_bytes()).hexdigest()
        stage(protected, archive, "e" * 32)
        protected_plan = protected.preview_staged_restore("e" * 32)
        source_action = next(
            item for item in actions(protected_plan)
            if item["dataset"] == "local_sources" and item["classification"] == "CONFLICT"
        )
        check("source_content_conflict_keep_only", source_action.get("allowed_decisions") == ["KEEP_LOCAL"], failures)
        invalid = decisions_for(protected_plan, backup=True)
        next(item for item in invalid if item["dataset"] == "local_sources")["decision"] = "RESTORE_BACKUP"
        check("source_backup_override_rejected", expect_code(
            lambda: protected.apply_staged_restore("e" * 32, protected_plan["plan_revision"], invalid),
            "unsupported_restore_decision",
        ), failures)
        protected.apply_staged_restore(
            "e" * 32, protected_plan["plan_revision"], decisions_for(protected_plan, backup=True)
        )
        check("source_keep_preserves_user_bytes", hashlib.sha256(protected_path.read_bytes()).hexdigest() == protected_hash, failures)
        status, response = http_post(protected, "/api/backup/restore-apply", {
            "restore_id": "e" * 32,
            "expected_plan_revision": protected_plan["plan_revision"],
            "decisions": [],
            "path": str(protected_path),
        })
        check("browser_path_override_rejected", status == 400 and str(protected_path) not in json.dumps(response), failures)
        check(
            "source_conflict_creates_no_restore_operation",
            protected_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 0,
            failures,
        )
        protected_conn.close()

        mixed_server, mixed_conn, mixed_original, mixed_plan, mixed_request = prepare_mixed_restore(
            base, "closure-mixed"
        )
        mixed_source_hash = hashlib.sha256(mixed_original.read_bytes()).hexdigest()
        mixed_before = user_state(mixed_conn)
        mixed_result = mixed_server.apply_staged_restore(
            mixed_request["restore_id"],
            mixed_request["expected_plan_revision"],
            mixed_request["decisions"],
            mixed_request["relinks"],
        )
        mixed_applied = mixed_result["applied"]
        mixed_operation = mixed_applied["restore_operation_id"]
        check(
            "mixed_restore_contains_all_action_families",
            mixed_applied["created"] >= 1
            and mixed_applied["restored_from_backup"] >= 1
            and mixed_applied["kept_local"] >= 1
            and mixed_applied["skipped"] >= 1
            and mixed_applied["relinked"] == 1
            and mixed_applied["noop"] >= 1,
            failures,
        )
        check(
            "mixed_restore_records_one_operation",
            mixed_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 1,
            failures,
        )
        mixed_undo = mixed_server.preview_restore_undo(mixed_operation)
        mixed_server.apply_restore_undo(mixed_operation, mixed_undo["undo_revision"])
        check("mixed_restore_undo_restores_user_state", user_state(mixed_conn) == mixed_before, failures)
        check(
            "mixed_restore_undo_preserves_original",
            hashlib.sha256(mixed_original.read_bytes()).hexdigest() == mixed_source_hash,
            failures,
        )
        mixed_conn.close()

        mixed_fail, mixed_fail_conn, mixed_fail_original, _mixed_fail_plan, mixed_fail_request = prepare_mixed_restore(
            base, "closure-mixed-failure"
        )
        mixed_fail_hash = hashlib.sha256(mixed_fail_original.read_bytes()).hexdigest()
        mixed_fail_before = logical_dump(mixed_fail_conn)
        mixed_descriptor = mixed_fail.restore_relink_descriptor_path(
            mixed_fail_request["relinks"][0]["relink_id"]
        )
        check(
            "mixed_backup_create_relink_failure_rolls_back",
            expect_code(
                lambda: mixed_fail.apply_staged_restore(
                    mixed_fail_request["restore_id"],
                    mixed_fail_request["expected_plan_revision"],
                    mixed_fail_request["decisions"],
                    mixed_fail_request["relinks"],
                    failure_point="after_restore_backup",
                ),
                "synthetic_restore_failure_after_restore_backup",
            )
            and logical_dump(mixed_fail_conn) == mixed_fail_before
            and mixed_descriptor.exists()
            and hashlib.sha256(mixed_fail_original.read_bytes()).hexdigest() == mixed_fail_hash,
            failures,
        )
        mixed_fail_conn.close()

        frontend = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        catalog = (ROOT / "static" / "i18n" / "catalog.js").read_text(encoding="utf-8")
        check("ui_has_no_default_backup_choice", 'value="${escapeHtml(decision)}"' in frontend and 'decision === "RESTORE_BACKUP"' in frontend, failures)
        check("ui_exposes_latest_restore_and_undo", "/api/backup/restore-undo-latest" in frontend and "data-apply-restore-undo" in frontend, failures)
        check("english_and_chinese_recovery_copy", catalog.count('"restore.useBackupValue"') == 2 and "恢复备份中的值" in catalog, failures)

    if failures:
        print("Backup recovery closure acceptance: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Backup recovery closure acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
