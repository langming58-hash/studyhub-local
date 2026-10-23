#!/usr/bin/env python3
"""Behavioral acceptance for the versioned user-owned backup export format."""

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
import urllib.request
import warnings
import zipfile
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
LOCAL_BYTES = b"SYNTHETIC-LOCAL-ORIGINAL-BYTES"
REMOTE_BYTES = b"SYNTHETIC-REMOTE-RETAINED-BYTES"
EXTRACTED_CANARY = "SYNTHETIC-EXTRACTED-TEXT-CANARY"
TOKEN_CANARY = "synthetic-token-canary-value-000000"
API_KEY_CANARY = "s" + "k-" + "syntheticbackupcanary000000"
SIGNED_URL_CANARY = "https://files.invalid/download?" + "to" + "ken=synthetic-signed-value"
CANVAS_ORIGIN_CANARY = "https://canvas.invalid"
MAC_PATH_CANARY = "/" + "Users" + "/example/PrivateStudy/cache/item.txt"
LOG_CANARY = "SYNTHETIC-PRIVATE-LOG-CANARY"


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def load_server(root: Path, profile: str = "development") -> Any:
    env = {
        "STUDYHUB_RUNTIME_PROFILE": profile,
        "STUDYHUB_RUNTIME_DIR": str(root / "runtime"),
        "STUDYHUB_DATA_DIR": str(root / "runtime" / "data"),
        "STUDYHUB_CACHE_DIR": str(root / "runtime" / "cache"),
        "STUDYHUB_LOG_DIR": str(root / "runtime" / "logs"),
        "STUDY_LIBRARY_PATH": str(root / "library"),
        "DATABASE_PATH": str(root / "runtime" / "data" / "studyhub.sqlite"),
        "STUDYHUB_CONFIG_PATH": str(root / "runtime" / "settings.env"),
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    module_name = f"studyhub_backup_{hashlib.sha256(str(root).encode()).hexdigest()[:12]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "server.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def insert_file(
    server: Any,
    conn: sqlite3.Connection,
    *,
    file_id: int,
    course_id: int,
    week_id: int,
    path: Path,
    stable_id: str,
    import_mode: str,
    display_name: str,
    metadata_locked: int,
    source_type: str,
) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    timestamp = "2026-04-01T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO files(
          id, course_id, week_id, course_code, course_name, week_label, week_number,
          section, category, exercise_type, filename, display_name, original_path,
          absolute_path, rel_path, source, source_label, source_type, import_mode,
          hash, sha256, size, file_size, modified_at, indexed_at, extension,
          file_extension, mime_type, is_official, suspicious, text_cache_path,
          stable_id, material_type, inbox, metadata_locked, source_missing,
          active, material_created_at, material_updated_at
        ) VALUES (?, ?, ?, 'SYN9200', 'Synthetic Course', 'Module Alpha', NULL,
          '01 Course Materials', 'Lecture', '', ?, ?, ?, ?, ?, 'user',
          'Synthetic source', ?, ?, ?, ?, ?, ?, ?, ?, '.txt', '.txt', 'text/plain',
          0, '', ?, ?, 'lecture', 0, ?, 0, 1, ?, ?)
        """,
        (
            file_id,
            course_id,
            week_id,
            path.name,
            display_name,
            str(path),
            str(path),
            f"Module Alpha/Lecture/{path.name}",
            source_type,
            import_mode,
            digest,
            digest,
            len(path.read_bytes()),
            len(path.read_bytes()),
            timestamp,
            timestamp,
            str(server.TEXT_CACHE_DIR / f"{stable_id}.txt"),
            stable_id,
            metadata_locked,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """
        INSERT INTO file_versions(file_id, stable_id, sha256, file_size, modified_at,
          indexed_at, text_cache_path, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (file_id, f"version_{stable_id}", digest, len(path.read_bytes()), timestamp, timestamp, str(server.TEXT_CACHE_DIR / f"{stable_id}.txt")),
    )
    conn.execute(
        """
        INSERT INTO document_chunks(file_id, version_id, chunk_index, course_code,
          week_label, category, exercise_type, filename, source_location, text,
          token_estimate, created_at)
        VALUES (?, (SELECT id FROM file_versions WHERE file_id=?), 0, 'SYN9200',
          'Module Alpha', 'Lecture', '', ?, 'Synthetic page', ?, 4, ?)
        """,
        (file_id, file_id, path.name, EXTRACTED_CANARY, timestamp),
    )


def seed_state(server: Any, root: Path, id_base: int, *, reverse_insert_order: bool = False) -> sqlite3.Connection:
    library_file = root / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "synthetic-guide.txt"
    library_file.parent.mkdir(parents=True, exist_ok=True)
    library_file.write_bytes(LOCAL_BYTES)
    remote_file = root / "runtime" / "data" / "canvas-remote" / "opaque" / "remote-note.txt"
    remote_file.parent.mkdir(parents=True, exist_ok=True)
    remote_file.write_bytes(REMOTE_BYTES)
    server.TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (server.TEXT_CACHE_DIR / "material_syn_local.txt").write_text(EXTRACTED_CANARY, encoding="utf-8")
    preview = server.CACHE_DIR / "previews" / "synthetic-preview.pdf"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"SYNTHETIC-PREVIEW-CANARY")
    server.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (server.LOG_DIR / "synthetic.log").write_text(LOG_CANARY, encoding="utf-8")

    conn = server.connect_db()
    server.init_db(conn)
    term_id = id_base + 1
    course_id = id_base + 2
    week_id = id_base + 3
    local_file_id = id_base + 4
    remote_course_id = id_base + 5
    remote_week_id = id_base + 6
    remote_file_id = id_base + 7
    timestamp = "2026-04-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO terms(id, stable_id, name, archived, sort_order, created_at, updated_at) VALUES (?, 'term_syn_alpha', 'Synthetic Term', 0, 4, ?, ?)",
        (term_id, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO courses(id, stable_id, code, course_code, name, display_name,
          folder_name, path, source_folder, source_kind, term_id, archived, active,
          sort_order, metadata_locked, created_at, updated_at)
        VALUES (?, 'course_syn_alpha', 'SYN9200', 'SYN9200', 'Synthetic Course',
          'My Synthetic Course', 'synthetic-course', ?, ?, 'managed', ?, 0, 1, 2, 1, ?, ?)
        """,
        (course_id, str(library_file.parent.parent), str(library_file.parent.parent), term_id, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO weeks(id, stable_id, course_id, week_label, week_number, path,
          has_materials, file_count, kind, origin, archived, created_at, updated_at)
        VALUES (?, 'unit_syn_alpha', ?, 'Module Alpha', NULL, '', 1, 1, 'module', 'managed', 0, ?, ?)
        """,
        (week_id, course_id, timestamp, timestamp),
    )
    insert_file(
        server,
        conn,
        file_id=local_file_id,
        course_id=course_id,
        week_id=week_id,
        path=library_file,
        stable_id="material_syn_local",
        import_mode="reference",
        display_name="My guide",
        metadata_locked=1,
        source_type="Local Reference",
    )
    conn.execute(
        """
        INSERT INTO courses(id, stable_id, code, course_code, name, display_name,
          folder_name, path, source_folder, source_kind, term_id, archived, active,
          sort_order, metadata_locked, created_at, updated_at)
        VALUES (?, 'course_syn_remote', 'REMOTE', 'REMOTE', 'Remote title',
          'Remote title', 'remote-course', ?, ?, 'canvas_remote', ?, 0, 1, 0, 0, ?, ?)
        """,
        (remote_course_id, str(remote_file.parent), str(remote_file.parent), term_id, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO weeks(id, stable_id, course_id, week_label, path, has_materials,
          file_count, kind, origin, archived, created_at, updated_at)
        VALUES (?, 'unit_syn_remote', ?, 'Canvas Files', '', 1, 1, 'module', 'canvas', 0, ?, ?)
        """,
        (remote_week_id, remote_course_id, timestamp, timestamp),
    )
    insert_file(
        server,
        conn,
        file_id=remote_file_id,
        course_id=remote_course_id,
        week_id=remote_week_id,
        path=remote_file,
        stable_id="material_syn_remote",
        import_mode="canvas_remote",
        display_name="Remote note",
        metadata_locked=0,
        source_type="Canvas",
    )
    server.reconcile_domain_projection(conn)

    duplicate_note = ("Repeated same-second note", timestamp)
    note_rows = [
        (f"My note with {root}, {MAC_PATH_CANARY}, {'to' + 'ken'}={TOKEN_CANARY}, and {API_KEY_CANARY}", timestamp),
        ("Second portable note", "2026-04-01T00:02:00+00:00"),
        duplicate_note,
        duplicate_note,
    ]
    if reverse_insert_order:
        note_rows.reverse()
    for body, note_timestamp in note_rows:
        conn.execute(
            """
            INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at)
            VALUES ('file', ?, ?, 'Module Alpha', ?, ?, ?)
            """,
            (local_file_id, course_id, body, note_timestamp, note_timestamp),
        )
    conn.execute("INSERT INTO stars(target_type, target_id, created_at) VALUES ('file', ?, ?)", (local_file_id, timestamp))
    attempt_rows = [
        ("remote prompt omitted", "My answer", "review", timestamp),
        ("remote prompt omitted", "My answer", "review", timestamp),
        ("another omitted prompt", "Another answer", "complete", "2026-04-01T00:03:00+00:00"),
    ]
    wrong_rows = [
        ("My correction", "Synthetic concept", 2, timestamp, "learning"),
        ("My correction", "Synthetic concept", 2, timestamp, "learning"),
        ("Updated correction", "Synthetic concept", 3, "2026-04-02T00:00:00+00:00", "reviewing"),
    ]
    session_rows = [
        ("Module Alpha", "review", timestamp, "2026-04-01T00:30:00+00:00"),
        ("Module Alpha", "review", timestamp, "2026-04-01T00:30:00+00:00"),
        ("Module Alpha", "practice", timestamp, "2026-04-01T00:45:00+00:00"),
    ]
    if reverse_insert_order:
        attempt_rows.reverse()
        wrong_rows.reverse()
        session_rows.reverse()
    for prompt, answer, result, attempt_timestamp in attempt_rows:
        conn.execute(
            "INSERT INTO attempts(file_id, prompt, answer_text, result, created_at) VALUES (?, ?, ?, ?, ?)",
            (local_file_id, prompt, answer, result, attempt_timestamp),
        )
    for mistake, concept, attempts, last_reviewed, mastery in wrong_rows:
        conn.execute(
            """
            INSERT INTO wrong_questions(course_id, week_label, type, source_file_id,
              question_ref, mistake, concept, attempts, last_reviewed, mastery, created_at)
            VALUES (?, 'Module Alpha', 'tutorial', ?, 'Q-SYN-1', ?, ?, ?, ?, ?, ?)
            """,
            (course_id, local_file_id, mistake, concept, attempts, last_reviewed, mastery, timestamp),
        )
    conn.execute("INSERT INTO bookmarks(target_type, target_id, label, created_at) VALUES ('file', ?, 'Review this', ?)", (local_file_id, timestamp))
    for week_label, mode, started_at, ended_at in session_rows:
        conn.execute(
            "INSERT INTO study_sessions(course_id, week_label, mode, started_at, ended_at) VALUES (?, ?, ?, ?, ?)",
            (course_id, week_label, mode, started_at, ended_at),
        )
    conn.execute(
        """
        INSERT INTO ai_conversations(title, scope, context_json, course_code, week_label,
          file_id, created_at, updated_at)
        VALUES ('Synthetic explanation', 'file', ?, 'SYN9200', 'Module Alpha', ?, ?, ?)
        """,
        (json.dumps({"unsafe_path": str(root), "signed_url": SIGNED_URL_CANARY}), local_file_id, timestamp, timestamp),
    )
    conversation_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.execute(
        """
        INSERT INTO ai_messages(conversation_id, role, body, status, sources_json,
          questions_json, solutions_json, created_at)
        VALUES (?, 'user', 'Explain my synthetic material', 'complete', ?, '[]', '[]', ?)
        """,
        (conversation_id, json.dumps([{"path": str(root), "url": SIGNED_URL_CANARY}]), timestamp),
    )
    conn.execute(
        """
        INSERT INTO ai_messages(conversation_id, role, body, status, sources_json,
          questions_json, solutions_json, created_at)
        VALUES (?, 'assistant', 'Synthetic explanation', 'complete', '[]', '[]', '[]', ?)
        """,
        (conversation_id, "2026-04-01T00:01:00+00:00"),
    )
    conn.execute(
        "INSERT INTO app_settings(key, value, updated_at) VALUES ('credential_canary', ?, ?)",
        (TOKEN_CANARY, timestamp),
    )
    conn.execute(
        """
        INSERT INTO ai_index_state(file_id, stable_id, sha256, provider, vector_store_id,
          provider_file_id, status, metadata_json)
        VALUES (?, 'index-synthetic', ?, 'openai', 'vector-canary', 'provider-file-canary',
          'indexed', ?)
        """,
        (local_file_id, hashlib.sha256(LOCAL_BYTES).hexdigest(), json.dumps({"origin": CANVAS_ORIGIN_CANARY})),
    )

    offering_id = id_base + 20
    conn.execute(
        """
        INSERT INTO course_offerings(id, stable_id, provider, authority_id, remote_course_id,
          remote_name, first_seen_at, last_seen_at, created_at, updated_at)
        VALUES (?, 'offering_syn_remote', 'canvas', 'authority-canary', 'remote-course-canary',
          'Remote Course', ?, ?, ?, ?)
        """,
        (offering_id, timestamp, timestamp, timestamp, timestamp),
    )
    conn.execute(
        "INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at) VALUES (?, 1, ?, ?)",
        (offering_id, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO canvas_file_sync_baselines(stable_id, course_offering_id, provider,
          authority_id, remote_course_id, remote_file_id, filename, baseline_fingerprint,
          applied_at, created_at, updated_at)
        VALUES ('baseline_syn_remote', ?, 'canvas', 'authority-canary', 'remote-course-canary',
          'remote-file-canary', 'remote-note.txt', 'fingerprint-canary', ?, ?, ?)
        """,
        (offering_id, timestamp, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO academic_change_events(stable_id, event_kind, provider, authority_id,
          course_offering_id, course_offering_stable_id, material_stable_id,
          remote_item_stable_id, course_label, material_label, changed_fields_json,
          applied_at, created_at)
        VALUES ('event_syn_alpha', 'CANVAS_FILE_ADDED', 'canvas', 'authority-canary', ?,
          'offering_syn_remote', 'material_syn_remote', 'remote-item-canary',
          'Remote Course', 'Remote note', '[]', ?, ?)
        """,
        (offering_id, timestamp, timestamp),
    )
    event_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.execute(
        "INSERT INTO academic_change_event_state(event_id, seen_at, updated_at) VALUES (?, ?, ?)",
        (event_id, timestamp, timestamp),
    )
    conn.commit()
    return conn


def archive_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def mark_zip_entries_encrypted(raw: bytes) -> bytes:
    """Set ZIP encryption flags without adding a real password-protected payload."""
    changed = bytearray(raw)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        cursor = 0
        while True:
            cursor = changed.find(signature, cursor)
            if cursor < 0:
                break
            flags = int.from_bytes(changed[cursor + flag_offset : cursor + flag_offset + 2], "little")
            changed[cursor + flag_offset : cursor + flag_offset + 2] = (flags | 0x1).to_bytes(2, "little")
            cursor += len(signature)
    return bytes(changed)


def rewrite_archive(path: Path, mutation: Callable[[dict[str, bytes]], None]) -> Path:
    members = archive_members(path)
    mutation(members)
    output = path.with_name(f"mutated-{hashlib.sha256(os.urandom(8)).hexdigest()[:8]}.zip")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return output


def rejected(server: Any, path: Path) -> bool:
    try:
        server.validate_backup_archive(path)
    except (ValueError, zipfile.BadZipFile):
        return True
    return False


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-backup-acceptance-") as temp:
        temp_root = Path(temp)
        root_one = temp_root / "root-one"
        root_two = temp_root / "root-two"
        server_one = load_server(root_one)
        server_two = load_server(root_two)
        conn_one = seed_state(server_one, root_one, 100)
        conn_two = seed_state(server_two, root_two, 500, reverse_insert_order=True)
        before_dump = "\n".join(conn_one.iterdump())

        blocked = {"canvas": 0, "openai": 0, "credentials": 0}

        def blocked_boundary(kind: str) -> Callable[..., Any]:
            def fail(*_args: Any, **_kwargs: Any) -> Any:
                blocked[kind] += 1
                raise AssertionError(f"{kind} boundary must remain offline")

            return fail

        server_one.with_canvas_connector = blocked_boundary("canvas")
        server_one.openai_responses_request = blocked_boundary("openai")
        server_one.initialize_credential_client = blocked_boundary("credentials")
        server_one.canvas_connection_record_from_private_transport = blocked_boundary("credentials")

        readonly_conn = server_one.connect_backup_db_readonly()
        readonly_failures = []
        for sql in (
            "INSERT INTO terms(stable_id, name, archived, sort_order, created_at, updated_at) VALUES ('blocked', 'Blocked', 0, 0, '', '')",
            "UPDATE terms SET name='Blocked'",
            "DELETE FROM terms",
            "CREATE TABLE blocked_write(id INTEGER)",
        ):
            try:
                readonly_conn.execute(sql)
            except sqlite3.OperationalError:
                readonly_failures.append(sql)
        check(
            "readonly_backup_connection_rejects_all_writes",
            readonly_conn.execute("PRAGMA query_only").fetchone()[0] == 1 and len(readonly_failures) == 4,
            failures,
        )
        readonly_conn.close()

        result_one = server_one.export_user_owned_backup(conn_one, created_at="2026-04-02T00:00:00+00:00")
        archive_one = server_one.EXPORT_DIR / result_one["filename"]
        validation = server_one.validate_backup_archive(archive_one)
        after_dump = "\n".join(conn_one.iterdump())
        members_one = archive_members(archive_one)
        raw_archive = archive_one.read_bytes()
        inspected_archive_content = raw_archive + b"".join(members_one.values())
        manifest = json.loads(members_one["manifest.json"])

        check("valid_synthetic_export_created", archive_one.is_file() and result_one["format"] == "studyhub-backup", failures)
        check("manifest_version_correct", manifest["format_version"] == 1 and validation["format_version"] == 1, failures)
        check("allowed_members_only", set(members_one) == set(server_one.BACKUP_MEMBERS), failures)
        check("member_checksums_correct", all(hashlib.sha256(members_one[name]).hexdigest() == item["sha256"] for name, item in manifest["entries"].items()), failures)
        check("record_counts_correct", validation["total_records"] == manifest["total_records"] and validation["total_records"] > 0, failures)
        check("export_is_zero_db_mutation", before_dump == after_dump, failures)
        check("export_works_offline", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)

        result_two = server_two.export_user_owned_backup(conn_two, created_at="2026-04-03T00:00:00+00:00")
        archive_two = server_two.EXPORT_DIR / result_two["filename"]
        members_two = archive_members(archive_two)
        check("deterministic_canonical_member_bytes", all(members_one[name] == members_two[name] for name in server_one.BACKUP_DATA_MEMBERS), failures)
        check("db_integer_ids_do_not_define_exported_identity", b'"id":' not in b"".join(members_one[name] for name in server_one.BACKUP_DATA_MEMBERS), failures)
        check("different_runtime_roots_are_portable", str(root_one).encode() not in inspected_archive_content and str(root_two).encode() not in inspected_archive_content, failures)
        copied = root_two / "inspection" / "portable.zip"
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(raw_archive)
        check("validator_requires_no_original_root", server_two.validate_backup_archive(copied)["total_records"] == validation["total_records"], failures)

        note_records_one = json.loads(members_one["data/notes.json"])["records"]
        note_records_two = json.loads(members_two["data/notes.json"])["records"]
        duplicate_notes_one = [record for record in note_records_one if record["body"] == "Repeated same-second note"]
        duplicate_notes_two = [record for record in note_records_two if record["body"] == "Repeated same-second note"]
        check(
            "duplicate_same_second_notes_have_distinct_ids",
            len(duplicate_notes_one) == 2 and len({record["stable_id"] for record in duplicate_notes_one}) == 2,
            failures,
        )
        check(
            "duplicate_note_id_set_is_portable_and_deterministic",
            {record["stable_id"] for record in duplicate_notes_one}
            == {record["stable_id"] for record in duplicate_notes_two},
            failures,
        )
        study_records_one = json.loads(members_one["data/study_records.json"])["records"]
        study_records_two = json.loads(members_two["data/study_records.json"])["records"]
        for record_type, expected_count in (("attempt", 3), ("wrong_question_state", 3), ("study_session", 3)):
            records_one = [record for record in study_records_one if record["record_type"] == record_type]
            records_two = [record for record in study_records_two if record["record_type"] == record_type]
            check(
                f"duplicate_{record_type}_identity_is_deterministic",
                len(records_one) == expected_count
                and len({record["stable_id"] for record in records_one}) == expected_count
                and {record["stable_id"] for record in records_one} == {record["stable_id"] for record in records_two},
                failures,
            )

        forbidden = [
            str(root_one).encode(),
            TOKEN_CANARY.encode(),
            API_KEY_CANARY.encode(),
            CANVAS_ORIGIN_CANARY.encode(),
            SIGNED_URL_CANARY.encode(),
            MAC_PATH_CANARY.encode(),
            LOG_CANARY.encode(),
            REMOTE_BYTES,
            LOCAL_BYTES,
            EXTRACTED_CANARY.encode(),
            b"SYNTHETIC-PREVIEW-CANARY",
            b"provider-file-canary",
            b"vector-canary",
            b"remote-file-canary",
        ]
        check("privacy_canaries_absent_from_archive", all(value not in inspected_archive_content for value in forbidden), failures)
        check("original_and_remote_academic_bytes_absent", LOCAL_BYTES not in inspected_archive_content and REMOTE_BYTES not in inspected_archive_content, failures)
        check(
            "source_files_are_not_mutated",
            (root_one / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "synthetic-guide.txt").read_bytes() == LOCAL_BYTES
            and (root_one / "runtime" / "data" / "canvas-remote" / "opaque" / "remote-note.txt").read_bytes() == REMOTE_BYTES,
            failures,
        )
        check("derived_and_runtime_payloads_absent", EXTRACTED_CANARY.encode() not in inspected_archive_content and b"studyhub.sqlite" not in inspected_archive_content, failures)
        connector = json.loads(members_one["data/connector_user_state.json"])["records"]
        seen = json.loads(members_one["data/whats_new_state.json"])["records"]
        check("canvas_selection_explicitly_excluded", connector == [] and manifest["features"]["canvas_selection_included"] is False, failures)
        check("seen_state_uses_event_stable_identity", len(seen) == 1 and seen[0]["event_stable_id"] == "event_syn_alpha" and "event_id" not in seen[0], failures)
        ai_records = json.loads(members_one["data/ai_history.json"])["records"]
        check("ai_history_explicitly_excluded", ai_records == [] and manifest["features"]["ai_history_included"] is False, failures)
        conn_one.execute("UPDATE ai_conversations SET title='Renamed synthetic conversation'")
        conn_one.commit()
        renamed_ai_result = server_one.export_user_owned_backup(conn_one, created_at="2026-04-02T00:01:00+00:00")
        renamed_ai_members = archive_members(server_one.EXPORT_DIR / renamed_ai_result["filename"])
        check(
            "ai_rename_cannot_change_claimed_portable_identity",
            members_one["data/ai_history.json"] == renamed_ai_members["data/ai_history.json"],
            failures,
        )
        local_sources = json.loads(members_one["data/local_sources.json"])["records"]
        check("local_source_uses_safe_relative_path", len(local_sources) == 1 and local_sources[0]["location"]["kind"] == "library_relative" and not local_sources[0]["location"]["relative_path"].startswith("/"), failures)

        def corrupt_manifest(members: dict[str, bytes]) -> None:
            members["manifest.json"] = b"not-json"

        check("malformed_manifest_rejected", rejected(server_one, rewrite_archive(archive_one, corrupt_manifest)), failures)

        def future_version(members: dict[str, bytes]) -> None:
            value = json.loads(members["manifest.json"])
            value["format_version"] = 99
            members["manifest.json"] = server_one.canonical_backup_json(value)

        check("unsupported_version_rejected", rejected(server_one, rewrite_archive(archive_one, future_version)), failures)

        def unexpected_manifest_field(members: dict[str, bytes]) -> None:
            value = json.loads(members["manifest.json"])
            value["unexpected"] = True
            members["manifest.json"] = server_one.canonical_backup_json(value)

        check("unexpected_manifest_field_rejected", rejected(server_one, rewrite_archive(archive_one, unexpected_manifest_field)), failures)

        def nonempty_ai_history(members: dict[str, bytes]) -> None:
            payload = json.loads(members["data/ai_history.json"])
            payload["records"] = [
                {
                    "stable_id": "conversation_disallowed",
                    "title": "Disallowed old export shape",
                    "scope": "course",
                    "course_ref": None,
                    "learning_unit_label": "",
                    "material_ref": None,
                    "created_at": "2026-04-01T00:00:00+00:00",
                    "updated_at": "2026-04-01T00:00:00+00:00",
                    "messages": [],
                }
            ]
            raw = server_one.canonical_backup_json(payload)
            members["data/ai_history.json"] = raw
            manifest_value = json.loads(members["manifest.json"])
            manifest_value["entries"]["data/ai_history.json"] = {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
                "record_count": 1,
            }
            manifest_value["total_records"] += 1
            members["manifest.json"] = server_one.canonical_backup_json(manifest_value)

        check("nonempty_ai_history_rejected", rejected(server_one, rewrite_archive(archive_one, nonempty_ai_history)), failures)

        duplicate_path = temp_root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate_path, "w") as archive:
                for name, raw in members_one.items():
                    archive.writestr(name, raw)
                archive.writestr("data/notes.json", members_one["data/notes.json"])
        check("duplicate_zip_member_rejected", rejected(server_one, duplicate_path), failures)

        traversal_path = temp_root / "traversal.zip"
        with zipfile.ZipFile(traversal_path, "w") as archive:
            for name, raw in members_one.items():
                archive.writestr(name, raw)
            archive.writestr("../escape.json", b"{}")
        check("path_traversal_member_rejected", rejected(server_one, traversal_path), failures)

        backslash_path = temp_root / "backslash.zip"
        with zipfile.ZipFile(backslash_path, "w") as archive:
            for name, raw in members_one.items():
                archive.writestr("data\\notes.json" if name == "data/notes.json" else name, raw)
        check("backslash_member_rejected", rejected(server_one, backslash_path), failures)

        symlink_path = temp_root / "symlink.zip"
        with zipfile.ZipFile(symlink_path, "w") as archive:
            for name, raw in members_one.items():
                if name == "data/notes.json":
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                    archive.writestr(info, raw)
                else:
                    archive.writestr(name, raw)
        check("zip_symlink_member_rejected", rejected(server_one, symlink_path), failures)

        encrypted_path = temp_root / "encrypted.zip"
        encrypted_path.write_bytes(mark_zip_entries_encrypted(archive_one.read_bytes()))
        check("encrypted_zip_rejected", rejected(server_one, encrypted_path), failures)

        unsupported_compression_path = temp_root / "unsupported-compression.zip"
        with zipfile.ZipFile(unsupported_compression_path, "w", compression=zipfile.ZIP_BZIP2) as archive:
            for name, raw in members_one.items():
                archive.writestr(name, raw)
        check("unsupported_compression_rejected", rejected(server_one, unsupported_compression_path), failures)

        oversized = temp_root / "oversized.zip"
        with zipfile.ZipFile(oversized, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"x" * (server_one.BACKUP_MAX_ENTRY_BYTES + 1))
        check("oversized_member_rejected", rejected(server_one, oversized), failures)

        def checksum_mismatch(members: dict[str, bytes]) -> None:
            members["data/notes.json"] += b" "

        check("checksum_mismatch_rejected", rejected(server_one, rewrite_archive(archive_one, checksum_mismatch)), failures)

        def count_mismatch(members: dict[str, bytes]) -> None:
            value = json.loads(members["manifest.json"])
            value["entries"]["data/notes.json"]["record_count"] += 1
            members["manifest.json"] = server_one.canonical_backup_json(value)

        check("count_mismatch_rejected", rejected(server_one, rewrite_archive(archive_one, count_mismatch)), failures)

        def unexpected_member(members: dict[str, bytes]) -> None:
            members["payload.bin"] = b"unexpected"

        check("unexpected_member_rejected", rejected(server_one, rewrite_archive(archive_one, unexpected_member)), failures)

        def malformed_reference(members: dict[str, bytes]) -> None:
            payload = json.loads(members["data/notes.json"])
            payload["records"][0]["target_ref"] = "../bad"
            raw = server_one.canonical_backup_json(payload)
            members["data/notes.json"] = raw
            manifest_value = json.loads(members["manifest.json"])
            manifest_value["entries"]["data/notes.json"]["sha256"] = hashlib.sha256(raw).hexdigest()
            manifest_value["entries"]["data/notes.json"]["byte_size"] = len(raw)
            members["manifest.json"] = server_one.canonical_backup_json(manifest_value)

        check("malformed_stable_reference_rejected", rejected(server_one, rewrite_archive(archive_one, malformed_reference)), failures)

        failed_dir = server_one.DATA_DIR / "failure-export"
        original_validator = server_one.validate_backup_archive
        server_one.validate_backup_archive = lambda _path: (_ for _ in ()).throw(ValueError("synthetic validation failure"))
        try:
            try:
                server_one.export_user_owned_backup(conn_one, destination_dir=failed_dir)
            except ValueError:
                pass
        finally:
            server_one.validate_backup_archive = original_validator
        check("incomplete_temporary_export_never_published", not list(failed_dir.glob("*.zip")) and not list(failed_dir.glob("*.tmp")), failures)

        local_file_id = int(conn_one.execute("SELECT id FROM files WHERE stable_id='material_syn_local'").fetchone()["id"])
        conn_one.execute("UPDATE files SET display_name='' WHERE id=?", (local_file_id,))
        conn_one.commit()
        trap_database = temp_root / "init-db-regression.sqlite"
        trap_conn = sqlite3.connect(trap_database)
        trap_conn.row_factory = sqlite3.Row
        conn_one.backup(trap_conn)
        original_init_db = server_one.init_db
        original_init_db(trap_conn)
        init_db_would_reconcile = trap_conn.execute(
            "SELECT display_name FROM files WHERE id=?",
            (local_file_id,),
        ).fetchone()["display_name"] != ""
        trap_conn.close()

        init_db_calls = 0

        def reject_init_db(_conn: sqlite3.Connection) -> None:
            nonlocal init_db_calls
            init_db_calls += 1
            raise AssertionError("Backup entry points must not call init_db")

        server_one.init_db = reject_init_db
        before_http_dump = "\n".join(conn_one.iterdump())
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_one.StudyHubHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/backup/export",
                data=b"{}",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                    "X-StudyHub-CSRF": server_one.CSRF_TOKEN,
                },
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                api_payload = json.loads(response.read())
            check("backup_api_returns_safe_metadata_only", response.status == 200 and "path" not in json.dumps(api_payload).lower() and api_payload["backup"]["filename"].endswith(".zip"), failures)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        after_http_dump = "\n".join(conn_one.iterdump())
        check("http_export_does_not_call_init_db", init_db_calls == 0, failures)
        check("http_export_is_zero_db_mutation", before_http_dump == after_http_dump, failures)
        check(
            "http_export_does_not_reconcile_init_db_trap",
            init_db_would_reconcile
            and conn_one.execute("SELECT display_name FROM files WHERE id=?", (local_file_id,)).fetchone()["display_name"] == "",
            failures,
        )

        before_cli_dump = "\n".join(conn_one.iterdump())
        with redirect_stdout(io.StringIO()):
            cli_status = server_one.main(["backup"])
        after_cli_dump = "\n".join(conn_one.iterdump())
        check("cli_backup_uses_readonly_boundary", cli_status == 0 and init_db_calls == 0, failures)
        check("cli_backup_is_zero_db_mutation", before_cli_dump == after_cli_dump, failures)
        check("all_export_entry_points_avoid_credentials", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)

        check("profile_isolation_preserved", archive_one.parent == server_one.DATA_DIR / "exports" and archive_two.parent == server_two.DATA_DIR / "exports", failures)
        check("restore_apply_remains_separate_from_export", "def apply_staged_restore" in (ROOT / "server.py").read_text(encoding="utf-8"), failures)
        conn_one.close()
        conn_two.close()

    if failures:
        print("Backup export acceptance: FAIL")
        return 1
    print("Backup export acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
