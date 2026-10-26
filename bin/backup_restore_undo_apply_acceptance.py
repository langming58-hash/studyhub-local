#!/usr/bin/env python3
"""Synthetic acceptance for atomic post-commit Restore Undo Apply."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import load_server, seed_state  # noqa: E402
from backup_restore_apply_acceptance import (  # noqa: E402
    consistency_archive,
    decisions_for,
    export_archive,
    prepare_target,
    seed_search_metadata,
)
from backup_restore_relink_apply_acceptance import fixture as relink_fixture  # noqa: E402
from backup_restore_preview_acceptance import export_fixture, mutate_dataset, stage  # noqa: E402
from backup_restore_undo_preview_acceptance import (  # noqa: E402
    http_request,
    logical_dump,
    operation_rows,
    tree_digest,
)


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def expect_code(operation: Callable[[], Any], *codes: str) -> bool:
    try:
        operation()
    except Exception as exc:
        return str(exc) in codes
    return False


def reversible_state(target: Any, conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    state: dict[str, list[dict[str, Any]]] = {}
    for spec in target.RESTORE_REVERSIBLE_ENTITY_SPECS:
        rows = conn.execute(
            f"SELECT {', '.join(spec.columns)} FROM {spec.table} ORDER BY {spec.primary_key}"
        ).fetchall()
        state[spec.entity_kind] = [
            {column: row[column] for column in spec.columns} for row in rows
        ]
    return state


def set_row_image(
    target: Any,
    conn: sqlite3.Connection,
    entity_kind: str,
    image: dict[str, Any],
) -> None:
    spec = target.restore_entity_spec(entity_kind)
    columns = tuple(column for column in spec.columns if column != spec.primary_key)
    conn.execute(
        f"UPDATE {spec.table} SET "
        + ", ".join(f"{column}=?" for column in columns)
        + f" WHERE {spec.primary_key}=?",
        tuple(image[column] for column in columns) + (image[spec.primary_key],),
    )
    conn.commit()


def applied_fixture(
    base: Path,
    suffix: str,
    *,
    seed_target: bool = True,
) -> tuple[Any, sqlite3.Connection, Path, dict[str, Any], str, dict[str, Any]]:
    target, conn, _restore_id, _source_id, selected, request = relink_fixture(
        base, suffix, seed_target=seed_target
    )
    pre_restore = reversible_state(target, conn)
    result = target.apply_staged_restore(
        request["restore_id"],
        request["expected_plan_revision"],
        request["decisions"],
        request["relinks"],
    )
    operation_id = result["applied"]["restore_operation_id"]
    preview = target.preview_restore_undo(operation_id)
    assert preview.get("undo_available") is True
    return target, conn, selected, pre_restore, operation_id, preview


def mixed_applied_fixture(
    base: Path,
    suffix: str,
) -> tuple[Any, sqlite3.Connection, str, dict[str, Any]]:
    source = load_server(base / f"mixed-source-{suffix}")
    source_conn = seed_state(source, base / f"mixed-source-{suffix}", 100)
    archive = export_archive(source, source_conn)
    consistency = consistency_archive(source, archive, base / f"mixed-{suffix}.zip")
    target, conn, _staged, plan = prepare_target(
        base, consistency, f"mixed-{suffix}", hashlib.sha256(suffix.encode()).hexdigest()[:32]
    )
    seed_search_metadata(conn)
    conn.execute("UPDATE files SET metadata_locked=0 WHERE stable_id='material_syn_local'")
    conn.commit()
    restore_id = hashlib.sha256(suffix.encode()).hexdigest()[:32]
    plan = target.preview_staged_restore(restore_id)
    result = target.apply_staged_restore(
        restore_id, plan["plan_revision"], decisions_for(plan)
    )
    operation_id = result["applied"]["restore_operation_id"]
    preview = target.preview_restore_undo(operation_id)
    source_conn.close()
    return target, conn, operation_id, preview


def overlapping_restore_fixture(
    base: Path,
    suffix: str,
) -> tuple[Any, sqlite3.Connection, str, dict[str, Any], str, Path, bytes]:
    source = load_server(base / f"overlap-source-{suffix}")
    source_conn = seed_state(source, base / f"overlap-source-{suffix}", 100)
    archive = export_archive(source, source_conn)
    consistency = consistency_archive(source, archive, base / f"overlap-{suffix}.zip")
    restore_a = hashlib.sha256(f"{suffix}-a".encode()).hexdigest()[:32]
    target, conn, _staged, _plan = prepare_target(
        base, consistency, f"overlap-{suffix}", restore_a
    )
    seed_search_metadata(conn)
    conn.execute(
        "UPDATE files SET display_name='L', metadata_locked=0 "
        "WHERE stable_id='material_syn_local'"
    )
    conn.execute(
        "UPDATE materials SET display_name='L' WHERE stable_id='material_syn_local'"
    )
    conn.commit()
    plan_a = target.preview_staged_restore(restore_a)
    applied_a = target.apply_staged_restore(
        restore_a, plan_a["plan_revision"], decisions_for(plan_a)
    )
    operation_a = applied_a["applied"]["restore_operation_id"]
    preview_a = target.preview_restore_undo(operation_a)
    conn.execute(
        "UPDATE files SET display_name='Y', metadata_locked=0 "
        "WHERE stable_id='material_syn_local'"
    )
    conn.execute(
        "UPDATE materials SET display_name='Y' WHERE stable_id='material_syn_local'"
    )
    conn.commit()
    restore_b = hashlib.sha256(f"{suffix}-b".encode()).hexdigest()[:32]
    stage(target, consistency, restore_b)
    plan_b = target.preview_staged_restore(restore_b)
    applied_b = target.apply_staged_restore(
        restore_b, plan_b["plan_revision"], decisions_for(plan_b)
    )
    operation_b = applied_b["applied"]["restore_operation_id"]
    original_path = Path(
        conn.execute(
            "SELECT original_path FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()["original_path"]
    )
    original_bytes = original_path.read_bytes()
    source_conn.close()
    return target, conn, operation_a, preview_a, operation_b, original_path, original_bytes


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-undo-apply-") as temporary:
        base = Path(temporary)

        target, conn, selected, pre_restore, operation_id, preview = applied_fixture(
            base, "basic"
        )
        original = selected.read_bytes()
        operation, changes = operation_rows(conn, operation_id)
        controlled_cache_ids = [
            json.loads(change["after_json"])["relative_cache_id"]
            for change in changes
            if change["change_kind"] == "CACHE_CREATE"
        ]
        result = target.apply_restore_undo(operation_id, preview["undo_revision"])
        operation_after, changes_after = operation_rows(conn, operation_id)
        check(
            "basic_exact_inverse_restores_user_owned_state",
            reversible_state(target, conn) == pre_restore,
            failures,
        )
        check(
            "operation_transitions_atomically_to_undone",
            result == {
                "ok": True,
                "operation_id": operation_id,
                "status": "UNDONE",
                "undo_applied": True,
            }
            and operation_after["status"] == "UNDONE"
            and operation_after["undone_at"]
            and [dict(row) for row in changes_after] == [dict(row) for row in changes],
            failures,
        )
        check(
            "existing_target_original_is_untouched",
            selected.read_bytes() == original,
            failures,
        )
        check(
            "controlled_cache_cleanup_occurs_only_after_success",
            bool(controlled_cache_ids)
            and all(not (target.TEXT_CACHE_DIR / cache_id).exists() for cache_id in controlled_cache_ids)
            and not any(cache_id in json.dumps(result) for cache_id in controlled_cache_ids),
            failures,
        )
        check(
            "second_undo_is_rejected",
            target.preview_restore_undo(operation_id).get("reason") == "ALREADY_UNDONE"
            and expect_code(
                lambda: target.apply_restore_undo(operation_id, preview["undo_revision"]),
                "ALREADY_UNDONE",
            ),
            failures,
        )
        reloaded = load_server(base / "target-basic")
        check(
            "undone_state_survives_restart",
            reloaded.latest_restore_undo_operation().get("operation", {}).get("status") == "UNDONE"
            and reloaded.preview_restore_undo(operation_id).get("reason") == "ALREADY_UNDONE",
            failures,
        )

        empty, empty_conn, empty_selected, empty_pre, empty_operation_id, empty_preview = applied_fixture(
            base, "empty-target", seed_target=False
        )
        empty_operation, empty_changes = operation_rows(empty_conn, empty_operation_id)
        empty_kinds = {
            change["entity_kind"]
            for change in empty_changes
            if change["change_kind"] == "INSERT"
        }
        empty_bytes = empty_selected.read_bytes()
        empty.apply_restore_undo(empty_operation_id, empty_preview["undo_revision"])
        check(
            "empty_target_full_canonical_graph_inverse",
            {
                "term", "course", "learning_unit", "file", "legacy_file_version",
                "blob", "source", "material", "material_version",
            }.issubset(empty_kinds)
            and reversible_state(empty, empty_conn) == empty_pre
            and empty_operation["insert_count"] >= 9,
            failures,
        )
        check(
            "operation_owned_dependencies_delete_child_first",
            empty_conn.execute("PRAGMA foreign_key_check").fetchall() == [],
            failures,
        )
        check(
            "empty_target_original_is_untouched",
            empty_selected.read_bytes() == empty_bytes,
            failures,
        )

        history, history_conn, operation_a, preview_a, operation_b, history_original, history_bytes = overlapping_restore_fixture(
            base, "cross-operation"
        )
        row_a, changes_a = operation_rows(history_conn, operation_a)
        row_b, changes_b = operation_rows(history_conn, operation_b)
        history_cache_before = tree_digest(history.CACHE_DIR)
        blocked_preview = history.preview_restore_undo(operation_a)
        check(
            "later_applied_restore_overlap_blocks_older_preview",
            row_b["id"] > row_a["id"]
            and blocked_preview.get("undo_available") is False
            and blocked_preview.get("reason") == "LATER_RESTORE_OVERLAP",
            failures,
        )
        original_b_digest = row_b["changeset_digest"]
        history_conn.execute(
            "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
            ("0" * 64, row_b["id"]),
        )
        history_conn.commit()
        corrupt_later = history.preview_restore_undo(operation_a)
        history_conn.execute(
            "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
            (original_b_digest, row_b["id"]),
        )
        history_conn.commit()
        check(
            "corrupt_relevant_later_applied_journal_fails_closed",
            corrupt_later.get("reason") == "LATER_RESTORE_OVERLAP",
            failures,
        )
        restored_history_before = logical_dump(history_conn)
        rejected = expect_code(
            lambda: history.apply_restore_undo(operation_a, preview_a["undo_revision"]),
            "LATER_RESTORE_OVERLAP",
        )
        row_a_after_reject, changes_a_after_reject = operation_rows(history_conn, operation_a)
        row_b_after_reject, changes_b_after_reject = operation_rows(history_conn, operation_b)
        check(
            "older_undo_overlap_rejection_is_zero_mutation",
            rejected
            and logical_dump(history_conn) == restored_history_before
            and row_a_after_reject["status"] == "APPLIED"
            and row_b_after_reject["status"] == "APPLIED"
            and [dict(row) for row in changes_a_after_reject] == [dict(row) for row in changes_a]
            and [dict(row) for row in changes_b_after_reject] == [dict(row) for row in changes_b]
            and history_original.read_bytes() == history_bytes
            and tree_digest(history.CACHE_DIR) == history_cache_before,
            failures,
        )
        preview_b = history.preview_restore_undo(operation_b)
        history.apply_restore_undo(operation_b, preview_b["undo_revision"])
        a_after_b_undo = history.preview_restore_undo(operation_a)
        for change in changes_a:
            if change["change_kind"] == "UPDATE":
                set_row_image(
                    history,
                    history_conn,
                    change["entity_kind"],
                    json.loads(change["after_json"]),
                )
        a_recovered = history.preview_restore_undo(operation_a)
        history.apply_restore_undo(operation_a, a_recovered["undo_revision"])
        final_a, _final_a_changes = operation_rows(history_conn, operation_a)
        final_b, _final_b_changes = operation_rows(history_conn, operation_b)
        a_updates_restored = all(
            history.current_restore_undo_row(
                history_conn,
                history.validate_restore_undo_change(change, change["sequence"]),
            )
            == json.loads(change["before_json"])
            for change in changes_a
            if change["change_kind"] == "UPDATE"
        )
        check(
            "safe_newest_first_unwind_sequence",
            preview_b.get("undo_available") is True
            and a_after_b_undo.get("reason") == "ROW_CHANGED"
            and a_recovered.get("undo_available") is True
            and final_a["status"] == "UNDONE"
            and final_b["status"] == "UNDONE"
            and a_updates_restored
            and history_original.read_bytes() == history_bytes,
            failures,
        )
        check(
            "later_undone_overlap_does_not_block_older_operation",
            a_recovered.get("undo_available") is True,
            failures,
        )

        unrelated_history, unrelated_history_conn, _restore_id, _source_id, unrelated_original, unrelated_request = relink_fixture(
            base, "unrelated-later-operation"
        )
        applied_history_a = unrelated_history.apply_staged_restore(
            unrelated_request["restore_id"],
            unrelated_request["expected_plan_revision"],
            unrelated_request["decisions"],
            unrelated_request["relinks"],
        )
        unrelated_original_bytes = unrelated_original.read_bytes()
        unrelated_operation_a = applied_history_a["applied"]["restore_operation_id"]
        unrelated_preview_a = unrelated_history.preview_restore_undo(unrelated_operation_a)
        exported = export_fixture(
            unrelated_history,
            unrelated_history_conn,
            "2026-06-01T00:00:00+00:00",
        )

        def append_unrelated_note(payload: dict[str, Any]) -> None:
            payload["records"].append(
                {
                    "body": "Unrelated later Restore note",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "stable_id": "note_unrelated_later_restore",
                    "target_ref": "material_syn_local",
                    "target_type": "material",
                    "updated_at": "2026-06-01T00:00:00+00:00",
                }
            )

        unrelated_archive = mutate_dataset(
            unrelated_history, exported, "data/notes.json", append_unrelated_note
        )
        unrelated_restore_c = hashlib.sha256(b"unrelated-restore-c").hexdigest()[:32]
        stage(unrelated_history, unrelated_archive, unrelated_restore_c)
        unrelated_plan_c = unrelated_history.preview_staged_restore(unrelated_restore_c)
        applied_history_c = unrelated_history.apply_staged_restore(
            unrelated_restore_c,
            unrelated_plan_c["plan_revision"],
            decisions_for(unrelated_plan_c),
        )
        unrelated_operation_c = applied_history_c["applied"]["restore_operation_id"]
        row_c, changes_c = operation_rows(unrelated_history_conn, unrelated_operation_c)
        note_c = json.loads(
            next(change["after_json"] for change in changes_c if change["entity_kind"] == "note")
        )
        unrelated_after_c = unrelated_history.preview_restore_undo(unrelated_operation_a)
        unrelated_history.apply_restore_undo(
            unrelated_operation_a, unrelated_preview_a["undo_revision"]
        )
        row_c_after, changes_c_after = operation_rows(
            unrelated_history_conn, unrelated_operation_c
        )
        check(
            "later_unrelated_restore_does_not_change_revision_or_block",
            unrelated_after_c.get("undo_available") is True
            and unrelated_after_c.get("undo_revision") == unrelated_preview_a["undo_revision"]
            and row_c["status"] == "APPLIED"
            and row_c_after["status"] == "APPLIED"
            and [dict(row) for row in changes_c_after] == [dict(row) for row in changes_c]
            and unrelated_history_conn.execute(
                "SELECT body FROM notes WHERE id=?", (note_c["id"],)
            ).fetchone()["body"] == "Unrelated later Restore note"
            and unrelated_original.read_bytes() == unrelated_original_bytes,
            failures,
        )

        dep, dep_conn, dep_selected, _dep_pre, dep_operation_id, dep_preview = applied_fixture(
            base, "dependency", seed_target=False
        )
        dep_operation, dep_changes = operation_rows(dep_conn, dep_operation_id)
        dep_material = json.loads(
            next(row["after_json"] for row in dep_changes if row["entity_kind"] == "material")
        )
        dep_source = json.loads(
            next(row["after_json"] for row in dep_changes if row["entity_kind"] == "source")
        )
        external_sha = "3" * 64
        dep_conn.execute(
            "INSERT INTO blobs(stable_id, sha256, byte_size, mime_type, extension, local_locator, created_at, updated_at) "
            "VALUES ('blob_external_undo_apply', ?, 1, 'text/plain', '.txt', '', '', '')",
            (external_sha,),
        )
        external_blob_id = int(dep_conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        dep_conn.execute(
            "INSERT INTO material_versions(stable_id, material_id, source_id, blob_id, sha256, byte_size, active, source_missing, created_at, updated_at) "
            "VALUES ('matver_external_undo_apply', ?, ?, ?, ?, 1, 0, 0, '', '')",
            (dep_material["id"], dep_source["id"], external_blob_id, external_sha),
        )
        external_version_id = int(dep_conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        dep_conn.commit()
        dep_blocked_state = logical_dump(dep_conn)
        check(
            "external_durable_dependency_rejected_zero_mutation",
            expect_code(
                lambda: dep.apply_restore_undo(dep_operation_id, dep_preview["undo_revision"]),
                "DEPENDENCY_CHANGED",
            )
            and logical_dump(dep_conn) == dep_blocked_state,
            failures,
        )
        dep_conn.execute("DELETE FROM material_versions WHERE id=?", (external_version_id,))
        dep_conn.execute("DELETE FROM blobs WHERE id=?", (external_blob_id,))
        dep_conn.commit()
        dep_recovered = dep.preview_restore_undo(dep_operation_id)
        dep.apply_restore_undo(dep_operation_id, dep_recovered["undo_revision"])
        check(
            "dependency_removal_allows_fresh_preview_and_undo",
            dep_recovered.get("undo_available") is True
            and dep_selected.read_bytes() == original,
            failures,
        )
        del dep_operation

        overlap, overlap_conn, overlap_selected, _overlap_pre, overlap_operation_id, overlap_preview = applied_fixture(
            base, "overlap"
        )
        _overlap_operation, overlap_changes = operation_rows(overlap_conn, overlap_operation_id)
        overlap_change = next(change for change in overlap_changes if change["change_kind"] == "UPDATE")
        overlap_after = json.loads(overlap_change["after_json"])
        overlap_spec = overlap.restore_entity_spec(overlap_change["entity_kind"])
        mutable = next(
            column
            for column in overlap_spec.columns
            if column != overlap_spec.primary_key and isinstance(overlap_after[column], str)
        )
        overlap_conn.execute(
            f"UPDATE {overlap_spec.table} SET {mutable}=? WHERE {overlap_spec.primary_key}=?",
            ("synthetic-overlap", overlap_after[overlap_spec.primary_key]),
        )
        overlap_conn.commit()
        overlap_state = logical_dump(overlap_conn)
        check(
            "affected_row_overlap_rejected_zero_mutation",
            expect_code(
                lambda: overlap.apply_restore_undo(
                    overlap_operation_id, overlap_preview["undo_revision"]
                ),
                "ROW_CHANGED",
            )
            and logical_dump(overlap_conn) == overlap_state
            and overlap_selected.read_bytes() == original,
            failures,
        )
        set_row_image(overlap, overlap_conn, overlap_change["entity_kind"], overlap_after)
        overlap_ready = overlap.preview_restore_undo(overlap_operation_id)
        overlap.apply_restore_undo(overlap_operation_id, overlap_ready["undo_revision"])
        check("exact_postimage_recovery_allows_undo", overlap_ready["undo_available"], failures)

        unrelated, unrelated_conn, _unrelated_selected, _unrelated_pre, unrelated_operation_id, unrelated_preview = applied_fixture(
            base, "unrelated"
        )
        unrelated_conn.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES ('synthetic-unrelated', 'preserve', '2026-01-01T00:00:00+00:00')"
        )
        unrelated_conn.commit()
        same_revision = unrelated.preview_restore_undo(unrelated_operation_id)
        unrelated.apply_restore_undo(unrelated_operation_id, unrelated_preview["undo_revision"])
        check(
            "unrelated_durable_change_preserved",
            same_revision.get("undo_revision") == unrelated_preview["undo_revision"]
            and unrelated_conn.execute(
                "SELECT value FROM app_settings WHERE key='synthetic-unrelated'"
            ).fetchone()["value"] == "preserve",
            failures,
        )

        stale, stale_conn, stale_selected, _stale_pre, stale_operation_id, stale_preview = applied_fixture(
            base, "stale"
        )
        stale_before = logical_dump(stale_conn)
        check(
            "stale_undo_revision_rejected",
            expect_code(
                lambda: stale.apply_restore_undo(
                    stale_operation_id, "undo_" + "0" * 24
                ),
                "STALE_UNDO_REVISION",
            )
            and logical_dump(stale_conn) == stale_before
            and stale_selected.read_bytes() == original,
            failures,
        )
        del stale_preview

        concurrency, concurrency_conn, _concurrency_selected, _concurrency_pre, concurrency_operation_id, concurrency_preview = applied_fixture(
            base, "concurrency"
        )
        second_writer = concurrency.connect_db()
        second_writer.execute(
            "UPDATE files SET display_name='concurrent-writer' WHERE stable_id='material_syn_local'"
        )
        second_writer.commit()
        second_writer.close()
        concurrent_state = logical_dump(concurrency_conn)
        check(
            "concurrent_affected_writer_is_revalidated_inside_transaction",
            expect_code(
                lambda: concurrency.apply_restore_undo(
                    concurrency_operation_id, concurrency_preview["undo_revision"]
                ),
                "ROW_CHANGED",
            )
            and logical_dump(concurrency_conn) == concurrent_state,
            failures,
        )

        mixed, mixed_conn, mixed_operation_id, mixed_preview = mixed_applied_fixture(
            base, "mixed-phase-boundary"
        )
        mixed_operation, _mixed_changes = operation_rows(mixed_conn, mixed_operation_id)
        mixed_before = logical_dump(mixed_conn)
        check(
            "mixed_update_insert_phase_boundary_rolls_back",
            mixed_operation["update_count"] > 0
            and mixed_operation["insert_count"] > 0
            and expect_code(
                lambda: mixed.apply_restore_undo(
                    mixed_operation_id,
                    mixed_preview["undo_revision"],
                    failure_point="after_update_phase",
                ),
                "synthetic_restore_undo_failure_after_update_phase",
            )
            and logical_dump(mixed_conn) == mixed_before
            and mixed.preview_restore_undo(mixed_operation_id).get("undo_revision")
            == mixed_preview["undo_revision"],
            failures,
        )

        failure_points = (
            ("after_first_update_inverse", True),
            ("after_update_phase", True),
            ("after_first_insert_delete", False),
            ("after_durable_inverse", False),
            ("after_derived_rebuild", True),
            ("before_status_transition", True),
            ("before_commit", True),
        )
        rollback_ok = True
        for point, seed_target in failure_points:
            fail_target, fail_conn, fail_selected, _fail_pre, fail_operation_id, fail_preview = applied_fixture(
                base, f"failure-{point}", seed_target=seed_target
            )
            fail_before = logical_dump(fail_conn)
            fail_changes = [dict(row) for row in operation_rows(fail_conn, fail_operation_id)[1]]
            fail_cache = tree_digest(fail_target.CACHE_DIR)
            fail_original = fail_selected.read_bytes()
            failed = expect_code(
                lambda point=point: fail_target.apply_restore_undo(
                    fail_operation_id,
                    fail_preview["undo_revision"],
                    failure_point=point,
                ),
                f"synthetic_restore_undo_failure_{point}",
            )
            fail_operation, fail_change_rows = operation_rows(fail_conn, fail_operation_id)
            recovered = fail_target.preview_restore_undo(fail_operation_id)
            rollback_ok = rollback_ok and all(
                (
                    failed,
                    logical_dump(fail_conn) == fail_before,
                    fail_operation["status"] == "APPLIED",
                    [dict(row) for row in fail_change_rows] == fail_changes,
                    fail_selected.read_bytes() == fail_original,
                    tree_digest(fail_target.CACHE_DIR) == fail_cache,
                    recovered.get("undo_revision") == fail_preview["undo_revision"],
                )
            )
            fail_conn.close()
        check("all_failure_injections_roll_back_completely", rollback_ok, failures)

        corrupt, corrupt_conn, _corrupt_selected, _corrupt_pre, corrupt_operation_id, corrupt_preview = applied_fixture(
            base, "corruption"
        )
        corrupt_operation, corrupt_changes = operation_rows(corrupt_conn, corrupt_operation_id)
        first_change = corrupt_changes[0]
        corruption_cases = (
            (
                "digest",
                "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
                ("0" * 64, corrupt_operation["id"]),
                "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
                (corrupt_operation["changeset_digest"], corrupt_operation["id"]),
                "CHANGESET_INVALID",
            ),
            (
                "sequence",
                "UPDATE restore_operation_changes SET sequence=999 WHERE id=?",
                (first_change["id"],),
                "UPDATE restore_operation_changes SET sequence=? WHERE id=?",
                (first_change["sequence"], first_change["id"]),
                "CHANGESET_INVALID",
            ),
            (
                "token",
                "UPDATE restore_operation_changes SET after_token=? WHERE id=?",
                ("0" * 64, first_change["id"]),
                "UPDATE restore_operation_changes SET after_token=? WHERE id=?",
                (first_change["after_token"], first_change["id"]),
                "CHANGESET_INVALID",
            ),
            (
                "json",
                "UPDATE restore_operation_changes SET after_json='{' WHERE id=?",
                (first_change["id"],),
                "UPDATE restore_operation_changes SET after_json=? WHERE id=?",
                (first_change["after_json"], first_change["id"]),
                "CHANGESET_INVALID",
            ),
            (
                "format",
                "UPDATE restore_operations SET format_version=999 WHERE id=?",
                (corrupt_operation["id"],),
                "UPDATE restore_operations SET format_version=? WHERE id=?",
                (corrupt_operation["format_version"], corrupt_operation["id"]),
                "UNSUPPORTED_CHANGESET",
            ),
            (
                "schema",
                "UPDATE restore_operations SET schema_fingerprint='unsupported' WHERE id=?",
                (corrupt_operation["id"],),
                "UPDATE restore_operations SET schema_fingerprint=? WHERE id=?",
                (corrupt_operation["schema_fingerprint"], corrupt_operation["id"]),
                "UNSUPPORTED_CHANGESET",
            ),
        )
        corruption_ok = True
        for _name, mutate_sql, mutate_parameters, restore_sql, restore_parameters, expected in corruption_cases:
            corrupt_conn.execute(mutate_sql, mutate_parameters)
            corrupt_conn.commit()
            corrupt_before = logical_dump(corrupt_conn)
            corruption_ok = corruption_ok and expect_code(
                lambda: corrupt.apply_restore_undo(
                    corrupt_operation_id, corrupt_preview["undo_revision"]
                ),
                expected,
            ) and logical_dump(corrupt_conn) == corrupt_before
            corrupt_conn.execute(restore_sql, restore_parameters)
            corrupt_conn.commit()
        corrupt_conn.execute("PRAGMA ignore_check_constraints=ON")
        enum_cases = (
            ("change_kind", "DELETE", first_change["change_kind"]),
            ("entity_kind", "private_table", first_change["entity_kind"]),
        )
        for column, invalid, original_value in enum_cases:
            corrupt_conn.execute(
                f"UPDATE restore_operation_changes SET {column}=? WHERE id=?",
                (invalid, first_change["id"]),
            )
            corrupt_conn.commit()
            corrupt_before = logical_dump(corrupt_conn)
            corruption_ok = corruption_ok and expect_code(
                lambda: corrupt.apply_restore_undo(
                    corrupt_operation_id, corrupt_preview["undo_revision"]
                ),
                "CHANGESET_INVALID",
            ) and logical_dump(corrupt_conn) == corrupt_before
            corrupt_conn.execute(
                f"UPDATE restore_operation_changes SET {column}=? WHERE id=?",
                (original_value, first_change["id"]),
            )
            corrupt_conn.commit()
        corrupt_conn.execute("PRAGMA ignore_check_constraints=OFF")
        check("changeset_corruption_fails_closed_zero_mutation", corruption_ok, failures)

        cache, cache_conn, _cache_selected, _cache_pre, cache_operation_id, cache_preview = applied_fixture(
            base, "cache"
        )
        _cache_operation, cache_changes = operation_rows(cache_conn, cache_operation_id)
        cache_change = next(
            (row for row in cache_changes if row["change_kind"] == "CACHE_CREATE"), None
        )
        cache_id = ""
        if cache_change is not None:
            cache_id = json.loads(cache_change["after_json"])["relative_cache_id"]
            (cache.TEXT_CACHE_DIR / cache_id).unlink(missing_ok=True)
        cache_result = cache.apply_restore_undo(cache_operation_id, cache_preview["undo_revision"])
        check(
            "missing_controlled_cache_does_not_block_or_leak",
            cache_change is not None
            and cache_result["undo_applied"] is True
            and cache_id not in json.dumps(cache_result),
            failures,
        )

        api, api_conn, _api_selected, _api_pre, api_operation_id, api_preview = applied_fixture(
            base, "api"
        )
        bad_status, bad_response = http_request(
            api,
            "POST",
            "/api/backup/restore-undo-apply",
            {
                "operation_id": api_operation_id,
                "expected_undo_revision": api_preview["undo_revision"],
                "path": str(base / "forbidden"),
            },
        )
        good_status, good_response = http_request(
            api,
            "POST",
            "/api/backup/restore-undo-apply",
            {
                "operation_id": api_operation_id,
                "expected_undo_revision": api_preview["undo_revision"],
            },
        )
        check(
            "narrow_http_contract_rejects_browser_overrides",
            bad_status == 400 and "error" in bad_response,
            failures,
        )
        check(
            "http_success_response_is_minimal_and_private",
            good_status == 200
            and set(good_response) == {"ok", "operation_id", "status", "undo_applied"}
            and str(base) not in json.dumps(good_response)
            and "sha256" not in json.dumps(good_response)
            and "locator" not in json.dumps(good_response),
            failures,
        )

        complexity, complexity_conn, _complexity_selected, _complexity_pre, complexity_operation_id, complexity_preview = applied_fixture(
            base, "complexity"
        )
        complexity_conn.executemany(
            "INSERT INTO app_settings(key, value, updated_at) VALUES (?, 'unrelated', '2026-01-01T00:00:00+00:00')",
            ((f"unrelated-{index:05d}",) for index in range(2_000)),
        )
        complexity_conn.commit()
        statements: list[str] = []
        original_connect = complexity.connect_restore_db_writable

        def traced_connect() -> sqlite3.Connection:
            traced = original_connect()
            traced.set_trace_callback(statements.append)
            return traced

        complexity.connect_restore_db_writable = traced_connect
        try:
            complexity.apply_restore_undo(
                complexity_operation_id, complexity_preview["undo_revision"]
            )
        finally:
            complexity.connect_restore_db_writable = original_connect
        check(
            "complexity_is_bounded_to_changeset_and_affected_graph",
            len(statements) < 250
            and not any("SELECT * FROM app_settings" in statement for statement in statements),
            failures,
        )

        isolation, isolation_conn, isolation_selected, _isolation_pre, isolation_operation_id, isolation_preview = applied_fixture(
            base, "isolation"
        )
        calls = {"canvas": 0, "openai": 0, "credential": 0, "network": 0}

        def deny(kind: str) -> Callable[..., Any]:
            def blocked(*_args: Any, **_kwargs: Any) -> Any:
                calls[kind] += 1
                raise AssertionError(f"{kind} boundary called")
            return blocked

        isolation.with_canvas_connector = deny("canvas")
        isolation.openai_responses_request = deny("openai")
        isolation.initialize_credential_client = deny("credential")
        isolation.urllib.request.urlopen = deny("network")
        isolation.apply_restore_undo(isolation_operation_id, isolation_preview["undo_revision"])
        check(
            "no_network_canvas_openai_or_credentials",
            not any(calls.values()) and isolation_selected.read_bytes() == original,
            failures,
        )

        for connection in (
            conn,
            empty_conn,
            dep_conn,
            overlap_conn,
            unrelated_conn,
            stale_conn,
            concurrency_conn,
            corrupt_conn,
            cache_conn,
            api_conn,
            complexity_conn,
            isolation_conn,
            mixed_conn,
            history_conn,
            unrelated_history_conn,
        ):
            try:
                connection.close()
            except sqlite3.Error:
                pass

    if failures:
        print("Backup restore Undo Apply acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore Undo Apply acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
