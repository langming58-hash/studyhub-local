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
        "UPDATE courses SET code='LOC9200', course_code='LOC9200', "
        "name='Local Course', display_name='Local Course' "
        "WHERE stable_id='course_syn_alpha'"
    )
    conn.execute(
        "UPDATE weeks SET week_label='Revision Week', week_number=99, kind='week', archived=1 "
        "WHERE stable_id='unit_syn_alpha'"
    )
    conn.execute(
        "UPDATE files SET display_name='Local Material', category='Reading', "
        "exercise_type='Reading', course_code='LOC9200', course_name='Local Course', "
        "week_label='Revision Week', week_number=99, metadata_locked=1 "
        "WHERE stable_id='material_syn_local'"
    )
    conn.execute(
        "UPDATE materials SET course_code='LOC9200', course_name='Local Course', "
        "week_label='Revision Week', week_number=99 "
        "WHERE stable_id='material_syn_local'"
    )
    conn.execute(
        "UPDATE document_chunks SET course_code='LOC9200', week_label='Revision Week', "
        "category='Reading', exercise_type='Reading' "
        "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute(
        "UPDATE questions SET course_code='LOC9200', week_label='Revision Week', "
        "exercise_type='Reading' "
        "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute(
        "UPDATE solutions SET course_code='LOC9200', week_label='Revision Week', "
        "exercise_type='Reading' "
        "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute(
        "UPDATE files_fts SET course_code='LOC9200', week_label='Revision Week', category='Reading' "
        "WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
    )
    conn.execute(
        "UPDATE notes SET week_label='Revision Week', body='Local Note', "
        "updated_at='2026-05-01T00:00:00+00:00'"
    )
    conn.execute(
        "UPDATE wrong_questions SET week_label='Revision Week', "
        "mistake='Local Mistake', mastery='local'"
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
            "SELECT course_code, display_name, archived, active, sort_order, metadata_locked "
            "FROM courses WHERE stable_id='course_syn_alpha'"
        ).fetchone()),
        "unit": tuple(conn.execute(
            "SELECT week_label, week_number, kind, archived "
            "FROM weeks WHERE stable_id='unit_syn_alpha'"
        ).fetchone()),
        "file": tuple(conn.execute(
            "SELECT course_code, course_name, week_label, week_number, display_name, "
            "category, exercise_type, metadata_locked "
            "FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()),
        "material": tuple(conn.execute(
            "SELECT course_code, course_name, week_label, week_number, display_name, material_type "
            "FROM materials WHERE stable_id='material_syn_local'"
        ).fetchone()),
        "chunks": [tuple(row) for row in conn.execute(
            "SELECT course_code, week_label, category, exercise_type "
            "FROM document_chunks WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local') "
            "ORDER BY id"
        ).fetchall()],
        "questions": [tuple(row) for row in conn.execute(
            "SELECT course_code, week_label, exercise_type FROM questions "
            "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local') ORDER BY id"
        ).fetchall()],
        "solutions": [tuple(row) for row in conn.execute(
            "SELECT course_code, week_label, exercise_type FROM solutions "
            "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local') ORDER BY id"
        ).fetchall()],
        "notes": [tuple(row) for row in conn.execute(
            "SELECT week_label, body, created_at, updated_at FROM notes ORDER BY id"
        ).fetchall()],
        "wrong": [tuple(row) for row in conn.execute(
            "SELECT week_label, mistake, mastery, created_at FROM wrong_questions ORDER BY id"
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
        content_identity = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in ("sources", "blobs", "material_versions")
        }
        remote_authority = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in ("course_offerings", "canvas_file_sync_baselines")
        }
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
            "SELECT course_code, display_name FROM courses WHERE stable_id='course_syn_alpha'"
        ).fetchone()["course_code"] == "SYN9200" and conn.execute(
            "SELECT display_name FROM courses WHERE stable_id='course_syn_alpha'"
        ).fetchone()["display_name"] == "My Synthetic Course", failures)
        check("unit_backup_value_applied", conn.execute(
            "SELECT week_label, week_number, kind FROM weeks WHERE stable_id='unit_syn_alpha'"
        ).fetchone()["week_label"] == "Module Alpha" and conn.execute(
            "SELECT week_number FROM weeks WHERE stable_id='unit_syn_alpha'"
        ).fetchone()["week_number"] is None and conn.execute(
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
        check("course_and_unit_file_projection_synchronized", tuple(conn.execute(
            "SELECT course_code, course_name, week_label, week_number FROM files "
            "WHERE stable_id='material_syn_local'"
        ).fetchone()) == ("SYN9200", "My Synthetic Course", "Module Alpha", None), failures)
        check("course_and_unit_material_projection_synchronized", tuple(conn.execute(
            "SELECT course_code, course_name, week_label, week_number FROM materials "
            "WHERE stable_id='material_syn_local'"
        ).fetchone()) == ("SYN9200", "My Synthetic Course", "Module Alpha", None), failures)
        check("hierarchy_search_projection_consistent", tuple(conn.execute(
            "SELECT course_code, week_label, category FROM files_fts "
            "WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
        ).fetchone()) == ("SYN9200", "Module Alpha", "Lecture") and all(
            tuple(row) == ("SYN9200", "Module Alpha")
            for table in ("document_chunks", "questions", "solutions")
            for row in conn.execute(
                f"SELECT course_code, week_label FROM {table} "
                "WHERE file_id=(SELECT id FROM files WHERE stable_id='material_syn_local')"
            ).fetchall()
        ), failures)
        check("learning_unit_user_state_projection_synchronized", all(
            row["week_label"] == "Module Alpha"
            for table in ("notes", "wrong_questions")
            for row in conn.execute(f"SELECT week_label FROM {table}").fetchall()
        ), failures)
        operation_pk = conn.execute(
            "SELECT id FROM restore_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()["id"]
        changed_kinds = {
            row["entity_kind"]
            for row in conn.execute(
                "SELECT entity_kind FROM restore_operation_changes WHERE restore_operation_id=?",
                (operation_pk,),
            ).fetchall()
        }
        check("hierarchy_changeset_covers_durable_graph", {
            "course", "learning_unit", "file", "material", "note", "wrong_question"
        }.issubset(changed_kinds), failures)
        check("content_identity_unchanged", content_identity == {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in ("sources", "blobs", "material_versions")
        }, failures)
        check("remote_authority_unchanged", remote_authority == {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in ("course_offerings", "canvas_file_sync_baselines")
        }, failures)
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
        check("undo_restores_hierarchy_derived_projection", tuple(conn.execute(
            "SELECT course_code, week_label, category FROM files_fts "
            "WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
        ).fetchone()) == ("LOC9200", "Revision Week", "Reading"), failures)
        check("undo_preserves_original_file", hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash, failures)
        check("second_undo_rejected", expect_code(
            lambda: restarted.apply_restore_undo(operation_id, restarted_undo["undo_revision"]),
            "ALREADY_UNDONE",
        ), failures)
        conn.close()

        hierarchy_only, hierarchy_conn, hierarchy_plan = prepare_target(
            base, archive, "hierarchy-only", "f" * 32
        )
        hierarchy_before = user_state(hierarchy_conn)
        hierarchy_decisions = decisions_for(hierarchy_plan, backup=True)
        for decision in hierarchy_decisions:
            if decision["dataset"] in {"material_overlays", "notes", "study_records"}:
                decision["decision"] = "KEEP_LOCAL"
        hierarchy_result = hierarchy_only.apply_staged_restore(
            "f" * 32,
            hierarchy_plan["plan_revision"],
            hierarchy_decisions,
        )
        hierarchy_operation = hierarchy_result["applied"]["restore_operation_id"]
        check(
            "hierarchy_propagates_with_dependent_conflicts_kept_local",
            tuple(hierarchy_conn.execute(
                "SELECT course_code, course_name, week_label, week_number, display_name, category "
                "FROM files WHERE stable_id='material_syn_local'"
            ).fetchone())
            == ("SYN9200", "My Synthetic Course", "Module Alpha", None, "Local Material", "Reading")
            and all(
                row["week_label"] == "Module Alpha" and row["body"] == "Local Note"
                for row in hierarchy_conn.execute("SELECT week_label, body FROM notes").fetchall()
            )
            and all(
                row["week_label"] == "Module Alpha" and row["mistake"] == "Local Mistake"
                for row in hierarchy_conn.execute(
                    "SELECT week_label, mistake FROM wrong_questions"
                ).fetchall()
            ),
            failures,
        )
        hierarchy_undo = hierarchy_only.preview_restore_undo(hierarchy_operation)
        hierarchy_conn.close()
        hierarchy_only.apply_restore_undo(
            hierarchy_operation,
            hierarchy_undo["undo_revision"],
        )
        hierarchy_conn = hierarchy_only.connect_db()
        check(
            "hierarchy_only_undo_restores_exact_pre_restore_state",
            user_state(hierarchy_conn) == hierarchy_before,
            failures,
        )
        hierarchy_conn.close()

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

        later_work, later_conn, later_plan = prepare_target(
            base, archive, "later-dependent-work", "7" * 32
        )
        later_result = later_work.apply_staged_restore(
            "7" * 32,
            later_plan["plan_revision"],
            decisions_for(later_plan, backup=True),
        )
        later_operation = later_result["applied"]["restore_operation_id"]
        later_ready = later_work.preview_restore_undo(later_operation)
        local_file = later_conn.execute(
            "SELECT id, course_id FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()
        later_conn.execute(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) "
            "VALUES ('file', ?, ?, 'Module Alpha', 'Later ordinary user work', ?, ?)",
            (local_file["id"], local_file["course_id"], "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"),
        )
        later_conn.commit()
        later_before = logical_dump(later_conn)
        later_blocked = later_work.preview_restore_undo(later_operation)
        check(
            "later_hierarchy_work_blocks_undo_without_partial_rollback",
            later_ready.get("undo_available") is True
            and later_blocked.get("reason") == "DEPENDENCY_CHANGED"
            and expect_code(
                lambda: later_work.apply_restore_undo(
                    later_operation,
                    later_ready["undo_revision"],
                ),
                "DEPENDENCY_CHANGED",
            )
            and logical_dump(later_conn) == later_before,
            failures,
        )
        later_conn.close()

        cross_course = load_server(base / "cross-course-unit")
        cross_conn = seed_state(cross_course, base / "cross-course-unit", 1_100, reverse_insert_order=True)
        seed_search_metadata(cross_conn)
        term_id = int(cross_conn.execute(
            "SELECT id FROM terms WHERE stable_id='term_syn_alpha'"
        ).fetchone()["id"])
        cross_conn.execute(
            """
            INSERT INTO courses(stable_id, code, course_code, name, display_name,
              folder_name, path, source_folder, source_kind, term_id, archived, active,
              sort_order, metadata_locked, created_at, updated_at)
            VALUES ('course_syn_other', 'OTHER1000', 'OTHER1000', 'Other Course',
              'Other Course', 'other-course', '', '', 'managed', ?, 0, 1, 0, 1, ?, ?)
            """,
            (term_id, "2026-05-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00"),
        )
        other_course_id = int(cross_conn.execute(
            "SELECT id FROM courses WHERE stable_id='course_syn_other'"
        ).fetchone()["id"])
        cross_conn.execute(
            "UPDATE weeks SET course_id=? WHERE stable_id='unit_syn_alpha'",
            (other_course_id,),
        )
        cross_conn.commit()
        stage(cross_course, archive, "6" * 32)
        cross_plan = cross_course.preview_staged_restore("6" * 32)
        unit_action = next(
            item for item in actions(cross_plan)
            if item["dataset"] == "learning_units" and item["stable_id"] == "unit_syn_alpha"
        )
        cross_before = logical_dump(cross_conn)
        invalid_cross = decisions_for(cross_plan, backup=True)
        next(
            item for item in invalid_cross
            if item["dataset"] == "learning_units" and item["stable_id"] == "unit_syn_alpha"
        )["decision"] = "RESTORE_BACKUP"
        check(
            "cross_course_learning_unit_is_keep_local_only",
            unit_action.get("allowed_decisions") == ["KEEP_LOCAL"]
            and unit_action.get("reason") == "cross_course_move_requires_keep_local",
            failures,
        )
        check(
            "cross_course_learning_unit_backup_override_rejected",
            expect_code(
                lambda: cross_course.apply_staged_restore(
                    "6" * 32,
                    cross_plan["plan_revision"],
                    invalid_cross,
                ),
                "unsupported_restore_decision",
            )
            and logical_dump(cross_conn) == cross_before
            and cross_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 0,
            failures,
        )
        cross_conn.close()

        remote_overlay = load_server(base / "remote-overlay")
        remote_conn = seed_state(remote_overlay, base / "remote-overlay", 1_300, reverse_insert_order=True)
        remote_conn.execute(
            "UPDATE courses SET name='Local Canvas Label', display_name='Local Canvas Label', "
            "metadata_locked=1 WHERE stable_id='course_syn_remote'"
        )
        remote_conn.commit()
        authority_before = [
            tuple(row) for row in remote_conn.execute("SELECT * FROM course_offerings ORDER BY id").fetchall()
        ]
        stage(remote_overlay, archive, "5" * 32)
        remote_plan = remote_overlay.preview_staged_restore("5" * 32)
        remote_overlay.apply_staged_restore(
            "5" * 32,
            remote_plan["plan_revision"],
            decisions_for(remote_plan, backup=True),
        )
        check(
            "remote_overlay_restore_preserves_authoritative_canvas_facts",
            authority_before == [
                tuple(row) for row in remote_conn.execute("SELECT * FROM course_offerings ORDER BY id").fetchall()
            ],
            failures,
        )
        remote_conn.close()

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
        mixed_conn.close()
        mixed_result = mixed_server.apply_staged_restore(
            mixed_request["restore_id"],
            mixed_request["expected_plan_revision"],
            mixed_request["decisions"],
            mixed_request["relinks"],
        )
        mixed_applied = mixed_result["applied"]
        mixed_operation = mixed_applied["restore_operation_id"]
        mixed_conn = mixed_server.connect_db()
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
        mixed_conn.close()
        mixed_undo = mixed_server.preview_restore_undo(mixed_operation)
        mixed_server.apply_restore_undo(mixed_operation, mixed_undo["undo_revision"])
        mixed_conn = mixed_server.connect_db()
        check("mixed_restore_undo_restores_user_state", user_state(mixed_conn) == mixed_before, failures)
        mixed_file_projection = mixed_conn.execute(
            "SELECT course_code, week_label, category FROM files "
            "WHERE stable_id='material_syn_local'"
        ).fetchone()
        mixed_fts_projection = mixed_conn.execute(
            "SELECT course_code, week_label, category FROM files_fts "
            "WHERE rowid=(SELECT id FROM files WHERE stable_id='material_syn_local')"
        ).fetchone()
        check(
            "mixed_restore_undo_derived_projection_consistent",
            mixed_fts_projection is None
            or tuple(mixed_fts_projection) == tuple(mixed_file_projection),
            failures,
        )
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
        mixed_fail_conn.close()
        mixed_descriptor = mixed_fail.restore_relink_descriptor_path(
            mixed_fail_request["relinks"][0]["relink_id"]
        )
        mixed_failed = expect_code(
            lambda: mixed_fail.apply_staged_restore(
                mixed_fail_request["restore_id"],
                mixed_fail_request["expected_plan_revision"],
                mixed_fail_request["decisions"],
                mixed_fail_request["relinks"],
                failure_point="after_restore_backup",
            ),
            "synthetic_restore_failure_after_restore_backup",
        )
        mixed_fail_check = mixed_fail.connect_db()
        check(
            "mixed_backup_create_relink_failure_rolls_back",
            mixed_failed
            and logical_dump(mixed_fail_check) == mixed_fail_before
            and mixed_descriptor.exists()
            and hashlib.sha256(mixed_fail_original.read_bytes()).hexdigest() == mixed_fail_hash,
            failures,
        )
        mixed_fail_check.close()

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
