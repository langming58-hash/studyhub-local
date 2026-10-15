#!/usr/bin/env python3
"""Synthetic acceptance checks for Canvas file-manifest sync planning."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://canvas.example.invalid"
OTHER_ORIGIN = "https://canvas-alt.example.invalid"
TOKEN = "syn-token"
BIG_COURSE_ID = "9007199254740995"
BIG_FILE_ID = "9007199254741999"
URL_CANARY = "https://storage.example.invalid/object?verifier=secret-canary"
RAW_METADATA_CANARY = "private-upstream-metadata-canary.pdf"
STATE_ENV = (
    "DATABASE_PATH",
    "OPENAI_API_KEY",
    "OPENAI_VECTOR_STORE_ID",
    "STUDYHUB_CACHE_DIR",
    "STUDYHUB_CONFIG_PATH",
    "STUDYHUB_DATA_DIR",
    "STUDYHUB_LOG_DIR",
    "STUDYHUB_RUNTIME_DIR",
    "STUDYHUB_RUNTIME_PROFILE",
    "STUDY_LIBRARY_PATH",
)


def load_server(tmp: Path):
    for key in STATE_ENV:
        os.environ.pop(key, None)
    runtime = tmp / "runtime"
    library = tmp / "library"
    os.environ.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": "production",
            "STUDY_LIBRARY_PATH": str(library),
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "DATABASE_PATH": str(runtime / "studyhub.sqlite"),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    module_name = f"studyhub_canvas_sync_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA_DIR = runtime / "data"
    module.CACHE_DIR = runtime / "cache"
    module.TEXT_CACHE_DIR = module.CACHE_DIR / "text"
    module.LOG_DIR = runtime / "logs"
    module.DB_PATH = runtime / "studyhub.sqlite"
    module.ENV_LOCAL_PATH = runtime / "settings.env"
    module.ENV_LOCAL_EXISTS = False
    module.DEFAULT_STUDY_ROOT = library
    module.ensure_dirs()
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def raises_code(expected: str, action) -> bool:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - acceptance verifies sanitized public codes
        rendered = f"{exc!s}\n{exc!r}"
        return (
            str(exc) == expected
            and TOKEN not in rendered
            and URL_CANARY not in rendered
            and RAW_METADATA_CANARY not in rendered
        )
    return False


class FakeCanvasHTTP:
    def __init__(self, server: Any, responses: list[Any]):
        self.server = server
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], int]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: int):
        self.requests.append((url, dict(headers), timeout))
        if not self.responses:
            raise self.server.CanvasConnectorError("transport_failure")
        return self.responses.pop(0)


def response(server: Any, status: int, payload: Any, headers: dict[str, str] | None = None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return server.CanvasHTTPResponse(status=status, headers=headers or {}, body=body)


def file_payload(
    remote_file_id: str = BIG_FILE_ID,
    *,
    filename: str = "synthetic-notes.pdf",
    size: int = 1200,
    updated_at: str = "2026-01-02T00:00:00Z",
    modified_at: str = "2026-01-02T00:00:00Z",
    locked: bool = False,
    hidden: bool = False,
    hidden_for_user: bool = False,
) -> dict[str, Any]:
    return {
        "id": remote_file_id,
        "folder_id": "9007199254742999",
        "display_name": filename,
        "filename": filename,
        "content-type": "application/pdf",
        "size": size,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": updated_at,
        "modified_at": modified_at,
        "locked": locked,
        "hidden": hidden,
        "hidden_for_user": hidden_for_user,
        "url": URL_CANARY,
        "preview_url": URL_CANARY + "&preview=1",
        "thumbnail_url": URL_CANARY + "&thumbnail=1",
    }


def connector_with(server: Any, responses: list[Any], *, max_pages: int = 10, max_items: int = 500):
    fake = FakeCanvasHTTP(server, responses)
    record = server.CanvasConnectionRecord(ORIGIN, TOKEN)
    connector = server.CanvasConnector(record, http_get=fake, max_pages=max_pages, max_items=max_items)
    return connector, fake


def seed_selected_offering(server: Any, conn: sqlite3.Connection, *, authority_id: str | None = None, remote_id: str = BIG_COURSE_ID):
    authority = authority_id or server.canvas_authority_id(ORIGIN)
    course = server.CanvasCourse(
        remote_course_id=remote_id,
        name="Synthetic Course",
        course_code="TEST4100",
        workflow_state="available",
        start_at="2026-01-01T00:00:00Z",
        end_at=None,
        term_id="synthetic-term",
        term_name="Synthetic Term",
    )
    row = server.upsert_canvas_course_offering(conn, authority, course)
    conn.execute(
        "INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at) VALUES (?, 1, ?, ?)",
        (row["id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    return conn.execute("SELECT * FROM course_offerings WHERE id=?", (row["id"],)).fetchone()


def normalized(server: Any, payload: dict[str, Any], course_id: str = BIG_COURSE_ID):
    return server.normalize_canvas_file_object(payload, course_id)


def action(plan: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for offering in plan["offerings"] for item in offering["actions"] if item["kind"] == kind)


def snapshot_tables(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in tables}


def seed_pr23_existing_data(conn: sqlite3.Connection) -> None:
    now = "2026-01-01T00:00:00+00:00"
    digest = hashlib.sha256(b"synthetic pre-pr24 material").hexdigest()
    conn.execute(
        "INSERT INTO terms(id, stable_id, name, archived, sort_order, created_at, updated_at) VALUES (10, 'term_pre24', 'Synthetic Pre24 Term', 0, 0, ?, ?)",
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO courses(id, code, name, folder_name, path, created_at, updated_at, stable_id,
          display_name, course_code, term_id, source_folder, source_kind, active)
        VALUES (20, 'TEST9100', 'Synthetic Pre24 Course', 'TEST9100 - Pre24', '/synthetic/pre24',
          ?, ?, 'course_pre24', 'Synthetic Pre24 Course', 'TEST9100', 10, '/synthetic/pre24', 'folder', 1)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO weeks(id, course_id, week_label, week_number, path, has_materials, file_count,
          stable_id, kind, origin, created_at, updated_at)
        VALUES (30, 20, 'Week 01', 1, '/synthetic/pre24/Week 01', 1, 1,
          'week_pre24', 'week', 'scan', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO files(id, course_id, week_id, course_code, week_label, section, category,
          exercise_type, filename, original_path, rel_path, source, source_label, hash, size,
          modified_at, indexed_at, extension, mime_type, stable_id, course_name, absolute_path,
          source_type, file_extension, file_size, sha256, display_name, material_type,
          import_mode, active, material_created_at, material_updated_at)
        VALUES (40, 20, 30, 'TEST9100', 'Week 01', '01 Course Materials', 'Lecture', '',
          'pre24.txt', '/synthetic/pre24/pre24.txt', 'TEST9100/Week 01/pre24.txt',
          'official', 'Synthetic material', ?, 27, ?, ?, '.txt', 'text/plain',
          'material_pre24', 'Synthetic Pre24 Course', '/synthetic/pre24/pre24.txt',
          'Local Reference', '.txt', 27, ?, 'pre24.txt', 'lecture', 'reference', 1, ?, ?)
        """,
        (digest, now, now, digest, now, now),
    )
    conn.execute(
        "INSERT INTO file_versions(id, file_id, stable_id, sha256, file_size, modified_at, indexed_at, active) VALUES (50, 40, 'file_version_pre24', ?, 27, ?, ?, 1)",
        (digest, now, now),
    )
    conn.execute(
        """
        INSERT INTO sources(id, stable_id, provider_kind, authority, source_label, source_type,
          import_mode, local_locator, created_at, updated_at)
        VALUES (60, 'source_pre24', 'local', 'USER_OWNED', 'Synthetic material',
          'Local Reference', 'reference', '/synthetic/pre24/pre24.txt', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        "INSERT INTO blobs(id, stable_id, sha256, byte_size, mime_type, extension, local_locator, created_at, updated_at) VALUES (70, 'blob_pre24', ?, 27, 'text/plain', '.txt', '', ?, ?)",
        (digest, now, now),
    )
    conn.execute(
        """
        INSERT INTO materials(id, stable_id, legacy_file_id, course_id, week_id, course_code,
          course_name, week_label, week_number, material_type, display_name, active,
          current_version_id, created_at, updated_at)
        VALUES (80, 'material_pre24', 40, 20, 30, 'TEST9100', 'Synthetic Pre24 Course',
          'Week 01', 1, 'lecture', 'pre24.txt', 1, 90, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO material_versions(id, stable_id, material_id, source_id, blob_id,
          legacy_file_id, legacy_file_version_id, sha256, byte_size, modified_at,
          indexed_at, active, created_at, updated_at)
        VALUES (90, 'material_version_pre24', 80, 60, 70, 40, 50, ?, 27, ?, ?, 1, ?, ?)
        """,
        (digest, now, now, now, now),
    )
    conn.commit()


def check_migration(server: Any, tmp: Path, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    seed_pr23_existing_data(conn)
    offering = seed_selected_offering(server, conn)
    conn.execute("DROP TABLE canvas_file_sync_baselines")
    conn.execute("DELETE FROM schema_migrations WHERE version=3")
    conn.commit()
    before = snapshot_tables(
        conn,
        ("terms", "courses", "weeks", "files", "file_versions", "sources", "blobs", "materials", "material_versions", "course_offerings", "course_offering_selections"),
    )
    server.run_schema_migrations(conn)
    migration_count = conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0]
    after = snapshot_tables(conn, tuple(before))
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    server.run_schema_migrations(conn)
    after_rerun = snapshot_tables(conn, tuple(before))
    check("pr23_to_pr24_migration_preserves_existing_rows", before == after, failures)
    check("sync_baseline_migration_runs_once", migration_count == 1 and conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0] == 1, failures)
    check("sync_baseline_migration_rerun_preserves_existing_rows", before == after_rerun, failures)
    check("sync_baseline_migration_fk_integrity", not fk_errors, failures)
    check("sync_baseline_table_and_index_created", bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canvas_file_sync_baselines'").fetchone()) and bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_canvas_file_baselines_offering'").fetchone()), failures)
    check("seeded_course_offering_survives_migration", conn.execute("SELECT stable_id FROM course_offerings WHERE id=?", (offering["id"],)).fetchone()[0] == offering["stable_id"], failures)
    conn.close()


def check_connector(server: Any, failures: list[str]) -> None:
    path_id = "course/../files?x=1"
    connector, fake = connector_with(server, [response(server, 200, [file_payload()])])
    files = connector.list_course_files(path_id)
    request_url, headers, _timeout = fake.requests[0]
    check("selected_offering_file_listing_succeeds", len(files) == 1 and files[0].remote_file_id == BIG_FILE_ID, failures)
    check("course_file_endpoint_is_get_only", request_url.startswith(f"{ORIGIN}/api/v1/courses/") and request_url.endswith("/files?per_page=50"), failures)
    check("course_id_path_segment_is_encoded", "course%2F..%2Ffiles%3Fx%3D1" in request_url, failures)
    check("path_sensitive_course_id_cannot_escape_endpoint", request_url.count("/files") == 1 and "/../" not in request_url, failures)
    check("large_file_id_preserved_as_string", files[0].remote_file_id == BIG_FILE_ID and isinstance(files[0].remote_file_id, str), failures)
    check("bearer_header_preserved_and_token_absent_from_url", headers.get("Authorization") == f"Bearer {TOKEN}" and TOKEN not in request_url, failures)

    next_url = f"{ORIGIN}/api/v1/courses/{BIG_COURSE_ID}/files?page=opaque"
    paged, paged_http = connector_with(
        server,
        [
            response(server, 200, [file_payload("one")], {"lInK": f'<{next_url}>; rel="next"'}),
            response(server, 200, [file_payload("two")]),
        ],
    )
    check("file_pagination_follows_case_insensitive_rel_next", [item.remote_file_id for item in paged.list_course_files(BIG_COURSE_ID)] == ["one", "two"] and paged_http.requests[1][0] == next_url, failures)

    duplicate_identical, _ = connector_with(
        server,
        [response(server, 200, [file_payload("duplicate"), file_payload("duplicate")])],
    )
    check("same_page_identical_file_id_rejected", raises_code("duplicate_canvas_file_id", lambda: duplicate_identical.list_course_files(BIG_COURSE_ID)), failures)
    conflicting_first = file_payload("conflict", filename=RAW_METADATA_CANARY, size=1200)
    conflicting_second = file_payload("conflict", filename="different.pdf", size=2400)
    duplicate_conflicting, _ = connector_with(server, [response(server, 200, [conflicting_first, conflicting_second])])
    check("same_page_conflicting_file_id_rejected", raises_code("duplicate_canvas_file_id", lambda: duplicate_conflicting.list_course_files(BIG_COURSE_ID)), failures)
    duplicate_reversed, _ = connector_with(server, [response(server, 200, [conflicting_second, conflicting_first])])
    check("reversed_conflicting_file_id_rejected", raises_code("duplicate_canvas_file_id", lambda: duplicate_reversed.list_course_files(BIG_COURSE_ID)), failures)
    duplicate_cross_page, _ = connector_with(
        server,
        [
            response(server, 200, [file_payload("cross-page")], {"Link": f'<{next_url}>; rel="next"'}),
            response(server, 200, [file_payload("cross-page", filename=RAW_METADATA_CANARY)]),
        ],
    )
    check("cross_page_file_id_rejected", raises_code("duplicate_canvas_file_id", lambda: duplicate_cross_page.list_course_files(BIG_COURSE_ID)), failures)
    cross, _ = connector_with(server, [response(server, 200, [], {"Link": '<https://evil.example.invalid/api/v1/files>; rel="next"'})])
    check("cross_origin_file_pagination_rejected", raises_code("cross_origin_pagination", lambda: cross.list_course_files(BIG_COURSE_ID)), failures)
    first_url = f"{ORIGIN}/api/v1/courses/{BIG_COURSE_ID}/files?per_page=50"
    loop, _ = connector_with(server, [response(server, 200, [], {"Link": f'<{first_url}>; rel="next"'})])
    check("file_pagination_loop_rejected", raises_code("pagination_loop", lambda: loop.list_course_files(BIG_COURSE_ID)), failures)
    limited, _ = connector_with(server, [response(server, 200, [file_payload("one"), file_payload("two")])], max_items=1)
    check("file_item_limit_enforced", raises_code("pagination_limit_exceeded", lambda: limited.list_course_files(BIG_COURSE_ID)), failures)
    page_limited, _ = connector_with(
        server,
        [response(server, 200, [file_payload("one")], {"Link": f'<{next_url}>; rel="next"'})],
        max_pages=1,
    )
    check("file_page_limit_enforced", raises_code("pagination_limit_exceeded", lambda: page_limited.list_course_files(BIG_COURSE_ID)), failures)

    for label, bad in (
        ("missing_file_id_rejected", file_payload() | {"id": None}),
        ("numeric_file_id_rejected", file_payload() | {"id": 42}),
        ("negative_file_size_rejected", file_payload() | {"size": -1}),
        ("oversized_file_name_rejected", file_payload() | {"filename": "x" * (server.CANVAS_MAX_FILE_NAME_LENGTH + 1)}),
        ("invalid_boolean_rejected", file_payload() | {"locked": "false"}),
    ):
        bad_connector, _ = connector_with(server, [response(server, 200, [bad])])
        check(label, raises_code("malformed_canvas_response", lambda c=bad_connector: c.list_course_files(BIG_COURSE_ID)), failures)
    for status, code in ((401, "authentication_failed"), (403, "permission_denied"), (429, "rate_limited"), (503, "canvas_unavailable")):
        failed, _ = connector_with(server, [response(server, status, {"secret": URL_CANARY})])
        check(f"file_http_{status}_sanitized", raises_code(code, lambda c=failed: c.list_course_files(BIG_COURSE_ID)), failures)


def check_plans(server: Any, conn: sqlite3.Connection, offering: Any, failures: list[str]) -> None:
    table_names = ("course_offerings", "course_offering_selections", "canvas_file_sync_baselines", "sources", "blobs", "materials", "material_versions", "files", "file_versions")
    connector, _ = connector_with(server, [response(server, 200, [file_payload()])])
    before = snapshot_tables(conn, table_names)
    add_plan = server.canvas_file_sync_plan(conn, connector, [offering["stable_id"]])
    after = snapshot_tables(conn, table_names)
    check("no_baseline_classifies_add", action(add_plan, "ADD")["remote_file_id"] == BIG_FILE_ID, failures)
    check("preview_zero_mutation", before == after and conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 0, failures)
    public_text = json.dumps(add_plan, sort_keys=True)
    check("ephemeral_urls_absent_from_manifest_and_plan", URL_CANARY not in public_text and "url" not in action(add_plan, "ADD")["metadata"], failures)
    check("locked_hidden_acquisition_not_bypassed", action(add_plan, "ADD")["acquisition_eligible"] is True, failures)

    base = normalized(server, file_payload())
    server.upsert_canvas_file_sync_baselines(conn, offering, [base], applied_at="2026-01-03T00:00:00Z")
    hidden_conn = sqlite3.connect(server.DB_PATH)
    hidden_count = hidden_conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0]
    hidden_conn.close()
    check("baseline_helper_success_does_not_auto_commit", conn.in_transaction and hidden_count == 0, failures)
    conn.rollback()
    check("caller_rollback_removes_baseline_change", conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 0, failures)
    server.upsert_canvas_file_sync_baselines(conn, offering, [base], applied_at="2026-01-03T00:00:00Z")
    conn.commit()

    def plan_for(payload: list[dict[str, Any]]):
        candidate, _ = connector_with(server, [response(server, 200, payload)])
        return server.canvas_file_sync_plan(conn, candidate, [offering["stable_id"]])

    unchanged = plan_for([file_payload()])
    size_change = plan_for([file_payload(size=1300)])
    timestamp_change = plan_for([file_payload(updated_at="2026-01-04T00:00:00Z")])
    rename = plan_for([file_payload(filename="renamed.pdf")])
    visibility = plan_for([file_payload(locked=True)])
    missing = plan_for([])
    check("identical_baseline_classifies_unchanged", action(unchanged, "UNCHANGED")["remote_file_id"] == BIG_FILE_ID, failures)
    check("size_change_is_potential_content_change", "byte_size" in action(size_change, "POTENTIAL_CONTENT_CHANGE")["changed_fields"], failures)
    check("timestamp_change_is_potential_content_change", "remote_updated_at" in action(timestamp_change, "POTENTIAL_CONTENT_CHANGE")["changed_fields"], failures)
    check("filename_only_change_is_metadata_change", action(rename, "METADATA_CHANGE")["changed_fields"] == ["display_name", "filename"], failures)
    check("visibility_change_is_metadata_change_and_unavailable", action(visibility, "METADATA_CHANGE")["metadata"]["currently_unavailable"] and not action(visibility, "METADATA_CHANGE")["acquisition_eligible"], failures)
    check("missing_is_advisory", action(missing, "MISSING_FROM_REMOTE_DISCOVERY")["reason_codes"] == ["remote_discovery_missing_advisory"], failures)
    check("missing_does_not_delete_baseline_or_local_rows", conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 1 and all(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in ("sources", "blobs", "materials", "material_versions", "files", "file_versions")), failures)

    unique_items = [file_payload("a"), file_payload("b"), file_payload("c")]
    ordered_a = plan_for(unique_items)
    ordered_b = plan_for(list(reversed(unique_items)))
    ordered_c = plan_for([unique_items[1], unique_items[2], unique_items[0]])
    same_again = plan_for([file_payload()])
    check(
        "manifest_and_plan_revisions_ignore_three_item_response_order",
        len({ordered_a["remote_manifest_revision"], ordered_b["remote_manifest_revision"], ordered_c["remote_manifest_revision"]}) == 1
        and len({ordered_a["plan_revision"], ordered_b["plan_revision"], ordered_c["plan_revision"]}) == 1,
        failures,
    )
    check("identical_state_has_deterministic_plan_revision", unchanged["plan_revision"] == same_again["plan_revision"], failures)
    check("remote_change_changes_plan_revision", unchanged["plan_revision"] != size_change["plan_revision"], failures)
    old_revision = unchanged["plan_revision"]
    changed_base = normalized(server, file_payload(size=1400))
    server.upsert_canvas_file_sync_baselines(conn, offering, [changed_base], applied_at="2026-01-05T00:00:00Z")
    conn.commit()
    baseline_changed = plan_for([file_payload()])
    check("baseline_change_changes_plan_revision", old_revision != baseline_changed["plan_revision"], failures)

    stable_before = conn.execute("SELECT stable_id FROM canvas_file_sync_baselines WHERE remote_file_id=?", (BIG_FILE_ID,)).fetchone()[0]
    renamed = normalized(server, file_payload(filename="stable-rename.pdf", size=1400))
    server.upsert_canvas_file_sync_baselines(conn, offering, [renamed])
    stable_after = conn.execute("SELECT stable_id FROM canvas_file_sync_baselines WHERE remote_file_id=?", (BIG_FILE_ID,)).fetchone()[0]
    conn.rollback()
    check("remote_file_identity_survives_rename", stable_before == stable_after, failures)

    second = normalized(server, file_payload("second"))
    existing_count = conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0]
    failed = raises_code("synthetic_baseline_failure", lambda: server.upsert_canvas_file_sync_baselines(conn, offering, [base, second], fail_after=1))
    check("baseline_helper_failure_is_atomic_and_rollbackable", failed and conn.in_transaction and conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == existing_count, failures)
    conn.rollback()

    duplicate_connector, _ = connector_with(
        server,
        [response(server, 200, [file_payload("ambiguous"), file_payload("ambiguous", filename=RAW_METADATA_CANARY)])],
    )
    duplicate_before = snapshot_tables(conn, table_names)
    duplicate_failed = raises_code(
        "duplicate_canvas_file_id",
        lambda: server.canvas_file_sync_plan(conn, duplicate_connector, [offering["stable_id"]]),
    )
    duplicate_after = snapshot_tables(conn, table_names)
    check("duplicate_manifest_failure_is_zero_mutation", duplicate_failed and duplicate_before == duplicate_after, failures)

    other_authority = server.canvas_authority_id(OTHER_ORIGIN)
    other = seed_selected_offering(server, conn, authority_id=other_authority, remote_id=BIG_COURSE_ID)
    check("different_authorities_have_distinct_file_identity", server.canvas_remote_file_stable_id(offering["authority_id"], offering["stable_id"], BIG_FILE_ID) != server.canvas_remote_file_stable_id(other["authority_id"], other["stable_id"], BIG_FILE_ID), failures)
    check("other_authority_offering_rejected", raises_code("canvas_course_offering_authority_mismatch", lambda: plan_for_other(server, conn, other)), failures)
    same_authority_other_offering = seed_selected_offering(server, conn, remote_id="second-offering")
    check(
        "same_remote_file_id_in_different_offerings_does_not_alias",
        server.canvas_remote_file_stable_id(offering["authority_id"], offering["stable_id"], BIG_FILE_ID)
        != server.canvas_remote_file_stable_id(same_authority_other_offering["authority_id"], same_authority_other_offering["stable_id"], BIG_FILE_ID),
        failures,
    )

    conn.execute(
        "INSERT INTO course_offerings(stable_id, provider, authority_id, remote_course_id, remote_name, first_seen_at, last_seen_at, created_at, updated_at) VALUES ('other-provider', 'other', ?, 'opaque', 'Other', ?, ?, ?, ?)",
        (offering["authority_id"],) + ("2026-01-01T00:00:00Z",) * 4,
    )
    other_provider_id = conn.execute("SELECT id FROM course_offerings WHERE stable_id='other-provider'").fetchone()[0]
    conn.execute("INSERT INTO course_offering_selections(offering_id, selected, updated_at) VALUES (?, 1, ?)", (other_provider_id, "2026-01-01T00:00:00Z"))
    conn.commit()
    check("unknown_offering_id_rejected", raises_code("unknown_canvas_course_offering", lambda: server.selected_canvas_offerings(conn, offering["authority_id"], ["unknown"])), failures)
    check("unselected_offering_rejected", check_unselected(server, conn, offering["authority_id"]), failures)
    check("another_provider_rejected", raises_code("unsupported_course_offering_provider", lambda: server.selected_canvas_offerings(conn, offering["authority_id"], ["other-provider"])), failures)


def plan_for_other(server: Any, conn: sqlite3.Connection, offering: Any):
    connector, _ = connector_with(server, [response(server, 200, [])])
    return server.canvas_file_sync_plan(conn, connector, [offering["stable_id"]])


def check_unselected(server: Any, conn: sqlite3.Connection, authority_id: str) -> bool:
    course = server.CanvasCourse("unselected", "Unselected", "TEST4200", "available", None, None, None, None)
    row = server.upsert_canvas_course_offering(conn, authority_id, course)
    conn.execute("INSERT INTO course_offering_selections(offering_id, selected, updated_at) VALUES (?, 0, ?)", (row["id"], "2026-01-01T00:00:00Z"))
    conn.commit()
    return raises_code("canvas_course_offering_not_selected", lambda: server.selected_canvas_offerings(conn, authority_id, [row["stable_id"]]))


def check_api(server: Any, offering: Any, failures: list[str]) -> None:
    fake_connector, _ = connector_with(server, [response(server, 200, [file_payload()])])
    original = server.with_canvas_connector
    server.with_canvas_connector = lambda callback: callback(fake_connector)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/api/canvas/sync-preview"

    def post(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-StudyHub-CSRF": server.CSRF_TOKEN,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response_obj:
                return response_obj.status, json.loads(response_obj.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        status, payload = post({"offering_ids": [offering["stable_id"]]})
        check("sync_preview_api_returns_plan", status == 200 and payload.get("ok") is True and sum(payload["plan"]["summary"].values()) == 1, failures)
        duplicate_connector, _ = connector_with(
            server,
            [response(server, 200, [file_payload("api-duplicate"), file_payload("api-duplicate", filename=RAW_METADATA_CANARY)])],
        )
        server.with_canvas_connector = lambda callback: callback(duplicate_connector)
        duplicate_status, duplicate_payload = post({"offering_ids": [offering["stable_id"]]})
        check(
            "duplicate_manifest_api_error_is_sanitized",
            duplicate_status == 502
            and duplicate_payload == {"ok": False, "error": "duplicate_canvas_file_id"}
            and RAW_METADATA_CANARY not in json.dumps(duplicate_payload),
            failures,
        )
        injected_status, _ = post({"offering_ids": [offering["stable_id"]], "remote_course_id": "attacker", "files": [file_payload("attacker")]})
        check("browser_cannot_override_course_or_inject_file_metadata", injected_status == 400, failures)
        request = urllib.request.Request(url, method="GET")
        try:
            urllib.request.urlopen(request, timeout=5)
            get_status = 200
        except urllib.error.HTTPError as exc:
            get_status = exc.code
        check("sync_preview_endpoint_is_post_only", get_status == 404, failures)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        server.with_canvas_connector = original


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-canvas-sync-plan-") as raw_tmp:
        tmp = Path(raw_tmp)
        migration_server = load_server(tmp / "migration")
        check_migration(migration_server, tmp / "migration", failures)
        server = load_server(tmp / "plan")
        check_connector(server, failures)
        conn = server.connect_db()
        server.init_db(conn)
        offering = seed_selected_offering(server, conn)
        check_plans(server, conn, offering, failures)
        check_api(server, offering, failures)

        db_bytes = Path(server.DB_PATH).read_bytes()
        log_text = (server.LOG_DIR / "studyhub.log").read_text(encoding="utf-8", errors="ignore") if (server.LOG_DIR / "studyhub.log").exists() else ""
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        connector_source = source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0]
        check("url_canary_absent_from_sqlite_and_logs", URL_CANARY.encode() not in db_bytes and URL_CANARY not in log_text, failures)
        check("token_absent_from_sqlite_and_logs", TOKEN.encode() not in db_bytes and TOKEN not in log_text, failures)
        check("no_canvas_mutating_http_method_added", 'method="POST"' not in connector_source, failures)
        check("no_content_download_or_ingestion_path_added", all(term not in connector_source for term in ("preview_url", "thumbnail_url", "import_course_folder", "register_material", "create_blob")), failures)
        check("no_public_baseline_write_endpoint", "/api/canvas/sync-apply" not in source and "upsert_canvas_file_sync_baselines" not in source.split("def do_POST", 1)[1], failures)
        check("mcp_exposes_no_canvas_sync_or_secret", all("canvas" not in name.lower() and "credential" not in name.lower() for name in server.MCP_TOOLS), failures)
        check("production_manual_token_enrollment_still_blocked", "canvas_manual_token_development_only" in (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8"), failures)
        conn.close()

    if failures:
        print("Canvas sync-plan acceptance failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Canvas sync-plan acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
