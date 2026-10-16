#!/usr/bin/env python3
"""Synthetic acceptance checks for the academic change journal."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from canvas_file_acquisition_acceptance import (
    BYTES_A,
    BYTES_B,
    COURSE_ID,
    BytesResponse,
    DownloadOpen,
    ScenarioConnector,
    load_server,
    seed_offering,
)


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def remote_file(
    server: Any,
    remote_file_id: str,
    body: bytes,
    *,
    filename: str = "synthetic-notes.txt",
    updated: str = "2026-01-01T00:00:00Z",
    modified: str | None = None,
):
    return server.CanvasRemoteFile(
        remote_file_id=remote_file_id,
        remote_course_id=COURSE_ID,
        display_name=filename,
        filename=filename,
        content_type="text/plain",
        byte_size=len(body),
        remote_created_at="2026-01-01T00:00:00Z",
        remote_updated_at=updated,
        remote_modified_at=modified or updated,
        locked=False,
        hidden=False,
        hidden_for_user=False,
    )


def first_action(plan: dict[str, Any]) -> dict[str, Any]:
    return plan["offerings"][0]["actions"][0]


def apply_one(
    server: Any,
    conn: sqlite3.Connection,
    offering: sqlite3.Row,
    item: Any,
    body: bytes | None,
    **kwargs: Any,
) -> dict[str, Any]:
    connector = ScenarioConnector(server, [item])
    plan = server.canvas_file_sync_plan(conn, connector)
    stable_id = str(first_action(plan)["remote_file_stable_id"])
    result = server.apply_canvas_file_sync(
        conn,
        connector,
        plan["plan_revision"],
        [stable_id],
        requested_offering_ids=[str(offering["stable_id"])],
        open_url=DownloadOpen([BytesResponse(body)]) if body is not None else DownloadOpen([]),
        **kwargs,
    )
    return {"result": result, "plan": plan, "stable_id": stable_id}


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def check_upgrade_migration(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    item = remote_file(server, "migration-file", BYTES_A)
    apply_one(server, conn, offering, item, BYTES_A)
    conn.commit()
    conn.execute(
        "INSERT INTO sync_events(event_type, detail, created_at) VALUES ('scan', ?, ?)",
        (json.dumps({"synthetic": True}), "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.execute("DROP TABLE academic_change_event_state")
    conn.execute("DROP TABLE academic_change_events")
    conn.execute("DELETE FROM schema_migrations WHERE version=5")
    conn.commit()
    preserved = (
        "terms",
        "courses",
        "weeks",
        "files",
        "file_versions",
        "sources",
        "blobs",
        "materials",
        "material_versions",
        "course_offerings",
        "course_offering_selections",
        "canvas_file_sync_baselines",
        "course_offering_local_bindings",
        "sync_events",
    )
    before = {table: table_rows(conn, table) for table in preserved}
    server.run_schema_migrations(conn)
    after = {table: table_rows(conn, table) for table in preserved}
    server.run_schema_migrations(conn)
    check("pr25_to_journal_migration_preserves_rows", before == after, failures)
    check("legacy_sync_events_survive_migration", before["sync_events"] == after["sync_events"], failures)
    check("journal_migration_recorded_once", conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=5").fetchone()[0] == 1, failures)
    check("journal_migration_tables_created", all(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0] == 1 for name in ("academic_change_events", "academic_change_event_state")), failures)
    event_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(academic_change_events)")}
    check("journal_migration_has_predecessor_field", "predecessor_event_stable_id" in event_columns, failures)
    event_indexes = {str(row["name"]) for row in conn.execute("PRAGMA index_list(academic_change_events)")}
    check("journal_migration_has_remote_chain_index", "idx_academic_change_events_remote_chain" in event_indexes, failures)
    check("journal_migration_foreign_keys_valid", not conn.execute("PRAGMA foreign_key_check").fetchall(), failures)
    conn.close()


def check_transaction_failures(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    item = remote_file(server, "atomic-file", BYTES_A)
    applied = apply_one(server, conn, offering, item, BYTES_A)
    check("event_exists_inside_caller_transaction", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 1, failures)
    conn.rollback()
    check("caller_rollback_removes_ingestion_and_event", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0 and conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0 and conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 0, failures)

    plan = server.canvas_file_sync_plan(conn, ScenarioConnector(server, [item]))
    stable_id = str(first_action(plan)["remote_file_stable_id"])
    try:
        server.apply_canvas_file_sync(
            conn,
            ScenarioConnector(server, [item]),
            plan["plan_revision"],
            [stable_id],
            requested_offering_ids=[str(offering["stable_id"])],
            open_url=DownloadOpen([BytesResponse(BYTES_A)]),
            fail_after_ingestion=1,
        )
    except RuntimeError:
        pass
    check("failed_ingestion_creates_no_event", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0, failures)

    try:
        apply_one(server, conn, offering, item, BYTES_A, fail_baseline=True)
    except RuntimeError:
        pass
    check("baseline_failure_creates_no_event", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0, failures)

    stale_plan = server.canvas_file_sync_plan(conn, ScenarioConnector(server, [item]))
    try:
        server.apply_canvas_file_sync(
            conn,
            ScenarioConnector(server, [item]),
            "stale-plan",
            [str(first_action(stale_plan)["remote_file_stable_id"])],
            requested_offering_ids=[str(offering["stable_id"])],
            open_url=DownloadOpen([]),
        )
    except Exception:
        pass
    check("stale_plan_creates_no_event", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0, failures)
    conn.close()


def check_event_lifecycle(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    first = remote_file(server, "lifecycle-file", BYTES_A)
    initial = apply_one(server, conn, offering, first, BYTES_A)
    conn.commit()
    added = conn.execute("SELECT * FROM academic_change_events").fetchall()
    check("first_add_creates_one_added_event", len(added) == 1 and added[0]["event_kind"] == "CANVAS_FILE_ADDED", failures)
    check("new_event_is_unseen", conn.execute("SELECT COUNT(*) FROM academic_change_event_state").fetchone()[0] == 0, failures)
    serialized = json.dumps(dict(added[0]), sort_keys=True)
    check("event_payload_excludes_sensitive_data", all(value not in serialized for value in ("https://", "/runtime/", "syn-token", "signed")), failures)
    check("event_identity_is_deterministic", added[0]["stable_id"].startswith("change_") and len(added[0]["stable_id"]) == 27, failures)

    before_read = {
        "events": table_rows(conn, "academic_change_events"),
        "state": table_rows(conn, "academic_change_event_state"),
        "materials": table_rows(conn, "materials"),
        "baselines": table_rows(conn, "canvas_file_sync_baselines"),
    }
    listed = server.list_academic_changes(conn, unseen_only=True)
    after_read = {
        "events": table_rows(conn, "academic_change_events"),
        "state": table_rows(conn, "academic_change_event_state"),
        "materials": table_rows(conn, "materials"),
        "baselines": table_rows(conn, "canvas_file_sync_baselines"),
    }
    check("whats_new_read_is_non_mutating", before_read == after_read, failures)
    check("whats_new_has_safe_local_navigation", len(listed) == 1 and isinstance(listed[0]["file_id"], int) and "path" not in listed[0], failures)

    first_event_id = str(added[0]["stable_id"])
    server.mark_academic_changes_seen(conn, [first_event_id])
    conn.commit()
    first_seen_at = conn.execute("SELECT seen_at FROM academic_change_event_state").fetchone()[0]
    server.mark_academic_changes_seen(conn, [first_event_id])
    conn.commit()
    check("mark_seen_is_idempotent", conn.execute("SELECT COUNT(*) FROM academic_change_event_state").fetchone()[0] == 1 and conn.execute("SELECT seen_at FROM academic_change_event_state").fetchone()[0] == first_seen_at, failures)

    same_bytes_new_metadata = remote_file(server, "lifecycle-file", BYTES_A, updated="2026-01-02T00:00:00Z")
    apply_one(server, conn, offering, same_bytes_new_metadata, BYTES_A)
    conn.commit()
    kinds = [row[0] for row in conn.execute("SELECT event_kind FROM academic_change_events ORDER BY id")]
    check("same_sha_potential_change_becomes_metadata_event", kinds == ["CANVAS_FILE_ADDED", "CANVAS_FILE_METADATA_UPDATED"], failures)
    check("remote_refresh_preserves_seen_state", conn.execute("SELECT seen_at FROM academic_change_event_state WHERE event_id=(SELECT id FROM academic_change_events WHERE stable_id=?)", (first_event_id,)).fetchone()[0] == first_seen_at, failures)
    check("new_change_remains_unseen", len(server.list_academic_changes(conn, unseen_only=True)) == 1, failures)

    new_bytes = remote_file(server, "lifecycle-file", BYTES_B, updated="2026-01-03T00:00:00Z")
    apply_one(server, conn, offering, new_bytes, BYTES_B)
    conn.commit()
    check("new_sha_creates_content_updated_event", conn.execute("SELECT event_kind FROM academic_change_events ORDER BY id DESC LIMIT 1").fetchone()[0] == "CANVAS_FILE_CONTENT_UPDATED", failures)

    renamed = remote_file(server, "lifecycle-file", BYTES_B, filename="renamed-synthetic.txt", updated="2026-01-03T00:00:00Z")
    apply_one(server, conn, offering, renamed, None)
    conn.commit()
    latest = conn.execute("SELECT event_kind, changed_fields_json FROM academic_change_events ORDER BY id DESC LIMIT 1").fetchone()
    check("pure_metadata_change_creates_metadata_event", latest["event_kind"] == "CANVAS_FILE_METADATA_UPDATED" and "filename" in json.loads(latest["changed_fields_json"]), failures)

    event_count = conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0]
    unchanged = apply_one(server, conn, offering, renamed, None)
    conn.commit()
    check("unchanged_creates_no_event", first_action(unchanged["plan"])["kind"] == "UNCHANGED" and conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == event_count, failures)
    missing_plan = server.canvas_file_sync_plan(conn, ScenarioConnector(server, []))
    check("missing_is_advisory_without_event", missing_plan["summary"]["MISSING_FROM_REMOTE_DISCOVERY"] == 1 and conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == event_count, failures)

    state_count = conn.execute("SELECT COUNT(*) FROM academic_change_event_state").fetchone()[0]
    malformed_safe = False
    numeric_safe = False
    unknown_safe = False
    try:
        server.mark_academic_changes_seen(conn, ["bad"])
    except ValueError:
        malformed_safe = True
    try:
        server.mark_academic_changes_seen(conn, [123])
    except ValueError:
        numeric_safe = True
    try:
        server.mark_academic_changes_seen(conn, ["change_" + "f" * 20])
    except ValueError:
        unknown_safe = True
    check("malformed_numeric_unknown_ids_fail_without_mutation", malformed_safe and numeric_safe and unknown_safe and conn.execute("SELECT COUNT(*) FROM academic_change_event_state").fetchone()[0] == state_count, failures)
    conn.close()


def check_transition_cycles(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    history = (
        (BYTES_A, "2026-02-01T00:00:00Z"),
        (BYTES_B, "2026-02-02T00:00:00Z"),
        (BYTES_A, "2026-02-03T00:00:00Z"),
        (BYTES_B, "2026-02-04T00:00:00Z"),
    )
    for body, updated in history[:2]:
        apply_one(server, conn, offering, remote_file(server, "content-cycle", body, updated=updated), body)
        conn.commit()
    rows = conn.execute("SELECT * FROM academic_change_events ORDER BY id").fetchall()
    first_b_id = str(rows[1]["stable_id"])
    server.mark_academic_changes_seen(conn, [first_b_id])
    conn.commit()
    first_b_seen_at = conn.execute(
        "SELECT seen_at FROM academic_change_event_state WHERE event_id=?",
        (rows[1]["id"],),
    ).fetchone()[0]
    for body, updated in history[2:]:
        apply_one(server, conn, offering, remote_file(server, "content-cycle", body, updated=updated), body)
        conn.commit()
    rows = conn.execute("SELECT * FROM academic_change_events ORDER BY id").fetchall()
    ids = [str(row["stable_id"]) for row in rows]
    check("content_cycle_records_four_distinct_transitions", len(rows) == 4 and len(set(ids)) == 4, failures)
    check("content_cycle_classifies_reused_versions_as_updates", [row["event_kind"] for row in rows] == ["CANVAS_FILE_ADDED"] + ["CANVAS_FILE_CONTENT_UPDATED"] * 3, failures)
    check("content_cycle_predecessor_chain_is_complete", rows[0]["predecessor_event_stable_id"] is None and all(rows[index]["predecessor_event_stable_id"] == rows[index - 1]["stable_id"] for index in range(1, 4)), failures)
    check("content_cycle_can_reuse_material_versions_without_collapsing_events", rows[1]["resulting_material_version_stable_id"] == rows[3]["resulting_material_version_stable_id"] and rows[1]["stable_id"] != rows[3]["stable_id"], failures)
    unseen_ids = {item["event_id"] for item in server.list_academic_changes(conn, unseen_only=True)}
    check("acknowledgement_cycle_keeps_first_seen_and_later_unseen", conn.execute("SELECT seen_at FROM academic_change_event_state WHERE event_id=?", (rows[1]["id"],)).fetchone()[0] == first_b_seen_at and rows[3]["stable_id"] in unseen_ids and rows[1]["stable_id"] not in unseen_ids, failures)
    conn.close()


def check_metadata_cycle(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    filenames = ("filename-a.txt", "filename-b.txt", "filename-a.txt", "filename-b.txt")
    for index, filename in enumerate(filenames[:2]):
        apply_one(server, conn, offering, remote_file(server, "metadata-cycle", BYTES_A, filename=filename), BYTES_A if index == 0 else None)
        conn.commit()
    first_b = conn.execute("SELECT * FROM academic_change_events ORDER BY id DESC LIMIT 1").fetchone()
    server.mark_academic_changes_seen(conn, [str(first_b["stable_id"])])
    conn.commit()
    first_b_seen_at = conn.execute(
        "SELECT seen_at FROM academic_change_event_state WHERE event_id=?",
        (first_b["id"],),
    ).fetchone()[0]
    for filename in filenames[2:]:
        apply_one(server, conn, offering, remote_file(server, "metadata-cycle", BYTES_A, filename=filename), None)
        conn.commit()
    rows = conn.execute("SELECT * FROM academic_change_events ORDER BY id").fetchall()
    metadata_rows = rows[1:]
    check("metadata_cycle_records_each_committed_transition", len(rows) == 4 and all(row["event_kind"] == "CANVAS_FILE_METADATA_UPDATED" for row in metadata_rows), failures)
    check("metadata_cycle_repeated_state_has_distinct_identity", len({row["stable_id"] for row in rows}) == 4 and metadata_rows[0]["resulting_material_version_stable_id"] == metadata_rows[2]["resulting_material_version_stable_id"], failures)
    check("metadata_cycle_predecessor_chain_is_complete", rows[0]["predecessor_event_stable_id"] is None and all(rows[index]["predecessor_event_stable_id"] == rows[index - 1]["stable_id"] for index in range(1, 4)), failures)
    unseen_ids = {item["event_id"] for item in server.list_academic_changes(conn, unseen_only=True)}
    check("metadata_cycle_seen_state_does_not_transfer", conn.execute("SELECT seen_at FROM academic_change_event_state WHERE event_id=?", (first_b["id"],)).fetchone()[0] == first_b_seen_at and rows[3]["stable_id"] in unseen_ids and first_b["stable_id"] not in unseen_ids, failures)
    count_before = len(rows)
    unchanged = apply_one(server, conn, offering, remote_file(server, "metadata-cycle", BYTES_A, filename="filename-b.txt"), None)
    conn.commit()
    check("metadata_cycle_unchanged_does_not_advance_chain", first_action(unchanged["plan"])["kind"] == "UNCHANGED" and conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == count_before, failures)
    conn.close()


def check_real_retry_idempotence(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    initial = remote_file(server, "retry-file", BYTES_A, updated="2026-03-01T00:00:00Z")
    apply_one(server, conn, offering, initial, BYTES_A)
    conn.commit()
    predecessor_id = str(conn.execute("SELECT stable_id FROM academic_change_events").fetchone()[0])
    changed = remote_file(server, "retry-file", BYTES_B, updated="2026-03-02T00:00:00Z")
    generated_ids: list[str] = []
    original_record = server.record_academic_change_event

    def capture_record(*args: Any, **kwargs: Any) -> str:
        event_id = original_record(*args, **kwargs)
        generated_ids.append(event_id)
        return event_id

    server.record_academic_change_event = capture_record
    failed = False
    try:
        apply_one(server, conn, offering, changed, BYTES_B, fail_after_event=1)
    except RuntimeError as exc:
        failed = str(exc) == "synthetic_academic_event_failure"
    finally:
        conn.rollback()
    check("real_retry_forces_failure_after_event_insert", failed and len(generated_ids) == 1, failures)
    check("real_retry_rollback_removes_transition", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 1, failures)
    apply_one(server, conn, offering, changed, BYTES_B)
    conn.commit()
    server.record_academic_change_event = original_record
    rows = conn.execute("SELECT * FROM academic_change_events ORDER BY id").fetchall()
    check("real_retry_recreates_identical_event_id_once", len(generated_ids) == 2 and generated_ids[0] == generated_ids[1] and len(rows) == 2, failures)
    check("real_retry_uses_same_committed_predecessor", rows[1]["predecessor_event_stable_id"] == predecessor_id, failures)

    conflict_rejected = False
    try:
        original_record(
            conn,
            event_kind=str(rows[1]["event_kind"]),
            offering=offering,
            remote_item_stable_id=str(rows[1]["remote_item_stable_id"]),
            predecessor_event_stable_id=str(rows[1]["predecessor_event_stable_id"]),
            material_stable_id=str(rows[1]["material_stable_id"]),
            material_label="different-synthetic-label.txt",
            changed_fields=json.loads(rows[1]["changed_fields_json"]),
            resulting_revision_id=str(rows[1]["resulting_material_version_stable_id"]),
            resulting_material_version_stable_id=str(rows[1]["resulting_material_version_stable_id"]),
            resulting_sha256=str(rows[1]["resulting_sha256"]),
            applied_at="2026-03-03T00:00:00Z",
        )
    except RuntimeError as exc:
        conflict_rejected = str(exc) == "academic_change_event_identity_conflict"
    check("same_id_different_fact_is_rejected", conflict_rejected and conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 2, failures)
    conn.rollback()
    conn.close()


def check_multi_file_atomicity(server: Any, failing_server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    offering = seed_offering(server, conn)
    files = [
        remote_file(server, "multi-a", BYTES_A, filename="multi-a.txt"),
        remote_file(server, "multi-b", BYTES_B, filename="multi-b.txt"),
    ]
    connector = ScenarioConnector(server, files)
    plan = server.canvas_file_sync_plan(conn, connector)
    stable_ids = [str(action["remote_file_stable_id"]) for action in plan["offerings"][0]["actions"]]
    server.apply_canvas_file_sync(
        conn,
        connector,
        plan["plan_revision"],
        stable_ids,
        requested_offering_ids=[str(offering["stable_id"])],
        open_url=DownloadOpen([BytesResponse(BYTES_A), BytesResponse(BYTES_B)]),
    )
    conn.commit()
    check("successful_multi_file_apply_creates_two_events", conn.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 2, failures)
    initial_rows = conn.execute("SELECT * FROM academic_change_events ORDER BY remote_item_stable_id").fetchall()
    updated_files = [
        remote_file(server, "multi-a", BYTES_A, filename="multi-a-renamed.txt"),
        remote_file(server, "multi-b", BYTES_B, filename="multi-b-renamed.txt"),
    ]
    connector = ScenarioConnector(server, updated_files)
    plan = server.canvas_file_sync_plan(conn, connector)
    stable_ids = [str(action["remote_file_stable_id"]) for action in plan["offerings"][0]["actions"]]
    server.apply_canvas_file_sync(
        conn,
        connector,
        plan["plan_revision"],
        stable_ids,
        requested_offering_ids=[str(offering["stable_id"])],
        open_url=DownloadOpen([]),
    )
    conn.commit()
    all_rows = conn.execute("SELECT * FROM academic_change_events ORDER BY remote_item_stable_id, id").fetchall()
    chains: dict[str, list[sqlite3.Row]] = {}
    for row in all_rows:
        chains.setdefault(str(row["remote_item_stable_id"]), []).append(row)
    check("multi_file_predecessor_chains_are_independent", len(chains) == 2 and all(len(chain) == 2 and chain[0]["predecessor_event_stable_id"] is None and chain[1]["predecessor_event_stable_id"] == chain[0]["stable_id"] for chain in chains.values()) and {str(row["stable_id"]) for row in initial_rows} == {str(chain[0]["stable_id"]) for chain in chains.values()}, failures)
    conn.close()

    failing = failing_server.connect_db()
    failing_server.init_db(failing)
    offering = seed_offering(failing_server, failing)
    failing_files = [
        remote_file(failing_server, "multi-a", BYTES_A, filename="multi-a.txt"),
        remote_file(failing_server, "multi-b", BYTES_B, filename="multi-b.txt"),
    ]
    connector = ScenarioConnector(failing_server, failing_files)
    plan = failing_server.canvas_file_sync_plan(failing, connector)
    stable_ids = [str(action["remote_file_stable_id"]) for action in plan["offerings"][0]["actions"]]
    failed = False
    try:
        failing_server.apply_canvas_file_sync(
            failing,
            connector,
            plan["plan_revision"],
            stable_ids,
            requested_offering_ids=[str(offering["stable_id"])],
            open_url=DownloadOpen([BytesResponse(BYTES_A), BytesResponse(BYTES_B)]),
            fail_after_event=2,
        )
    except RuntimeError:
        failed = True
    check("second_event_failure_rolls_back_full_db_apply", failed and failing.execute("SELECT COUNT(*) FROM academic_change_events").fetchone()[0] == 0 and failing.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0 and failing.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 0, failures)
    failing.close()


def check_offline_http_api(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    rows = conn.execute("SELECT stable_id FROM academic_change_events ORDER BY id").fetchall()
    conn.close()
    target = str(rows[-1]["stable_id"])
    original_connector = server.with_canvas_connector
    server.with_canvas_connector = lambda _callback: (_ for _ in ()).throw(RuntimeError("network_must_not_run"))
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/whats-new?unseen_only=1&limit=10", timeout=5) as response:
            payload = json.loads(response.read())
        check("offline_whats_new_api_lists_without_connector", response.status == 200 and isinstance(payload["events"], list), failures)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/whats-new/seen",
            data=json.dumps({"event_ids": [target]}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-StudyHub-CSRF": server.CSRF_TOKEN,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            marked = json.loads(response.read())
        check("offline_mark_seen_api_uses_explicit_ids", response.status == 200 and marked == {"ok": True, "marked": 1}, failures)

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/whats-new/seen",
            data=json.dumps({"event_ids": [123]}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-StudyHub-CSRF": server.CSRF_TOKEN,
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(bad, timeout=5)
            bad_status = 200
        except urllib.error.HTTPError as exc:
            bad_status = exc.code
        check("mark_seen_api_rejects_numeric_ids", bad_status == 400, failures)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        server.with_canvas_connector = original_connector


def run() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-academic-journal-") as raw:
        root = Path(raw)

        migration_server = load_server(root / "migration")
        check_upgrade_migration(migration_server, failures)

        transaction_server = load_server(root / "transaction")
        check_transaction_failures(transaction_server, failures)

        lifecycle_server = load_server(root / "lifecycle")
        check_event_lifecycle(lifecycle_server, failures)
        check_offline_http_api(lifecycle_server, failures)

        cycle_server = load_server(root / "content-cycle")
        check_transition_cycles(cycle_server, failures)

        metadata_cycle_server = load_server(root / "metadata-cycle")
        check_metadata_cycle(metadata_cycle_server, failures)

        retry_server = load_server(root / "real-retry")
        check_real_retry_idempotence(retry_server, failures)

        multi_success_server = load_server(root / "multi-success")
        multi_failure_server = load_server(root / "multi-failure")
        check_multi_file_atomicity(multi_success_server, multi_failure_server, failures)

    if failures:
        print("Academic change journal acceptance: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Academic change journal acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
