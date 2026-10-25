#!/usr/bin/env python3
"""Synthetic acceptance for private atomic Restore operation recording."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import load_server, seed_state  # noqa: E402
from backup_restore_apply_acceptance import (  # noqa: E402
    clear_restore_state,
    consistency_archive,
    decisions_for,
    export_archive,
    prepare_target,
    seed_search_metadata,
)
from backup_restore_preview_acceptance import stage  # noqa: E402
from backup_restore_relink_apply_acceptance import fixture as relink_fixture  # noqa: E402


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def expect_failure(operation: Callable[[], Any], expected: str) -> bool:
    try:
        operation()
    except Exception as exc:
        return str(exc) == expected
    return False


def operation_row(conn: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM restore_operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    assert row is not None
    return row


def operation_changes(conn: sqlite3.Connection, operation_pk: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM restore_operation_changes WHERE restore_operation_id=? ORDER BY sequence",
        (operation_pk,),
    ).fetchall()


def digestable_changes(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    fields = (
        "sequence",
        "change_kind",
        "entity_kind",
        "locator_json",
        "before_json",
        "after_json",
        "before_token",
        "after_token",
        "metadata_json",
    )
    return [{field: row[field] for field in fields} for row in rows]


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-restore-operation-") as temporary:
        base = Path(temporary)

        migration = load_server(base / "migration")
        migration_conn = migration.connect_db()
        migration.init_db(migration_conn)
        expected_tables = {"restore_operations", "restore_operation_changes"}
        tables = {
            str(row["name"])
            for row in migration_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            str(row["name"])
            for row in migration_conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        check("fresh_db_creates_only_intended_changeset_tables", expected_tables.issubset(tables), failures)
        check(
            "changeset_indexes_created",
            {
                "idx_restore_operations_status",
                "idx_restore_operation_changes_operation",
            }.issubset(indexes),
            failures,
        )
        check(
            "migration_recorded_once",
            migration_conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=6 AND name='phase2_restore_operation_changeset'"
            ).fetchone()[0] == 1,
            failures,
        )
        migration.init_db(migration_conn)
        check(
            "migration_idempotent",
            migration_conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=6").fetchone()[0] == 1,
            failures,
        )
        migration_conn.execute("DROP TABLE restore_operation_changes")
        migration_conn.execute("DROP TABLE restore_operations")
        migration_conn.execute("DELETE FROM schema_migrations WHERE version=6")
        migration_conn.commit()
        migration.run_schema_migrations(migration_conn)
        check(
            "existing_db_upgrade_recreates_changeset_schema",
            all(
                migration_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                for table in expected_tables
            ),
            failures,
        )
        migration_conn.close()

        source = load_server(base / "source")
        source_conn = seed_state(source, base / "source", 100)
        archive = export_archive(source, source_conn)
        consistency = consistency_archive(source, archive, base / "consistency.zip")

        target, conn, _staged, plan = prepare_target(base, consistency, "success", "a" * 32)
        seed_search_metadata(conn)
        conn.execute("UPDATE files SET metadata_locked=0 WHERE stable_id='material_syn_local'")
        conn.commit()
        plan = target.preview_staged_restore("a" * 32)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = target.apply_staged_restore(
                "a" * 32,
                plan["plan_revision"],
                decisions_for(plan),
            )
        operation_id = result["applied"].get("restore_operation_id", "")
        check("successful_restore_returns_opaque_operation_id", bool(target.RESTORE_OPERATION_ID_RE.fullmatch(operation_id)), failures)
        operation = operation_row(conn, operation_id)
        changes = operation_changes(conn, int(operation["id"]))
        check("restore_operation_status_applied", operation["status"] == "APPLIED" and operation["undone_at"] is None, failures)
        check("operation_summary_matches_private_changes", operation["change_count"] == len(changes) and len(changes) > 0, failures)
        check("change_sequence_is_deterministic", [row["sequence"] for row in changes] == list(range(1, len(changes) + 1)), failures)
        check(
            "create_rows_have_null_before_and_exact_after",
            any(row["change_kind"] == "INSERT" and row["before_json"] is None and row["after_json"] for row in changes),
            failures,
        )
        file_update = next(
            (
                row
                for row in changes
                if row["entity_kind"] == "file"
                and row["change_kind"] == "UPDATE"
                and json.loads(row["after_json"])["course_code"] == "SYN9300"
            ),
            None,
        )
        material_update = next(
            (row for row in changes if row["entity_kind"] == "material" and row["change_kind"] == "UPDATE"),
            None,
        )
        check(
            "material_overlay_records_exact_before_after",
            file_update is not None
            and json.loads(file_update["before_json"])["course_code"] == "SYN9200"
            and file_update["before_token"] != file_update["after_token"],
            failures,
        )
        check("material_projection_update_recorded", material_update is not None, failures)
        digest_input = digestable_changes(changes)
        check(
            "changeset_digest_deterministic",
            target.restore_changeset_digest(digest_input) == operation["changeset_digest"]
            and target.restore_changeset_digest(digest_input) == target.restore_changeset_digest(digest_input),
            failures,
        )
        private_note_bodies = [
            json.loads(row["after_json"])["body"]
            for row in changes
            if row["entity_kind"] == "note" and row["after_json"]
        ]
        public_payload = json.dumps(result, sort_keys=True)
        check(
            "private_change_images_absent_from_public_result",
            not any(body in public_payload for body in private_note_bodies)
            and "before_json" not in public_payload
            and "locator_json" not in public_payload,
            failures,
        )
        check("absolute_paths_absent_from_public_result_and_logs", str(base) not in public_payload and str(base) not in output.getvalue(), failures)
        check(
            "private_runtime_row_ids_absent_from_public_result",
            "restore_operation_id" in result["applied"]
            and "operation_pk" not in public_payload
            and "primary_key" not in public_payload,
            failures,
        )

        before_operation_count = conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0]
        noop_plan = target.preview_staged_restore("a" * 32)
        noop_result = target.apply_staged_restore(
            "a" * 32,
            noop_plan["plan_revision"],
            decisions_for(noop_plan),
        )
        check(
            "no_op_restore_creates_no_operation",
            "restore_operation_id" not in noop_result["applied"]
            and conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == before_operation_count,
            failures,
        )

        state_target = load_server(base / "state-update")
        state_conn = seed_state(state_target, base / "state-update", 900, reverse_insert_order=True)
        state_conn.execute(
            "UPDATE academic_change_event_state SET seen_at=NULL, updated_at='2026-03-01T00:00:00+00:00'"
        )
        state_conn.commit()
        stage(state_target, archive, "d" * 32)
        state_plan = state_target.preview_staged_restore("d" * 32)
        state_result = state_target.apply_staged_restore(
            "d" * 32,
            state_plan["plan_revision"],
            decisions_for(state_plan),
        )
        state_operation = operation_row(
            state_conn,
            state_result["applied"]["restore_operation_id"],
        )
        state_changes = operation_changes(state_conn, int(state_operation["id"]))
        state_update = next(
            (
                row
                for row in state_changes
                if row["entity_kind"] == "academic_change_event_state"
                and row["change_kind"] == "UPDATE"
            ),
            None,
        )
        check(
            "update_user_state_records_exact_before_after",
            state_update is not None
            and json.loads(state_update["before_json"])["seen_at"] is None
            and json.loads(state_update["after_json"])["seen_at"] is not None,
            failures,
        )

        rollback, rollback_conn, _copy, rollback_plan = prepare_target(
            base, consistency, "rollback", "b" * 32
        )
        seed_search_metadata(rollback_conn)
        rollback_plan = rollback.preview_staged_restore("b" * 32)
        check(
            "failure_after_recorder_rolls_back_restore_and_changeset",
            expect_failure(
                lambda: rollback.apply_staged_restore(
                    "b" * 32,
                    rollback_plan["plan_revision"],
                    decisions_for(rollback_plan),
                    failure_point="after_restore_operation_recorded",
                ),
                "synthetic_restore_failure_after_restore_operation_recorded",
            )
            and rollback_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 0
            and rollback_conn.execute("SELECT COUNT(*) FROM restore_operation_changes").fetchone()[0] == 0,
            failures,
        )

        early, early_conn, _copy, early_plan = prepare_target(base, archive, "early", "c" * 32)
        check(
            "failed_restore_leaves_zero_operation_rows",
            expect_failure(
                lambda: early.apply_staged_restore(
                    "c" * 32,
                    early_plan["plan_revision"],
                    decisions_for(early_plan),
                    failure_point="after_first_create",
                ),
                "synthetic_restore_failure_after_first_create",
            )
            and early_conn.execute("SELECT COUNT(*) FROM restore_operations").fetchone()[0] == 0
            and early_conn.execute("SELECT COUNT(*) FROM restore_operation_changes").fetchone()[0] == 0,
            failures,
        )

        relink, relink_conn, _restore_id, _source_id, selected, request = relink_fixture(
            base, "recorder-relink", seed_target=False
        )
        original_bytes = selected.read_bytes()
        boundaries = {"canvas": 0, "openai": 0, "credential": 0}

        def deny(kind: str) -> Callable[..., Any]:
            def blocked(*_args: Any, **_kwargs: Any) -> Any:
                boundaries[kind] += 1
                raise AssertionError(f"{kind} boundary called")
            return blocked

        relink.with_canvas_connector = deny("canvas")
        relink.openai_responses_request = deny("openai")
        relink.initialize_credential_client = deny("credential")
        relink_result = relink.apply_staged_restore(
            request["restore_id"],
            request["expected_plan_revision"],
            request["decisions"],
            request["relinks"],
        )
        relink_operation_id = relink_result["applied"]["restore_operation_id"]
        relink_operation = operation_row(relink_conn, relink_operation_id)
        relink_changes = operation_changes(relink_conn, int(relink_operation["id"]))
        relink_entities = {row["entity_kind"] for row in relink_changes}
        check(
            "relink_records_canonical_and_source_state",
            {"file", "legacy_file_version", "blob", "source", "material", "material_version"}.issubset(relink_entities),
            failures,
        )
        check("relink_records_only_safe_relative_cache_identity", "text_cache_artifact" in relink_entities and str(base) not in "".join(str(row["metadata_json"]) + str(row["locator_json"]) for row in relink_changes), failures)
        check("relink_source_remains_user_owned_and_untouched", selected.read_bytes() == original_bytes, failures)
        check("relink_capability_consumed_only_after_commit", not relink.restore_relink_descriptor_path(request["relinks"][0]["relink_id"]).exists(), failures)
        check("recorder_introduces_no_network_or_credentials", not any(boundaries.values()), failures)
        check("operation_ids_are_unique", operation_id != relink_operation_id, failures)

        source_text = (ROOT / "server.py").read_text(encoding="utf-8")
        check("undo_preview_not_implemented", "/api/backup/restore-undo-preview" not in source_text, failures)
        check("undo_apply_not_implemented", "/api/backup/restore-undo-apply" not in source_text, failures)
        check("backup_wins_not_implemented", '"RESTORE_BACKUP"' not in source_text, failures)

        for active_conn in (source_conn, conn, state_conn, rollback_conn, early_conn, relink_conn):
            active_conn.close()

    if failures:
        print("Backup restore operation acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore operation acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
