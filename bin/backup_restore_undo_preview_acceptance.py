#!/usr/bin/env python3
"""Synthetic acceptance for read-only post-commit Restore Undo Preview."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
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


PRIVATE_CANARY = "synthetic-private-note-body-never-public"


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def logical_dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def operation_rows(conn: sqlite3.Connection, operation_id: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    operation = conn.execute(
        "SELECT * FROM restore_operations WHERE operation_id=?", (operation_id,)
    ).fetchone()
    assert operation is not None
    changes = conn.execute(
        "SELECT * FROM restore_operation_changes WHERE restore_operation_id=? ORDER BY sequence",
        (operation["id"],),
    ).fetchall()
    return operation, changes


def http_request(
    server: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = int(httpd.server_address[1])
    headers = {"Host": f"localhost:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.update(
            {
                "Content-Type": "application/json",
                "Origin": f"http://localhost:{port}",
                "X-StudyHub-CSRF": server.CSRF_TOKEN,
            }
        )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method, data=data, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def restore_row(conn: sqlite3.Connection, table: str, image: dict[str, Any]) -> None:
    columns = tuple(image)
    conn.execute(
        f"INSERT INTO {table}({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(image[column] for column in columns),
    )
    conn.commit()


def mutate_and_restore(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
    restore_sql: str,
    restore_parameters: tuple[Any, ...],
    call: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    conn.execute(sql, parameters)
    conn.commit()
    result = call()
    conn.execute(restore_sql, restore_parameters)
    conn.commit()
    return result


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-undo-preview-") as temporary:
        base = Path(temporary)
        source = load_server(base / "source")
        source_conn = seed_state(source, base / "source", 100)
        archive = export_archive(source, source_conn)
        consistency = consistency_archive(source, archive, base / "consistency.zip")

        target, conn, _staged, plan = prepare_target(base, consistency, "ready", "a" * 32)
        seed_search_metadata(conn)
        conn.execute("UPDATE files SET metadata_locked=0 WHERE stable_id='material_syn_local'")
        conn.commit()
        plan = target.preview_staged_restore("a" * 32)
        applied = target.apply_staged_restore("a" * 32, plan["plan_revision"], decisions_for(plan))
        operation_id = applied["applied"]["restore_operation_id"]
        operation, changes = operation_rows(conn, operation_id)
        private_note = next(
            (change for change in changes if change["entity_kind"] == "note"), None
        )
        if private_note is not None:
            image = json.loads(private_note["after_json"])
            conn.execute("UPDATE notes SET body=? WHERE id=?", (PRIVATE_CANARY, image["id"]))
            conn.commit()
            conn.execute("UPDATE notes SET body=? WHERE id=?", (image["body"], image["id"]))
            conn.commit()

        before_dump = logical_dump(conn)
        before_cache = tree_digest(target.CACHE_DIR)
        before_db_bytes = target.DB_PATH.read_bytes()
        first = target.preview_restore_undo(operation_id)
        second = target.preview_restore_undo(operation_id)
        check(
            "ready_preview_is_deterministic",
            first.get("undo_available") is True
            and first.get("status") == "APPLIED"
            and target.RESTORE_UNDO_REVISION_RE.fullmatch(first.get("undo_revision", "")) is not None
            and first == second,
            failures,
        )
        check(
            "safe_summary_matches_private_operation",
            first.get("summary") == {
                "changes": operation["change_count"],
                "inserts": operation["insert_count"],
                "updates": operation["update_count"],
                "artifacts": operation["artifact_count"],
            },
            failures,
        )
        check(
            "preview_is_zero_mutation",
            logical_dump(conn) == before_dump
            and tree_digest(target.CACHE_DIR) == before_cache
            and target.DB_PATH.read_bytes() == before_db_bytes,
            failures,
        )

        restarted = load_server(base / "ready")
        restarted_preview = restarted.preview_restore_undo(operation_id)
        latest = restarted.latest_restore_undo_operation()
        check(
            "restart_preserves_preview_and_latest_discovery",
            restarted_preview == first
            and latest.get("operation", {}).get("operation_id") == operation_id,
            failures,
        )

        status, http_preview = http_request(
            target, "POST", "/api/backup/restore-undo-preview", {"operation_id": operation_id}
        )
        latest_status, http_latest = http_request(
            target, "GET", "/api/backup/restore-undo-latest"
        )
        extra_status, _extra = http_request(
            target,
            "POST",
            "/api/backup/restore-undo-preview",
            {"operation_id": operation_id, "path": str(base)},
        )
        check(
            "narrow_http_contract_and_latest_surface",
            status == 200
            and http_preview.get("preview") == first
            and latest_status == 200
            and http_latest.get("operation", {}).get("operation_id") == operation_id
            and extra_status == 400,
            failures,
        )

        update_change = next(change for change in changes if change["change_kind"] == "UPDATE")
        update_image = json.loads(update_change["after_json"])
        update_spec = target.restore_entity_spec(update_change["entity_kind"])
        update_column = next(
            column
            for column in update_spec.columns
            if column not in {update_spec.primary_key, "stable_id"}
            and isinstance(update_image[column], str)
        )
        old_value = update_image[update_column]
        changed_value = old_value + "-changed"
        overlap = mutate_and_restore(
            conn,
            f"UPDATE {update_spec.table} SET {update_column}=? WHERE {update_spec.primary_key}=?",
            (changed_value, update_image[update_spec.primary_key]),
            f"UPDATE {update_spec.table} SET {update_column}=? WHERE {update_spec.primary_key}=?",
            (old_value, update_image[update_spec.primary_key]),
            lambda: target.preview_restore_undo(operation_id),
        )
        check(
            "affected_update_overlap_blocks_then_exact_restore_recovers",
            overlap.get("reason") == "ROW_CHANGED"
            and target.preview_restore_undo(operation_id) == first,
            failures,
        )

        insert_change = next(
            change for change in changes if change["change_kind"] == "INSERT" and change["entity_kind"] == "note"
        )
        insert_image = json.loads(insert_change["after_json"])
        conn.execute("DELETE FROM notes WHERE id=?", (insert_image["id"],))
        conn.commit()
        missing = target.preview_restore_undo(operation_id)
        restore_row(conn, "notes", insert_image)
        check(
            "missing_insert_blocks_then_exact_restore_recovers",
            missing.get("reason") == "ROW_MISSING"
            and target.preview_restore_undo(operation_id) == first,
            failures,
        )

        course_change = next(
            change for change in changes if change["change_kind"] == "INSERT" and change["entity_kind"] == "course"
        )
        course_image = json.loads(course_change["after_json"])
        conn.execute(
            "INSERT INTO weeks(course_id, week_label, week_number, path, has_materials, file_count, "
            "stable_id, kind, origin, archived, created_at, updated_at) "
            "VALUES (?, 'Later Week', 99, '', 0, 0, 'unit_later_external', 'week', 'manual', 0, '', '')",
            (course_image["id"],),
        )
        later_week_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        dependency = target.preview_restore_undo(operation_id)
        conn.execute("DELETE FROM weeks WHERE id=?", (later_week_id,))
        conn.commit()
        check(
            "unexpected_dependency_blocks_and_operation_graph_is_allowed",
            dependency.get("reason") == "DEPENDENCY_CHANGED"
            and target.preview_restore_undo(operation_id).get("undo_available") is True,
            failures,
        )

        unrelated_course = conn.execute(
            "SELECT id, display_name FROM courses WHERE id<>? ORDER BY id LIMIT 1", (course_image["id"],)
        ).fetchone()
        assert unrelated_course is not None
        conn.execute(
            "UPDATE courses SET display_name=? WHERE id=?",
            (str(unrelated_course["display_name"]) + " unrelated", unrelated_course["id"]),
        )
        conn.commit()
        unrelated = target.preview_restore_undo(operation_id)
        check(
            "unrelated_mutation_does_not_invalidate_or_change_revision",
            unrelated.get("undo_available") is True
            and unrelated.get("undo_revision") == first.get("undo_revision"),
            failures,
        )

        operation_original = dict(operation)
        first_change_original = dict(changes[0])

        def corruption_case(
            name: str,
            sql: str,
            parameters: tuple[Any, ...],
            expected: str,
            restore_sql: str,
            restore_parameters: tuple[Any, ...],
        ) -> None:
            result = mutate_and_restore(
                conn, sql, parameters, restore_sql, restore_parameters,
                lambda: target.preview_restore_undo(operation_id),
            )
            check(name, result.get("reason") == expected and str(base) not in json.dumps(result), failures)

        corruption_case(
            "digest_corruption_fails_closed",
            "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
            ("0" * 64, operation["id"]),
            "CHANGESET_INVALID",
            "UPDATE restore_operations SET changeset_digest=? WHERE id=?",
            (operation_original["changeset_digest"], operation["id"]),
        )
        corruption_case(
            "broken_sequence_fails_closed",
            "UPDATE restore_operation_changes SET sequence=999 WHERE id=?",
            (changes[0]["id"],),
            "CHANGESET_INVALID",
            "UPDATE restore_operation_changes SET sequence=? WHERE id=?",
            (first_change_original["sequence"], changes[0]["id"]),
        )
        corruption_case(
            "token_corruption_fails_closed",
            "UPDATE restore_operation_changes SET after_token=? WHERE id=?",
            ("0" * 64, changes[0]["id"]),
            "CHANGESET_INVALID",
            "UPDATE restore_operation_changes SET after_token=? WHERE id=?",
            (first_change_original["after_token"], changes[0]["id"]),
        )
        corruption_case(
            "malformed_image_fails_closed",
            "UPDATE restore_operation_changes SET after_json='{' WHERE id=?",
            (changes[0]["id"],),
            "CHANGESET_INVALID",
            "UPDATE restore_operation_changes SET after_json=? WHERE id=?",
            (first_change_original["after_json"], changes[0]["id"]),
        )
        corruption_case(
            "unsupported_format_fails_closed",
            "UPDATE restore_operations SET format_version=999 WHERE id=?",
            (operation["id"],),
            "UNSUPPORTED_CHANGESET",
            "UPDATE restore_operations SET format_version=? WHERE id=?",
            (operation_original["format_version"], operation["id"]),
        )
        corruption_case(
            "unsupported_schema_fails_closed",
            "UPDATE restore_operations SET schema_fingerprint='restore_schema_unsupported' WHERE id=?",
            (operation["id"],),
            "UNSUPPORTED_CHANGESET",
            "UPDATE restore_operations SET schema_fingerprint=? WHERE id=?",
            (operation_original["schema_fingerprint"], operation["id"]),
        )
        conn.execute("PRAGMA ignore_check_constraints=ON")
        corruption_case(
            "invalid_change_kind_fails_closed",
            "UPDATE restore_operation_changes SET change_kind='DELETE' WHERE id=?",
            (changes[0]["id"],),
            "CHANGESET_INVALID",
            "UPDATE restore_operation_changes SET change_kind=? WHERE id=?",
            (first_change_original["change_kind"], changes[0]["id"]),
        )
        corruption_case(
            "invalid_entity_kind_fails_closed",
            "UPDATE restore_operation_changes SET entity_kind='private_table' WHERE id=?",
            (changes[0]["id"],),
            "CHANGESET_INVALID",
            "UPDATE restore_operation_changes SET entity_kind=? WHERE id=?",
            (first_change_original["entity_kind"], changes[0]["id"]),
        )
        conn.execute("PRAGMA ignore_check_constraints=OFF")

        malformed_status, _malformed = http_request(
            target, "POST", "/api/backup/restore-undo-preview", {"operation_id": "1"}
        )
        unknown_status, _unknown = http_request(
            target,
            "POST",
            "/api/backup/restore-undo-preview",
            {"operation_id": "restoreop_" + "f" * 32},
        )
        conn.execute(
            "UPDATE restore_operations SET status='UNDONE', undone_at=? WHERE id=?",
            ("2026-10-26T00:00:00+11:00", operation["id"]),
        )
        conn.commit()
        undone = target.preview_restore_undo(operation_id)
        conn.execute(
            "UPDATE restore_operations SET status='APPLIED', undone_at=NULL WHERE id=?",
            (operation["id"],),
        )
        conn.commit()
        check(
            "operation_identity_and_undone_status_are_safe",
            malformed_status == 400
            and unknown_status == 404
            and undone.get("reason") == "ALREADY_UNDONE",
            failures,
        )

        conn.executemany(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) "
            "VALUES ('file', NULL, NULL, '', ?, '', '')",
            ((f"unrelated-{index}",) for index in range(2_000)),
        )
        conn.commit()
        statements: list[str] = []
        original_connect = target.connect_backup_db_readonly

        def traced_connect() -> sqlite3.Connection:
            traced = original_connect()
            traced.set_trace_callback(statements.append)
            return traced

        target.connect_backup_db_readonly = traced_connect
        try:
            complexity = target.preview_restore_undo(operation_id)
        finally:
            target.connect_backup_db_readonly = original_connect
        select_statements = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        check(
            "preview_queries_only_affected_graph",
            complexity.get("undo_available") is True
            and select_statements
            and all(" FROM notes " not in statement or " WHERE " in statement for statement in select_statements)
            and not any("SELECT * FROM notes" in statement for statement in select_statements),
            failures,
        )

        relink, relink_conn, relink_restore_id, _source_id, selected, request = relink_fixture(
            base, "undo-preview-relink"
        )
        selected_before = selected.read_bytes()
        relink_result = relink.apply_staged_restore(
            request["restore_id"],
            request["expected_plan_revision"],
            request["decisions"],
            request["relinks"],
        )
        relink_operation_id = relink_result["applied"]["restore_operation_id"]
        relink_ready = relink.preview_restore_undo(relink_operation_id)
        relink_operation, relink_changes = operation_rows(relink_conn, relink_operation_id)
        source_change = next(
            change for change in relink_changes if change["entity_kind"] == "source"
        )
        source_image = json.loads(source_change["after_json"])
        conn_value = source_image["source_label"]
        relink_conn.execute(
            "UPDATE sources SET source_label=? WHERE id=?",
            (conn_value + " changed", source_image["id"]),
        )
        relink_conn.commit()
        relink_changed = relink.preview_restore_undo(relink_operation_id)
        relink_conn.execute(
            "UPDATE sources SET source_label=? WHERE id=?", (conn_value, source_image["id"])
        )
        relink_conn.commit()
        cache_change = next(
            (change for change in relink_changes if change["entity_kind"] == "text_cache_artifact"),
            None,
        )
        if cache_change is not None:
            cache_id = json.loads(cache_change["after_json"])["relative_cache_id"]
            (relink.TEXT_CACHE_DIR / cache_id).unlink(missing_ok=True)
        relink_cache_absent = relink.preview_restore_undo(relink_operation_id)
        check(
            "canonical_relink_graph_and_cache_semantics",
            relink_ready.get("undo_available") is True
            and relink_changed.get("reason") == "ROW_CHANGED"
            and relink_cache_absent.get("undo_available") is True
            and selected.read_bytes() == selected_before,
            failures,
        )

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            public = target.preview_restore_undo(operation_id)
        serialized = json.dumps(public, sort_keys=True) + output.getvalue()
        private_fragments = (
            PRIVATE_CANARY,
            str(base),
            "before_json",
            "after_json",
            "locator_json",
            "primary_key",
            "relative_cache_id",
            "source_syn",
        )
        check(
            "public_response_and_logs_are_privacy_safe",
            not any(fragment in serialized for fragment in private_fragments)
            and set(public).issubset(
                {"operation_id", "status", "undo_available", "undo_revision", "committed_at", "summary", "reason"}
            ),
            failures,
        )
        source_text = (ROOT / "server.py").read_text(encoding="utf-8")
        original_urlopen = target.urllib.request.urlopen
        original_canvas = target.with_canvas_connector

        def forbidden_call(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Undo Preview must not cross an external capability boundary")

        target.urllib.request.urlopen = forbidden_call
        target.with_canvas_connector = forbidden_call
        try:
            isolated = target.preview_restore_undo(operation_id)
        finally:
            target.urllib.request.urlopen = original_urlopen
            target.with_canvas_connector = original_canvas

        demo = load_server(base / "demo-isolation", profile="demo-test")
        demo_conn = demo.connect_db()
        demo.init_db(demo_conn)
        demo_conn.commit()
        demo_conn.close()
        check(
            "no_undo_apply_network_or_credential_surface",
            "/api/backup/restore-undo-apply" not in source_text
            and isolated.get("undo_available") is True
            and demo.latest_restore_undo_operation() == {"operation": None}
            and "with_canvas_connector" not in target.preview_restore_undo.__code__.co_names
            and "credential" not in target.preview_restore_undo.__code__.co_names,
            failures,
        )

        source_conn.close()
        conn.close()
        relink_conn.close()

    if failures:
        print("Backup restore Undo Preview acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore Undo Preview acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
