#!/usr/bin/env python3
"""Synthetic acceptance checks for explicit Canvas file acquisition."""

from __future__ import annotations

import hashlib
import importlib.util
import io
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
DOWNLOAD_URL = "https://objects.example.invalid/signed/file?verifier=synthetic-secret"
TOKEN = "syn-token"
COURSE_ID = "9007199254748001"
FILE_ID = "9007199254748002"
BYTES_A = b"Synthetic Canvas file version A.\n"
BYTES_B = b"Synthetic Canvas file version B with different bytes.\n"
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
    name = f"studyhub_canvas_acquisition_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA_DIR = runtime / "data"
    module.CACHE_DIR = runtime / "cache"
    module.TEXT_CACHE_DIR = module.CACHE_DIR / "text"
    module.LOG_DIR = runtime / "logs"
    module.DB_PATH = runtime / "studyhub.sqlite"
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
    except Exception as exc:  # noqa: BLE001 - acceptance verifies safe public codes
        rendered = f"{exc!s}\n{exc!r}"
        return str(exc) == expected and TOKEN not in rendered and DOWNLOAD_URL not in rendered
    return False


def remote_file(server: Any, *, size: int, updated: str = "2026-01-01T00:00:00Z", modified: str | None = None, filename: str = "notes.txt"):
    return server.CanvasRemoteFile(
        remote_file_id=FILE_ID,
        remote_course_id=COURSE_ID,
        display_name=filename,
        filename=filename,
        content_type="text/plain",
        byte_size=size,
        remote_created_at="2026-01-01T00:00:00Z",
        remote_updated_at=updated,
        remote_modified_at=modified or updated,
        locked=False,
        hidden=False,
        hidden_for_user=False,
    )


class ScenarioConnector:
    def __init__(self, server: Any, manifest: list[Any], lookups: list[Any] | None = None):
        self.server = server
        self.record = server.CanvasConnectionRecord(ORIGIN, TOKEN)
        self.manifest = manifest
        self.lookups = list(lookups or [])
        self.lookup_calls: list[tuple[str, str]] = []

    def list_course_files(self, remote_course_id: str):
        assert remote_course_id == COURSE_ID
        return list(self.manifest)

    def get_course_file(self, remote_course_id: str, remote_file_id: str):
        self.lookup_calls.append((remote_course_id, remote_file_id))
        item = self.lookups.pop(0) if self.lookups else next(file for file in self.manifest if file.remote_file_id == remote_file_id)
        return self.server.CanvasFileAcquisitionMetadata(item, DOWNLOAD_URL)


class BytesResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class DownloadOpen:
    def __init__(self, responses: list[BytesResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, timeout: int):
        self.calls.append((url, timeout))
        if not self.responses:
            raise RuntimeError("synthetic_download_missing")
        return self.responses.pop(0)


def seed_offering(server: Any, conn: sqlite3.Connection, *, selected: bool = True, authority_id: str | None = None):
    authority = authority_id or server.canvas_authority_id(ORIGIN)
    course = server.CanvasCourse(COURSE_ID, "Synthetic Remote Course", "TEST9200", "available", None, None, None, None)
    offering = server.upsert_canvas_course_offering(conn, authority, course)
    conn.execute(
        "INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at) VALUES (?, ?, ?, ?)",
        (offering["id"], int(selected), "2026-01-01T00:00:00Z" if selected else None, "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    return conn.execute("SELECT * FROM course_offerings WHERE id=?", (offering["id"],)).fetchone()


def plan_for(server: Any, conn: sqlite3.Connection, connector: ScenarioConnector):
    return server.canvas_file_sync_plan(conn, connector)


def stable_id_from_plan(plan: dict[str, Any]) -> str:
    return str(plan["offerings"][0]["actions"][0]["remote_file_stable_id"])


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = (
        "course_offering_local_bindings",
        "canvas_file_sync_baselines",
        "files",
        "file_versions",
        "sources",
        "blobs",
        "materials",
        "material_versions",
    )
    return {name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}


def check_official_lookup(server: Any, failures: list[str]) -> None:
    payload = {
        "id": "file/opaque",
        "display_name": "notes.txt",
        "filename": "notes.txt",
        "content-type": "text/plain",
        "size": len(BYTES_A),
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "modified_at": "2026-01-01T00:00:00Z",
        "locked": False,
        "hidden": False,
        "hidden_for_user": False,
        "url": DOWNLOAD_URL,
    }
    requests: list[tuple[str, dict[str, str], int]] = []

    def http_get(url: str, headers: dict[str, str], timeout: int):
        requests.append((url, headers, timeout))
        return server.CanvasHTTPResponse(200, {}, json.dumps(payload).encode())

    connector = server.CanvasConnector(server.CanvasConnectionRecord(ORIGIN, TOKEN), http_get=http_get)
    result = connector.get_course_file("course/opaque", "file/opaque")
    check("authoritative_file_lookup_uses_bearer", requests[0][1].get("Authorization") == f"Bearer {TOKEN}", failures)
    check("authoritative_lookup_encodes_both_ids", "course%2Fopaque/files/file%2Fopaque" in requests[0][0], failures)
    check("authoritative_lookup_validates_expected_id", result.remote_file.remote_file_id == "file/opaque", failures)


def check_download_security(server: Any, tmp: Path, failures: list[str]) -> None:
    unsafe = (
        "http://objects.example.invalid/file",
        "https://" + "user" + ":" + "pass" + "@objects.example.invalid/file",
        "https://127.0.0.1/file",
        "https://10.0.0.4/file",
        "https://169.254.1.2/file",
        "https://[::1]/file",
    )
    check("unsafe_download_targets_rejected", all(raises_code("unsafe_canvas_download_url", lambda value=value: server.validate_canvas_download_url(value)) for value in unsafe), failures)

    redirect_open = DownloadOpen(
        [
            BytesResponse(b"", 302, {"Location": "https://cdn.example.invalid/final"}),
            BytesResponse(BYTES_A),
        ]
    )
    target = tmp / "redirect.txt"
    digest, size = server.stream_canvas_download(DOWNLOAD_URL, target, declared_size=len(BYTES_A), open_url=redirect_open)
    check("cross_origin_redirect_downloads_without_auth_surface", len(redirect_open.calls) == 2 and digest == hashlib.sha256(BYTES_A).hexdigest() and size == len(BYTES_A), failures)
    unsafe_redirect = DownloadOpen([BytesResponse(b"", 302, {"Location": "https://127.0.0.1/private"})])
    check("redirect_to_unsafe_target_rejected", raises_code("unsafe_canvas_download_url", lambda: server.stream_canvas_download(DOWNLOAD_URL, tmp / "unsafe.txt", declared_size=0, open_url=unsafe_redirect)), failures)
    loop_responses = [BytesResponse(b"", 302, {"Location": f"https://cdn.example.invalid/{index}"}) for index in range(server.CANVAS_MAX_DOWNLOAD_REDIRECTS + 1)]
    check("download_redirect_limit_enforced", raises_code("canvas_download_redirect_limit", lambda: server.stream_canvas_download(DOWNLOAD_URL, tmp / "loop.txt", declared_size=0, open_url=DownloadOpen(loop_responses))), failures)
    no_open = DownloadOpen([])
    check("declared_oversize_rejected_before_download", raises_code("canvas_file_too_large", lambda: server.stream_canvas_download(DOWNLOAD_URL, tmp / "declared.txt", declared_size=6, max_bytes=5, open_url=no_open)) and not no_open.calls, failures)
    over_path = tmp / "stream-over.txt"
    check("streamed_oversize_removes_partial", raises_code("canvas_file_too_large", lambda: server.stream_canvas_download(DOWNLOAD_URL, over_path, declared_size=None, max_bytes=5, open_url=DownloadOpen([BytesResponse(b"123456")]))) and not over_path.exists(), failures)
    mismatch_path = tmp / "mismatch.txt"
    check("streamed_size_mismatch_removes_partial", raises_code("canvas_file_size_mismatch", lambda: server.stream_canvas_download(DOWNLOAD_URL, mismatch_path, declared_size=7, open_url=DownloadOpen([BytesResponse(b"short")]))) and not mismatch_path.exists(), failures)


def check_apply_and_versions(server: Any, conn: sqlite3.Connection, offering: sqlite3.Row, failures: list[str]) -> None:
    term = server.ensure_term(conn, "Imported Courses", stable_id="term_imported")
    now = server.now_iso()
    conn.execute(
        """
        INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
          source_folder, source_kind, term_id, archived, active, created_at, updated_at)
        VALUES ('local_collision', 'TEST9200', 'TEST9200', 'Synthetic Remote Course',
          'Synthetic Remote Course', 'local-collision', '/synthetic/local-collision',
          '/synthetic/local-collision', 'managed', ?, 0, 1, ?, ?)
        """,
        (term["id"], now, now),
    )
    local_course_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    first_remote = remote_file(server, size=len(BYTES_A))
    connector = ScenarioConnector(server, [first_remote], [first_remote, first_remote])
    plan = plan_for(server, conn, connector)
    remote_stable_id = stable_id_from_plan(plan)
    opener = DownloadOpen([BytesResponse(BYTES_A)])
    result = server.apply_canvas_file_sync(conn, connector, plan["plan_revision"], [remote_stable_id], open_url=opener)
    hidden = sqlite3.connect(server.DB_PATH)
    hidden_count = hidden.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    hidden.close()
    check("successful_apply_is_caller_transaction_owned", conn.in_transaction and hidden_count == 0, failures)
    conn.commit()
    visible = sqlite3.connect(server.DB_PATH)
    visible_count = visible.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    visible.close()
    check("caller_commit_publishes_apply_atomically", visible_count == 1, failures)
    source = conn.execute("SELECT * FROM sources").fetchone()
    material = conn.execute("SELECT * FROM materials").fetchone()
    blob_a = conn.execute("SELECT * FROM blobs").fetchone()
    version_a = conn.execute("SELECT * FROM material_versions").fetchone()
    retained_a = Path(source["local_locator"])
    binding = conn.execute("SELECT * FROM course_offering_local_bindings").fetchone()
    bound_course = conn.execute("SELECT * FROM courses WHERE id=?", (binding["legacy_course_id"],)).fetchone()
    canvas_week = conn.execute("SELECT * FROM weeks WHERE course_id=?", (bound_course["id"],)).fetchone()
    check("canvas_source_is_remote_authoritative", source["provider_kind"] == "canvas" and source["authority"] == "REMOTE_AUTHORITATIVE", failures)
    check("canvas_source_has_safe_remote_identity", source["remote_id"] == FILE_ID and source["connector_id"] == offering["authority_id"] and not source["remote_url"], failures)
    check("retained_path_is_profile_scoped_and_content_addressed", server.is_inside(retained_a, server.canvas_remote_content_root()) and retained_a.name == f"{hashlib.sha256(BYTES_A).hexdigest()}.txt", failures)
    check("remote_course_metadata_is_absent_from_retained_path", offering["remote_name"] not in str(retained_a) and offering["remote_course_code"] not in str(retained_a), failures)
    file_row = conn.execute("SELECT * FROM files WHERE stable_id=?", (remote_stable_id,)).fetchone()
    check("canvas_file_uses_normal_get_file_path", server.get_file(conn, int(file_row["id"]))["stable_id"] == remote_stable_id, failures)
    check("exact_canvas_retained_path_is_allowed", server.material_path_allowed(file_row, retained_a), failures)
    data_sibling = server.DATA_DIR / "unrelated.txt"
    data_sibling.write_bytes(b"unrelated synthetic data")
    cache_file = server.CACHE_DIR / "unrelated.txt"
    cache_file.write_bytes(b"unrelated synthetic cache")
    check("arbitrary_data_dir_path_is_rejected", not server.material_path_allowed(file_row, data_sibling), failures)
    check("arbitrary_cache_path_is_rejected", not server.material_path_allowed(file_row, cache_file), failures)
    outside = server.DATA_DIR / "outside-remote-content.txt"
    outside.write_bytes(BYTES_A)
    conn.execute("UPDATE files SET original_path=?, absolute_path=? WHERE id=?", (str(outside), str(outside), file_row["id"]))
    check("persisted_canvas_path_outside_retained_root_is_rejected", raises_code("file outside StudyHub material allowlist", lambda: server.get_file(conn, int(file_row["id"]))), failures)
    fake_canvas_row = {"import_mode": "canvas_remote", "original_path": str(outside)}
    check("fake_canvas_row_outside_retained_root_is_rejected", not server.material_path_allowed(fake_canvas_row, outside), failures)
    conn.execute("UPDATE files SET original_path=?, absolute_path=? WHERE id=?", (str(retained_a), str(retained_a), file_row["id"]))
    conn.commit()
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    preview_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    preview_thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/preview/{file_row['id']}", timeout=5) as response:
            preview_body = response.read()
            preview_status = response.status
        check("canvas_retained_text_uses_normal_preview_path", preview_status == 200 and BYTES_A.strip() in preview_body, failures)
    finally:
        httpd.shutdown()
        httpd.server_close()
        preview_thread.join(timeout=5)
    mcp_file_id = f"file_{str(file_row['stable_id'] or file_row['sha256'])[:24]}"
    mcp_payload = server.mcp_read_file({"file_id": mcp_file_id, "limit": 2000})
    check("readonly_mcp_can_read_explicitly_acquired_canvas_material", BYTES_A.decode().strip() in mcp_payload["content"] and DOWNLOAD_URL not in json.dumps(mcp_payload), failures)
    server.DEFAULT_STUDY_ROOT.mkdir(parents=True, exist_ok=True)
    server.scan_library(server.DEFAULT_STUDY_ROOT)
    check("library_scan_preserves_canvas_retained_material", server.get_file(conn, int(file_row["id"]))["active"] == 1, failures)
    check("first_version_links_source_and_blob", version_a["source_id"] == source["id"] and version_a["blob_id"] == blob_a["id"] and blob_a["sha256"] == hashlib.sha256(BYTES_A).hexdigest(), failures)
    check("baseline_advances_after_ingestion", conn.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines").fetchone()[0] == 1, failures)
    check("apply_response_hides_url_and_path", DOWNLOAD_URL not in json.dumps(result) and str(server.DATA_DIR) not in json.dumps(result), failures)
    check("offering_gets_one_deterministic_course_binding", conn.execute("SELECT COUNT(*) FROM course_offering_local_bindings").fetchone()[0] == 1 and bound_course["source_kind"] == "canvas_remote", failures)
    check("same_name_and_code_local_course_not_merged", int(bound_course["id"]) != local_course_id and conn.execute("SELECT COUNT(*) FROM courses WHERE course_code='TEST9200'").fetchone()[0] == 2, failures)
    check("deterministic_canvas_files_learning_unit", canvas_week["week_label"] == "Canvas Files" and canvas_week["origin"] == "canvas", failures)

    second_remote = remote_file(server, size=len(BYTES_B), updated="2026-01-02T00:00:00Z")
    conn.execute("UPDATE course_offerings SET remote_name='Renamed Synthetic Remote Course' WHERE id=?", (offering["id"],))
    conn.commit()
    connector_b = ScenarioConnector(server, [second_remote], [second_remote, second_remote])
    plan_b = plan_for(server, conn, connector_b)
    result_b = server.apply_canvas_file_sync(conn, connector_b, plan_b["plan_revision"], [remote_stable_id], open_url=DownloadOpen([BytesResponse(BYTES_B)]))
    conn.commit()
    material_b = conn.execute("SELECT * FROM materials").fetchone()
    source_b = conn.execute("SELECT * FROM sources").fetchone()
    retained_b = Path(source_b["local_locator"])
    check("same_remote_file_keeps_material_and_source_identity", material_b["stable_id"] == material["stable_id"] == remote_stable_id and source_b["stable_id"] == source["stable_id"], failures)
    check("different_bytes_create_blob_and_material_version", conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 2 and conn.execute("SELECT COUNT(*) FROM material_versions").fetchone()[0] == 2, failures)
    check("current_version_points_to_second_bytes", conn.execute("SELECT sha256 FROM material_versions WHERE id=?", (material_b["current_version_id"],)).fetchone()[0] == hashlib.sha256(BYTES_B).hexdigest(), failures)
    check("historical_retained_bytes_remain", retained_a.exists() and retained_b.exists() and retained_a.read_bytes() == BYTES_A and retained_b.read_bytes() == BYTES_B, failures)
    check("second_apply_reports_new_version", result_b["results"][0]["new_version"] is True, failures)
    rebound = conn.execute("SELECT * FROM course_offering_local_bindings").fetchone()
    renamed_course = conn.execute("SELECT * FROM courses WHERE id=?", (rebound["legacy_course_id"],)).fetchone()
    check("binding_reused_and_identity_survives_remote_rename", rebound["id"] == binding["id"] and renamed_course["stable_id"] == bound_course["stable_id"] and renamed_course["display_name"] == "Renamed Synthetic Remote Course", failures)

    custom_term = server.ensure_term(conn, "Synthetic Custom Term", stable_id="term_synthetic_custom")
    conn.commit()
    server.manage_course(
        conn,
        {
            "action": "update",
            "id": renamed_course["id"],
            "display_name": "My Canvas Course Label",
            "course_code": "MY-CODE",
            "term_id": custom_term["id"],
        },
    )
    conn.execute(
        "UPDATE course_offerings SET remote_name=?, remote_course_code=?, updated_at=? WHERE id=?",
        ("Second Remote Name", "REMOTE-NEW", server.now_iso(), offering["id"]),
    )
    conn.commit()
    same_bytes_remote = remote_file(server, size=len(BYTES_B), updated="2026-01-03T00:00:00Z")
    same_connector = ScenarioConnector(server, [same_bytes_remote], [same_bytes_remote, same_bytes_remote])
    same_plan = plan_for(server, conn, same_connector)
    server.apply_canvas_file_sync(conn, same_connector, same_plan["plan_revision"], [remote_stable_id], open_url=DownloadOpen([BytesResponse(BYTES_B)]))
    conn.commit()
    check("same_bytes_do_not_duplicate_blob_or_version", conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 2 and conn.execute("SELECT COUNT(*) FROM material_versions").fetchone()[0] == 2, failures)
    overridden = conn.execute("SELECT * FROM courses WHERE id=?", (renamed_course["id"],)).fetchone()
    fresh_offering = conn.execute("SELECT * FROM course_offerings WHERE id=?", (offering["id"],)).fetchone()
    check("canvas_course_user_override_is_locked", overridden["metadata_locked"] == 1, failures)
    check("canvas_course_user_name_code_and_term_are_preserved", overridden["display_name"] == "My Canvas Course Label" and overridden["course_code"] == "MY-CODE" and overridden["term_id"] == custom_term["id"], failures)
    check("course_offering_keeps_fresh_remote_metadata", fresh_offering["remote_name"] == "Second Remote Name" and fresh_offering["remote_course_code"] == "REMOTE-NEW", failures)
    check("locked_canvas_binding_is_reused_without_duplicate_course", conn.execute("SELECT COUNT(*) FROM course_offering_local_bindings WHERE course_offering_id=?", (offering["id"],)).fetchone()[0] == 1 and overridden["source_kind"] == "canvas_remote", failures)
    check("canvas_ingestion_succeeds_after_local_course_override", conn.execute("SELECT active FROM files WHERE stable_id=?", (remote_stable_id,)).fetchone()[0] == 1, failures)
    server.reconcile_domain_projection(conn)
    reconciled_source = conn.execute("SELECT * FROM sources WHERE stable_id=?", (source["stable_id"],)).fetchone()
    check("domain_reconciliation_preserves_canvas_provenance", reconciled_source["provider_kind"] == "canvas" and reconciled_source["authority"] == "REMOTE_AUTHORITATIVE", failures)
    conn.commit()

    metadata_remote = remote_file(server, size=len(BYTES_B), updated="2026-01-03T00:00:00Z", filename="renamed.txt")
    metadata_connector = ScenarioConnector(server, [metadata_remote], [metadata_remote])
    metadata_plan = plan_for(server, conn, metadata_connector)
    unopened = DownloadOpen([])
    metadata_result = server.apply_canvas_file_sync(conn, metadata_connector, metadata_plan["plan_revision"], [remote_stable_id], open_url=unopened)
    conn.commit()
    check("metadata_only_apply_does_not_redownload", not unopened.calls and metadata_result["results"][0]["status"] == "metadata_updated", failures)

    missing_plan = plan_for(server, conn, ScenarioConnector(server, []))
    missing_action = missing_plan["offerings"][0]["actions"][0]
    missing_before = table_counts(conn)
    check("missing_action_cannot_delete", raises_code("canvas_sync_action_not_applicable", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, []), missing_plan["plan_revision"], [missing_action["remote_file_stable_id"]], open_url=DownloadOpen([]))) and table_counts(conn) == missing_before and retained_a.exists() and retained_b.exists(), failures)

    other_authority = server.canvas_authority_id("https://canvas-alt.example.invalid")
    other_offering = server.upsert_canvas_course_offering(
        conn,
        other_authority,
        server.CanvasCourse(COURSE_ID, "Renamed Synthetic Remote Course", "TEST9200", "available", None, None, None, None),
    )
    other_course, _ = server.ensure_canvas_course_binding(conn, other_offering)
    conn.commit()
    check("different_canvas_authorities_do_not_collide", other_course["stable_id"] != renamed_course["stable_id"], failures)


def check_stale_race_and_rollback(server: Any, conn: sqlite3.Connection, offering: sqlite3.Row, tmp: Path, failures: list[str]) -> None:
    original = remote_file(server, size=len(BYTES_A))
    changed = remote_file(server, size=len(BYTES_B), updated="2026-02-01T00:00:00Z")
    plan = plan_for(server, conn, ScenarioConnector(server, [original]))
    stable_id = stable_id_from_plan(plan)
    check("changed_manifest_rejects_old_plan", raises_code("stale_canvas_sync_plan", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [changed]), plan["plan_revision"], [stable_id], open_url=DownloadOpen([]))), failures)
    check("unknown_item_rejected", raises_code("unknown_canvas_sync_item", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [original]), plan["plan_revision"], ["canvas_remote_file_unknown"], open_url=DownloadOpen([]))), failures)

    before_counts = table_counts(conn)
    race_connector = ScenarioConnector(server, [original], [original, changed])
    race_plan = plan_for(server, conn, race_connector)
    check("remote_race_rejected", raises_code("canvas_file_changed_during_acquisition", lambda: server.apply_canvas_file_sync(conn, race_connector, race_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]))), failures)
    check("race_failure_has_zero_db_mutation", table_counts(conn) == before_counts, failures)

    modified_only = remote_file(server, size=len(BYTES_A), modified="2026-02-02T00:00:00Z")
    modified_connector = ScenarioConnector(server, [original], [original, modified_only])
    modified_plan = plan_for(server, conn, modified_connector)
    check("remote_modified_at_race_rejected", raises_code("canvas_file_changed_during_acquisition", lambda: server.apply_canvas_file_sync(conn, modified_connector, modified_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]))), failures)
    size_only = remote_file(server, size=len(BYTES_A) + 1)
    size_connector = ScenarioConnector(server, [original], [original, size_only])
    size_plan = plan_for(server, conn, size_connector)
    check("remote_size_race_rejected", raises_code("canvas_file_changed_during_acquisition", lambda: server.apply_canvas_file_sync(conn, size_connector, size_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]))), failures)

    failure_connector = ScenarioConnector(server, [original], [original, original])
    failure_plan = plan_for(server, conn, failure_connector)
    expected_retained = server.safe_child_path(server.canvas_remote_content_root(), stable_id, f"{hashlib.sha256(BYTES_A).hexdigest()}.txt")
    failed = raises_code("synthetic_canvas_ingestion_failure", lambda: server.apply_canvas_file_sync(conn, failure_connector, failure_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]), fail_after_ingestion=1))
    check("ingestion_failure_rolls_back_db_and_new_retained_file", failed and table_counts(conn) == before_counts and not expected_retained.exists(), failures)
    conn.rollback()

    expected_retained.parent.mkdir(parents=True, exist_ok=True)
    expected_retained.write_bytes(BYTES_A)
    preexisting_failed = raises_code("synthetic_canvas_ingestion_failure", lambda: server.apply_canvas_file_sync(conn, failure_connector, failure_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]), fail_after_ingestion=1))
    check("failed_apply_preserves_preexisting_retained_file", preexisting_failed and expected_retained.exists() and expected_retained.read_bytes() == BYTES_A, failures)
    conn.rollback()
    expected_retained.unlink()

    expected_retained.write_bytes(b"corrupt-preexisting-content")
    conflict = raises_code("canvas_retained_content_conflict", lambda: server.apply_canvas_file_sync(conn, failure_connector, failure_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)])))
    check("corrupt_preexisting_retained_file_is_not_overwritten", conflict and expected_retained.read_bytes() == b"corrupt-preexisting-content" and table_counts(conn) == before_counts, failures)
    expected_retained.unlink()

    baseline_failure_connector = ScenarioConnector(server, [original], [original, original])
    baseline_plan = plan_for(server, conn, baseline_failure_connector)
    baseline_failed = raises_code("synthetic_baseline_failure", lambda: server.apply_canvas_file_sync(conn, baseline_failure_connector, baseline_plan["plan_revision"], [stable_id], open_url=DownloadOpen([BytesResponse(BYTES_A)]), fail_baseline=True))
    check("baseline_failure_rolls_back_ingestion", baseline_failed and table_counts(conn) == before_counts and not expected_retained.exists(), failures)
    conn.rollback()

    traversal = remote_file(server, size=len(BYTES_A), filename="../escape.txt")
    traversal_plan = plan_for(server, conn, ScenarioConnector(server, [traversal]))
    check("remote_filename_traversal_rejected_before_download", raises_code("unsupported_canvas_file_type", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [traversal], [traversal]), traversal_plan["plan_revision"], [stable_id], open_url=DownloadOpen([]))), failures)
    unsupported = remote_file(server, size=len(BYTES_A), filename="program.exe")
    unsupported_plan = plan_for(server, conn, ScenarioConnector(server, [unsupported]))
    check("unsupported_canvas_file_type_rejected_before_download", raises_code("unsupported_canvas_file_type", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [unsupported], [unsupported]), unsupported_plan["plan_revision"], [stable_id], open_url=DownloadOpen([]))), failures)
    locked = server.CanvasRemoteFile(**(original.__dict__ | {"locked": True}))
    locked_plan = plan_for(server, conn, ScenarioConnector(server, [locked]))
    check("locked_canvas_file_rejected_before_download", raises_code("canvas_file_unavailable", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [locked], [locked]), locked_plan["plan_revision"], [stable_id], open_url=DownloadOpen([]))), failures)

    unselected_course_id = "unselected-course"
    unselected = server.upsert_canvas_course_offering(
        conn,
        offering["authority_id"],
        server.CanvasCourse(unselected_course_id, "Unselected", "TEST9300", "available", None, None, None, None),
    )
    conn.execute(
        "INSERT INTO course_offering_selections(offering_id, selected, updated_at) VALUES (?, 0, ?)",
        (unselected["id"], server.now_iso()),
    )
    conn.commit()
    unselected_file_id = server.canvas_remote_file_stable_id(offering["authority_id"], unselected["stable_id"], FILE_ID)
    current_plan = server.canvas_file_sync_plan(conn, ScenarioConnector(server, [original]), [offering["stable_id"]])
    check("unselected_offering_file_id_rejected", raises_code("unknown_canvas_sync_item", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [original]), current_plan["plan_revision"], [unselected_file_id], open_url=DownloadOpen([]))), failures)

    other_selected = server.upsert_canvas_course_offering(
        conn,
        offering["authority_id"],
        server.CanvasCourse("other-selected-course", "Other Selected", "TEST9400", "available", None, None, None, None),
    )
    conn.execute(
        "INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at) VALUES (?, 1, ?, ?)",
        (other_selected["id"], server.now_iso(), server.now_iso()),
    )
    conn.commit()
    other_selected_file_id = server.canvas_remote_file_stable_id(offering["authority_id"], other_selected["stable_id"], FILE_ID)
    check(
        "file_id_from_other_selected_offering_rejected_by_scope",
        raises_code(
            "unknown_canvas_sync_item",
            lambda: server.apply_canvas_file_sync(
                conn,
                ScenarioConnector(server, [original]),
                current_plan["plan_revision"],
                [other_selected_file_id],
                requested_offering_ids=[offering["stable_id"]],
                open_url=DownloadOpen([]),
            ),
        ),
        failures,
    )

    server.upsert_canvas_file_sync_baselines(conn, offering, [original])
    conn.commit()
    check("local_baseline_change_rejects_old_plan", raises_code("stale_canvas_sync_plan", lambda: server.apply_canvas_file_sync(conn, ScenarioConnector(server, [original]), current_plan["plan_revision"], [stable_id], requested_offering_ids=[offering["stable_id"]], open_url=DownloadOpen([]))), failures)


def check_multi_file_atomicity(server: Any, conn: sqlite3.Connection, failures: list[str]) -> None:
    first = remote_file(server, size=len(BYTES_A))
    second = server.CanvasRemoteFile(
        remote_file_id=f"{FILE_ID}-second",
        remote_course_id=COURSE_ID,
        display_name="second.txt",
        filename="second.txt",
        content_type="text/plain",
        byte_size=len(BYTES_A),
        remote_created_at=first.remote_created_at,
        remote_updated_at=first.remote_updated_at,
        remote_modified_at=first.remote_modified_at,
        locked=False,
        hidden=False,
        hidden_for_user=False,
    )
    plan_connector = ScenarioConnector(server, [first, second])
    plan = plan_for(server, conn, plan_connector)
    ids = [action["remote_file_stable_id"] for action in plan["offerings"][0]["actions"]]
    apply_connector = ScenarioConnector(server, [first, second], [first, first, second])
    before = table_counts(conn)
    failed = raises_code(
        "canvas_file_size_mismatch",
        lambda: server.apply_canvas_file_sync(
            conn,
            apply_connector,
            plan["plan_revision"],
            ids,
            open_url=DownloadOpen([BytesResponse(BYTES_A), BytesResponse(b"short")]),
        ),
    )
    check("multi_file_download_failure_has_zero_db_mutation", failed and table_counts(conn) == before, failures)
    first_path = server.safe_child_path(server.canvas_remote_content_root(), ids[0], f"{hashlib.sha256(BYTES_A).hexdigest()}.txt")
    check("multi_file_failure_cleans_prior_new_retained_file", not first_path.exists(), failures)

    changed_first = server.CanvasRemoteFile(**(first.__dict__ | {"remote_updated_at": "2026-03-01T00:00:00Z"}))
    late_race_connector = ScenarioConnector(server, [first, second], [first, first, second, second, changed_first])
    late_race_failed = raises_code(
        "canvas_file_changed_during_acquisition",
        lambda: server.apply_canvas_file_sync(
            conn,
            late_race_connector,
            plan["plan_revision"],
            ids,
            open_url=DownloadOpen([BytesResponse(BYTES_A), BytesResponse(BYTES_A)]),
        ),
    )
    second_path = server.safe_child_path(server.canvas_remote_content_root(), ids[1], f"{hashlib.sha256(BYTES_A).hexdigest()}.txt")
    check("final_remote_recheck_closes_multi_file_toctou_window", late_race_failed and table_counts(conn) == before, failures)
    check("late_remote_race_cleans_all_new_retained_files", not first_path.exists() and not second_path.exists(), failures)


def check_concurrent_production_apply(server: Any, failures: list[str]) -> None:
    setup = server.connect_db()
    server.init_db(setup)
    offering = seed_offering(server, setup)
    current = remote_file(server, size=len(BYTES_A))
    plan = plan_for(server, setup, ScenarioConnector(server, [current]))
    stable_id = stable_id_from_plan(plan)
    setup.close()

    first_inside = threading.Event()
    second_attempting = threading.Event()

    class BlockingConnector(ScenarioConnector):
        def list_course_files(self, remote_course_id: str):
            first_inside.set()
            if not second_attempting.wait(timeout=5):
                raise RuntimeError("second_apply_did_not_overlap")
            return super().list_course_files(remote_course_id)

    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def first_worker() -> None:
        try:
            server.run_canvas_sync_apply(
                BlockingConnector(server, [current]),
                plan["plan_revision"],
                [stable_id],
                requested_offering_ids=[offering["stable_id"]],
                open_url=DownloadOpen([BytesResponse(BYTES_A)]),
            )
            outcome = "success"
        except Exception as exc:  # noqa: BLE001 - acceptance records sanitized code
            outcome = str(exc)
        with outcome_lock:
            outcomes.append(outcome)

    def second_worker() -> None:
        if not first_inside.wait(timeout=5):
            with outcome_lock:
                outcomes.append("first_apply_did_not_enter")
            return
        second_attempting.set()
        try:
            server.run_canvas_sync_apply(
                ScenarioConnector(server, [current]),
                plan["plan_revision"],
                [stable_id],
                requested_offering_ids=[offering["stable_id"]],
                open_url=DownloadOpen([]),
            )
            outcome = "success"
        except Exception as exc:  # noqa: BLE001 - acceptance records sanitized code
            outcome = str(exc)
        with outcome_lock:
            outcomes.append(outcome)

    first_thread = threading.Thread(target=first_worker)
    second_thread = threading.Thread(target=second_worker)
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)
    check("concurrent_apply_threads_complete", not first_thread.is_alive() and not second_thread.is_alive(), failures)
    check("production_apply_lock_serializes_same_old_plan", sorted(outcomes) == ["stale_canvas_sync_plan", "success"], failures)

    verify = server.connect_db()
    source = verify.execute("SELECT * FROM sources WHERE provider_kind='canvas'").fetchone()
    material = verify.execute("SELECT * FROM materials WHERE stable_id=?", (stable_id,)).fetchone()
    versions = verify.execute("SELECT COUNT(*) FROM material_versions WHERE material_id=?", (material["id"],)).fetchone()[0]
    baseline = verify.execute("SELECT COUNT(*) FROM canvas_file_sync_baselines WHERE course_offering_id=?", (offering["id"],)).fetchone()[0]
    retained = Path(source["local_locator"])
    check("concurrent_apply_retains_committed_bytes", retained.exists() and retained.read_bytes() == BYTES_A, failures)
    check("concurrent_apply_commits_one_consistent_domain_state", versions == 1 and baseline == 1 and material["current_version_id"] is not None, failures)
    verify.close()


def check_migration(server: Any, failures: list[str]) -> None:
    conn = server.connect_db()
    server.init_db(conn)
    helper_spec = importlib.util.spec_from_file_location(
        "studyhub_canvas_sync_seed_helper", ROOT / "bin" / "canvas_sync_plan_acceptance.py"
    )
    helper = importlib.util.module_from_spec(helper_spec)
    assert helper_spec and helper_spec.loader
    sys.modules[helper_spec.name] = helper
    helper_spec.loader.exec_module(helper)
    helper.seed_pr23_existing_data(conn)
    offering = seed_offering(server, conn)
    baseline_file = remote_file(server, size=len(BYTES_A))
    server.upsert_canvas_file_sync_baselines(conn, offering, [baseline_file])
    conn.commit()
    conn.execute("DROP TABLE course_offering_local_bindings")
    conn.execute("DELETE FROM schema_migrations WHERE version=4")
    conn.commit()
    tables = ("terms", "courses", "weeks", "files", "file_versions", "sources", "blobs", "materials", "material_versions", "course_offerings", "course_offering_selections", "canvas_file_sync_baselines")
    before = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in tables}
    server.run_schema_migrations(conn)
    after = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in tables}
    server.run_schema_migrations(conn)
    check("pr24_to_pr25_migration_preserves_existing_rows", before == after, failures)
    check("binding_migration_recorded_once", conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=4").fetchone()[0] == 1, failures)
    check("binding_migration_is_idempotent", conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='course_offering_local_bindings'").fetchone()[0] == 1, failures)
    check("binding_migration_foreign_keys_valid", not conn.execute("PRAGMA foreign_key_check").fetchall(), failures)
    conn.close()


def check_api_input_boundary(server: Any, failures: list[str]) -> None:
    original = server.with_canvas_connector
    server.with_canvas_connector = lambda _callback: (_ for _ in ()).throw(RuntimeError("connector_must_not_run"))
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/canvas/sync-apply",
        data=json.dumps(
            {
                "plan_revision": "synthetic",
                "remote_file_stable_ids": [],
                "download_url": DOWNLOAD_URL,
                "filename": "injected.txt",
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "X-StudyHub-CSRF": server.CSRF_TOKEN,
        },
        method="POST",
    )
    try:
        try:
            urllib.request.urlopen(request, timeout=5)
            status, payload = 200, {}
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, json.loads(exc.read())
        check("browser_metadata_and_url_injection_rejected", status == 400 and DOWNLOAD_URL not in json.dumps(payload), failures)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        server.with_canvas_connector = original


def check_local_provenance_regression(server: Any, conn: sqlite3.Connection, tmp: Path, failures: list[str]) -> None:
    term = server.ensure_term(conn, "Imported Courses", stable_id="term_imported")
    now = server.now_iso()
    conn.execute(
        """
        INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
          source_folder, source_kind, term_id, archived, active, created_at, updated_at)
        VALUES ('local_regression_course', 'TEST9500', 'TEST9500', 'Local Regression',
          'Local Regression', 'local-regression', ?, ?, 'managed', ?, 0, 1, ?, ?)
        """,
        (str(tmp), str(tmp), term["id"], now, now),
    )
    course = conn.execute("SELECT * FROM courses WHERE stable_id='local_regression_course'").fetchone()
    week = server.ensure_week_record(conn, course["id"], "Week 01", number=1, path=str(tmp), origin="managed")
    path = tmp / "local.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"Synthetic local user-owned material.\n")
    server.ingest_material_candidate(
        conn,
        server.IngestionCandidate(
            path=path,
            course_id=course["id"],
            week_id=week["id"],
            course_code="TEST9500",
            course_name="Local Regression",
            week_label="Week 01",
            week_number=1,
            section="Materials",
            category="Lecture",
            exercise_type="",
            material_type="lecture",
            source="official",
            source_label="Local Reference",
            source_type="Local Reference",
            rel_path="local.txt",
            import_mode="reference",
            is_official=1,
            is_solution=0,
            is_question_source=0,
        ),
    )
    source = conn.execute("SELECT * FROM sources").fetchone()
    check("local_scanner_source_remains_user_owned", source["provider_kind"] == "local" and source["authority"] == "USER_OWNED", failures)
    local_row = conn.execute("SELECT * FROM files WHERE original_path=?", (str(path.resolve()),)).fetchone()
    check("study_root_material_path_behavior_is_unchanged", server.material_path_allowed(local_row, path), failures)
    external = server.DATA_DIR.parent / "external-reference.txt"
    external.write_bytes(b"Synthetic external reference.\n")
    external_row = {"import_mode": "reference", "original_path": str(external)}
    check("external_reference_exact_path_remains_allowed", server.material_path_allowed(external_row, external), failures)
    check("external_reference_sibling_remains_rejected", not server.material_path_allowed(external_row, external.with_name("other.txt")), failures)
    conn.rollback()


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-canvas-acquisition-") as raw_tmp:
        tmp = Path(raw_tmp)
        migration_server = load_server(tmp / "migration")
        check_migration(migration_server, failures)
        server = load_server(tmp / "main")
        check_official_lookup(server, failures)
        check_download_security(server, tmp / "downloads", failures)
        check_api_input_boundary(server, failures)
        conn = server.connect_db()
        server.init_db(conn)
        offering = seed_offering(server, conn)
        check_apply_and_versions(server, conn, offering, failures)
        conn.close()

        rollback_server = load_server(tmp / "rollback")
        rollback_conn = rollback_server.connect_db()
        rollback_server.init_db(rollback_conn)
        rollback_offering = seed_offering(rollback_server, rollback_conn)
        check_stale_race_and_rollback(rollback_server, rollback_conn, rollback_offering, tmp / "rollback", failures)
        rollback_conn.close()

        multi_server = load_server(tmp / "multi")
        multi_conn = multi_server.connect_db()
        multi_server.init_db(multi_conn)
        seed_offering(multi_server, multi_conn)
        check_multi_file_atomicity(multi_server, multi_conn, failures)
        multi_conn.close()

        concurrent_server = load_server(tmp / "concurrent")
        check_concurrent_production_apply(concurrent_server, failures)

        local_server = load_server(tmp / "local-provenance")
        local_conn = local_server.connect_db()
        local_server.init_db(local_conn)
        check_local_provenance_regression(local_server, local_conn, tmp / "local-provenance" / "library", failures)
        local_conn.close()

        database_bytes = Path(server.DB_PATH).read_bytes()
        log_path = server.LOG_DIR / "studyhub.log"
        logs = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
        check("token_absent_from_db_and_logs", TOKEN.encode() not in database_bytes and TOKEN not in logs, failures)
        check("signed_url_absent_from_db_and_logs", DOWNLOAD_URL.encode() not in database_bytes and DOWNLOAD_URL not in logs, failures)
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        download_source = source.split("def default_canvas_download_open", 1)[1].split("class CanvasConnector", 1)[0]
        check("download_client_has_no_authorization_header", "Authorization" not in download_source and "Cookie" not in download_source, failures)
        check("canvas_connector_remains_get_only", 'method="POST"' not in source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0], failures)
        check("no_background_sync_or_remote_delete_added", all(token not in source for token in ("canvas_sync_scheduler", "canvas_sync_poll", "/api/canvas/sync-delete")), failures)
        check("mcp_has_no_canvas_apply", all("canvas" not in name.lower() for name in server.MCP_TOOLS), failures)

    if failures:
        print("Canvas file acquisition acceptance failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Canvas file acquisition acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
