#!/usr/bin/env python3
"""Synthetic acceptance for read-only backup restore validation and preview."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import warnings
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import (  # noqa: E402
    LOCAL_BYTES,
    archive_members,
    load_server,
    rewrite_archive,
    seed_state,
)


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def stage(server: Any, archive: Path, restore_id: str) -> Path:
    server.RESTORE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    staged = server.RESTORE_INBOX_DIR / f"{restore_id}.zip"
    shutil.copyfile(archive, staged)
    return staged


def export_fixture(server: Any, conn: sqlite3.Connection, created_at: str) -> Path:
    result = server.export_user_owned_backup(conn, created_at=created_at)
    return server.EXPORT_DIR / result["filename"]


def plan_actions(plan: dict[str, Any], dataset: str | None = None) -> list[dict[str, Any]]:
    actions = [action for section in plan["sections"] for action in section["actions"]]
    return [action for action in actions if dataset is None or action["dataset"] == dataset]


def contains_absolute_path(value: Any, roots: list[Path]) -> bool:
    serialized = json.dumps(value, sort_keys=True)
    return any(str(root) in serialized for root in roots) or "/Users/" in serialized or "/home/" in serialized


def mutate_dataset(
    server: Any,
    archive: Path,
    member: str,
    mutation: Callable[[dict[str, Any]], None],
) -> Path:
    def update(members: dict[str, bytes]) -> None:
        payload = json.loads(members[member])
        mutation(payload)
        raw = server.canonical_backup_json(payload)
        members[member] = raw
        manifest = json.loads(members["manifest.json"])
        manifest["entries"][member] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
            "record_count": len(payload["records"]),
        }
        manifest["total_records"] = sum(item["record_count"] for item in manifest["entries"].values())
        members["manifest.json"] = server.canonical_backup_json(manifest)

    return rewrite_archive(archive, update)


def write_archive(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
            for name, raw in members:
                output.writestr(name, raw)
    return path


def assert_preview_rejected_before_planning(
    server: Any,
    archive: Path,
    restore_id: str,
) -> bool:
    stage(server, archive, restore_id)
    planner_called = False
    original_planner = server.build_restore_plan

    def planner_trap(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal planner_called
        planner_called = True
        raise AssertionError("planner must not run for an invalid archive")

    server.build_restore_plan = planner_trap
    rejected = False
    try:
        server.preview_staged_restore(restore_id)
    except (ValueError, zipfile.BadZipFile):
        rejected = True
    finally:
        server.build_restore_plan = original_planner
    return rejected and not planner_called


def http_post(server: Any, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StudyHubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = int(httpd.server_address[1])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Host": f"localhost:{port}",
            "Origin": f"http://localhost:{port}",
            "X-StudyHub-CSRF": server.CSRF_TOKEN,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-restore-preview-") as temporary:
        base = Path(temporary)
        source_root = base / "source"
        match_root = base / "match"
        reverse_root = base / "reverse"
        empty_root = base / "empty"

        source = load_server(source_root)
        source_conn = seed_state(source, source_root, 100)
        source_conn.execute("UPDATE courses SET metadata_locked=1 WHERE stable_id='course_syn_remote'")
        source_conn.execute("UPDATE weeks SET origin='managed' WHERE stable_id='unit_syn_remote'")
        source_conn.commit()
        archive = export_fixture(source, source_conn, "2026-04-02T00:00:00+00:00")
        original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()

        match = load_server(match_root)
        match_conn = seed_state(match, match_root, 500, reverse_insert_order=True)
        match_conn.execute("UPDATE courses SET metadata_locked=1 WHERE stable_id='course_syn_remote'")
        match_conn.execute("UPDATE weeks SET origin='managed' WHERE stable_id='unit_syn_remote'")
        match_conn.commit()
        match_restore_id = "a" * 32
        staged = stage(match, archive, match_restore_id)

        check("pr28_merge_contract_present", hasattr(match, "connect_backup_db_readonly") and match.BACKUP_FORMAT_VERSION == 1, failures)
        rust_source = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
        check(
            "native_picker_boundary_is_narrow",
            "choose_backup_archive" in rust_source
            and "blocking_pick_file" in rust_source
            and "restore-inbox" in rust_source
            and "Demo/Test mode cannot select external backup archives" in rust_source,
            failures,
        )
        check(
            "native_picker_is_in_tauri_capability",
            '"choose_backup_archive"' in (ROOT / "src-tauri/build.rs").read_text(encoding="utf-8")
            and '"allow-choose-backup-archive"'
            in (ROOT / "src-tauri/capabilities/default.json").read_text(encoding="utf-8"),
            failures,
        )
        frontend_source = (ROOT / "static/app.js").read_text(encoding="utf-8")
        style_source = (ROOT / "static/styles.css").read_text(encoding="utf-8")
        catalogs = (ROOT / "static/i18n/catalog.js").read_text(encoding="utf-8")
        check(
            "settings_restore_preview_preserves_review_boundary",
            'data-choose-backup="1"' in frontend_source
            and 'data-discard-restore="1"' in frontend_source
            and "/api/backup/restore-preview" in frontend_source
            and "restoreRequiredDecision" in frontend_source,
            failures,
        )
        check(
            "restore_preview_accessibility_contract",
            'role="status"' in frontend_source
            and 'role="alert"' in frontend_source
            and "<details" in frontend_source
            and "focus-visible" in style_source,
            failures,
        )
        check(
            "restore_preview_english_and_chinese_catalogs",
            catalogs.count('"restore.title"') == 2
            and '"Restore from backup"' in catalogs
            and '"从备份恢复"' in catalogs,
            failures,
        )
        check("selected_archive_copied_into_profile_staging", staged.parent == match.RESTORE_INBOX_DIR and staged.read_bytes() == archive.read_bytes(), failures)
        check("original_selected_archive_untouched", hashlib.sha256(archive.read_bytes()).hexdigest() == original_hash, failures)

        blocked = {"canvas": 0, "openai": 0, "credentials": 0}

        def deny(kind: str) -> Callable[..., Any]:
            def fail(*_args: Any, **_kwargs: Any) -> Any:
                blocked[kind] += 1
                raise AssertionError(f"{kind} boundary called")

            return fail

        match.with_canvas_connector = deny("canvas")
        match.openai_responses_request = deny("openai")
        match.initialize_credential_client = deny("credentials")
        match.canvas_connection_record_from_private_transport = deny("credentials")

        before = "\n".join(match_conn.iterdump())
        init_calls = 0

        def reject_init(_conn: sqlite3.Connection) -> None:
            nonlocal init_calls
            init_calls += 1
            raise AssertionError("restore preview must not call init_db")

        match.init_db = reject_init
        plan = match.preview_staged_restore(match_restore_id)
        after = "\n".join(match_conn.iterdump())
        check("preview_zero_database_mutation", before == after, failures)
        check("preview_does_not_call_init_db", init_calls == 0, failures)
        check("preview_makes_no_network_or_credential_calls", blocked == {"canvas": 0, "openai": 0, "credentials": 0}, failures)
        readonly = match.connect_backup_db_readonly()
        try:
            write_failed = False
            try:
                readonly.execute("CREATE TABLE restore_preview_must_fail(id INTEGER)")
            except sqlite3.OperationalError:
                write_failed = True
        finally:
            readonly.close()
        check("preview_read_connection_cannot_write", write_failed, failures)

        actions = plan_actions(plan)
        check("exact_term_match", any(item["dataset"] == "terms" and item["classification"] == "NOOP" for item in actions), failures)
        check("course_stable_id_matching", any(item["dataset"] == "courses" and item["stable_id"] == "course_syn_alpha" and item["classification"] == "NOOP" for item in actions), failures)
        check("learning_unit_parent_dependency_satisfied", any(item["dataset"] == "learning_units" and item["stable_id"] == "unit_syn_alpha" and item["classification"] == "NOOP" for item in actions), failures)
        check("library_relative_source_exact_hash_match", any(item["dataset"] == "local_sources" and item["classification"] == "NOOP" for item in actions), failures)
        check("existing_star_and_bookmark_noop", sum(item["classification"] == "NOOP" for item in actions if item["dataset"] in {"stars", "study_records"} and item["label"] in {"Star", "Bookmark"}) == 2, failures)
        check("fully_equivalent_target_only_matches_or_noops", all(item["classification"] in {"NOOP", "MATCH"} for item in actions), failures)
        check("ai_history_reported_excluded", "ai_history_not_included" in plan["warnings"], failures)
        check("canvas_selection_reported_excluded", "canvas_state_not_included" in plan["warnings"], failures)
        check("no_remote_facts_planned_for_creation", not any(item["dataset"] in {"course_offerings", "canvas_sync_baselines", "academic_change_events"} for item in actions), failures)
        check("preview_response_contains_no_absolute_paths", not contains_absolute_path(plan, [source_root, match_root]), failures)
        check("private_target_state_tokens_not_exposed", "relevant_target_state" not in json.dumps(plan), failures)

        reverse = load_server(reverse_root)
        reverse_conn = seed_state(reverse, reverse_root, 900)
        reverse_conn.execute("UPDATE courses SET metadata_locked=1 WHERE stable_id='course_syn_remote'")
        reverse_conn.execute("UPDATE weeks SET origin='managed' WHERE stable_id='unit_syn_remote'")
        reverse_conn.execute("UPDATE terms SET created_at='2030-01-01T00:00:00+00:00', updated_at='2030-01-02T00:00:00+00:00'")
        reverse_conn.execute("UPDATE courses SET created_at='2030-01-03T00:00:00+00:00', updated_at='2030-01-04T00:00:00+00:00'")
        reverse_conn.execute("UPDATE weeks SET created_at='2030-01-05T00:00:00+00:00', updated_at='2030-01-06T00:00:00+00:00'")
        reverse_conn.commit()
        reverse_id = "b" * 32
        stage(reverse, archive, reverse_id)
        reverse_plan = reverse.preview_staged_restore(reverse_id)
        check("deterministic_restore_plan_revision", plan["plan_revision"] == reverse_plan["plan_revision"], failures)
        check("plan_revision_independent_of_ids_roots_and_session", plan["summary"] == reverse_plan["summary"] and plan["sections"] == reverse_plan["sections"], failures)

        def revision() -> str:
            return str(match.preview_staged_restore(match_restore_id)["plan_revision"])

        match_conn.execute("UPDATE terms SET name='Local B' WHERE stable_id='term_syn_alpha'")
        match_conn.commit()
        term_b = revision()
        match_conn.execute("UPDATE terms SET name='Local C' WHERE stable_id='term_syn_alpha'")
        match_conn.commit()
        term_c_plan = match.preview_staged_restore(match_restore_id)
        term_conflict = next(
            item for item in plan_actions(term_c_plan, "terms") if item["stable_id"] == "term_syn_alpha"
        )
        check(
            "conflicting_term_value_changes_revision",
            term_b != term_c_plan["plan_revision"]
            and term_conflict["classification"] == "CONFLICT"
            and term_conflict["differing_fields"] == ["name"],
            failures,
        )
        match_conn.execute("UPDATE terms SET name='Synthetic Term' WHERE stable_id='term_syn_alpha'")

        match_conn.execute("UPDATE courses SET display_name='Local B' WHERE stable_id='course_syn_alpha'")
        match_conn.commit()
        course_b = revision()
        match_conn.execute("UPDATE courses SET display_name='Local C' WHERE stable_id='course_syn_alpha'")
        match_conn.commit()
        course_c = revision()
        check("conflicting_course_value_changes_revision", course_b != course_c, failures)
        match_conn.execute("UPDATE courses SET display_name='My Synthetic Course' WHERE stable_id='course_syn_alpha'")

        match_conn.execute("UPDATE files SET display_name='Local B' WHERE stable_id='material_syn_local'")
        match_conn.commit()
        overlay_b = revision()
        match_conn.execute("UPDATE files SET display_name='Local C' WHERE stable_id='material_syn_local'")
        match_conn.commit()
        overlay_c = revision()
        check("conflicting_material_overlay_value_changes_revision", overlay_b != overlay_c, failures)
        match_conn.execute("UPDATE files SET display_name='My guide' WHERE stable_id='material_syn_local'")

        match_conn.execute("UPDATE bookmarks SET label='Local B'")
        match_conn.commit()
        bookmark_b = revision()
        match_conn.execute("UPDATE bookmarks SET label='Local C'")
        match_conn.commit()
        bookmark_c = revision()
        check("conflicting_bookmark_value_changes_revision", bookmark_b != bookmark_c, failures)
        match_conn.execute("UPDATE bookmarks SET label='Review this'")

        wrong_snapshot = [
            tuple(row)
            for row in match_conn.execute(
                "SELECT id, mistake, concept, attempts, last_reviewed, mastery FROM wrong_questions ORDER BY id"
            ).fetchall()
        ]
        match_conn.execute("UPDATE wrong_questions SET mastery='local-learning'")
        match_conn.commit()
        wrong_learning = revision()
        match_conn.execute("UPDATE wrong_questions SET mastery='local-mastered'")
        match_conn.commit()
        wrong_mastered = revision()
        check("wrong_question_mutable_value_changes_revision", wrong_learning != wrong_mastered, failures)
        match_conn.executemany(
            "UPDATE wrong_questions SET mistake=?, concept=?, attempts=?, last_reviewed=?, mastery=? WHERE id=?",
            [(mistake, concept, attempts, reviewed, mastery, row_id) for row_id, mistake, concept, attempts, reviewed, mastery in wrong_snapshot],
        )

        attempt = match_conn.execute(
            "SELECT id, file_id, prompt, answer_text, result, created_at FROM attempts ORDER BY id LIMIT 1"
        ).fetchone()
        match_conn.commit()
        attempts_full = revision()
        match_conn.execute("DELETE FROM attempts WHERE id=?", (attempt["id"],))
        match_conn.commit()
        attempts_reduced = revision()
        check("duplicate_attempt_multiplicity_changes_revision", attempts_full != attempts_reduced, failures)
        match_conn.execute(
            "INSERT INTO attempts(id, file_id, prompt, answer_text, result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(attempt),
        )

        match_conn.execute("UPDATE academic_change_event_state SET seen_at='2026-04-01T00:00:00+00:00'")
        match_conn.commit()
        seen_revision = revision()
        match_conn.execute("UPDATE academic_change_event_state SET seen_at=NULL")
        match_conn.commit()
        unseen_revision = revision()
        check("whats_new_seen_state_changes_revision", seen_revision != unseen_revision, failures)
        match_conn.execute("UPDATE academic_change_event_state SET seen_at='2026-04-01T00:00:00+00:00'")

        material_version = match_conn.execute(
            """
            SELECT mv.id, mv.sha256, mv.byte_size
            FROM material_versions mv JOIN materials m ON m.current_version_id=mv.id
            WHERE m.stable_id='material_syn_local'
            """
        ).fetchone()
        match_conn.commit()
        match_conn.execute("UPDATE material_versions SET sha256=? WHERE id=?", ("b" * 64, material_version["id"]))
        match_conn.commit()
        material_b = revision()
        match_conn.execute("UPDATE material_versions SET sha256=? WHERE id=?", ("c" * 64, material_version["id"]))
        match_conn.commit()
        material_c = revision()
        check("current_material_content_identity_changes_revision", material_b != material_c, failures)
        match_conn.execute(
            "UPDATE material_versions SET sha256=?, byte_size=? WHERE id=?",
            (material_version["sha256"], material_version["byte_size"], material_version["id"]),
        )
        match_conn.commit()

        source_file = match_root / "library/SYN9200 - Synthetic Course/Module Alpha/Lecture/synthetic-guide.txt"
        source_bytes = source_file.read_bytes()
        source_file.write_bytes(b"B" * len(source_bytes))
        source_b = revision()
        source_file.write_bytes(b"C" * len(source_bytes))
        source_c = revision()
        check("explicit_source_candidate_bytes_change_revision", source_b != source_c, failures)
        source_file.write_bytes(source_bytes)
        match_conn.commit()

        replacement_archive = mutate_dataset(
            source,
            archive,
            "data/terms.json",
            lambda payload: payload["records"][0].update({"name": "Replacement archive"}),
        )
        original_snapshot_reader = match.read_backup_archive_snapshot

        def replace_after_snapshot(path: Path) -> bytes:
            snapshot = original_snapshot_reader(path)
            Path(path).write_bytes(replacement_archive.read_bytes())
            return snapshot

        match.read_backup_archive_snapshot = replace_after_snapshot
        staged.write_bytes(archive.read_bytes())
        try:
            snapshot_a_plan = match.preview_staged_restore(match_restore_id)
        finally:
            match.read_backup_archive_snapshot = original_snapshot_reader
        snapshot_b_plan = match.preview_staged_restore(match_restore_id)
        check(
            "archive_replacement_uses_one_validated_snapshot",
            snapshot_a_plan["plan_revision"] == plan["plan_revision"]
            and snapshot_b_plan["plan_revision"] != snapshot_a_plan["plan_revision"],
            failures,
        )

        malformed_replacement = base / "post-snapshot-malformed.zip"
        malformed_replacement.write_bytes(b"not-a-zip")

        def replace_with_malformed_after_snapshot(path: Path) -> bytes:
            snapshot = original_snapshot_reader(path)
            Path(path).write_bytes(malformed_replacement.read_bytes())
            return snapshot

        match.read_backup_archive_snapshot = replace_with_malformed_after_snapshot
        staged.write_bytes(archive.read_bytes())
        try:
            snapshot_before_malformed = match.preview_staged_restore(match_restore_id)
        finally:
            match.read_backup_archive_snapshot = original_snapshot_reader
        malformed_next_rejected = False
        try:
            match.preview_staged_restore(match_restore_id)
        except ValueError:
            malformed_next_rejected = True
        check(
            "malformed_replacement_cannot_cross_validation_boundary",
            snapshot_before_malformed["plan_revision"] == plan["plan_revision"] and malformed_next_rejected,
            failures,
        )
        staged.write_bytes(archive.read_bytes())

        empty = load_server(empty_root)
        empty_conn = empty.connect_db()
        empty.init_db(empty_conn)
        empty_conn.commit()
        empty_conn.execute(
            """
            INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name,
              path, source_folder, source_kind, archived, active, sort_order, metadata_locked,
              created_at, updated_at)
            VALUES ('course_same_name_only', 'SYN9200', 'SYN9200', 'Synthetic Course',
              'My Synthetic Course', 'same-name-only', '', '', 'managed', 0, 1, 0, 1, '', '')
            """
        )
        empty_conn.commit()
        empty_id = "c" * 32
        stage(empty, archive, empty_id)
        empty_plan = empty.preview_staged_restore(empty_id)
        empty_actions = plan_actions(empty_plan)
        check("new_term_create_candidate", any(item["dataset"] == "terms" and item["stable_id"] == "term_syn_alpha" and item["classification"] == "CREATE" for item in empty_actions), failures)
        check("no_fuzzy_course_merge", any(item["dataset"] == "courses" and item["stable_id"] == "course_syn_alpha" and item["classification"] == "CREATE" for item in empty_actions), failures)
        check("remote_overlay_does_not_recreate_canvas_facts", any(item["dataset"] == "courses" and item["stable_id"] == "course_syn_remote" and item["classification"] == "UNRESOLVED_REFERENCE" for item in empty_actions), failures)
        check("unresolved_parent_blocks_learning_unit", any(item["dataset"] == "learning_units" and item["stable_id"] == "unit_syn_remote" and item["classification"] == "UNRESOLVED_REFERENCE" for item in empty_actions), failures)
        check("missing_source_becomes_relink_required", any(item["dataset"] == "local_sources" and item["classification"] == "RELINK_REQUIRED" for item in empty_actions), failures)
        check("material_references_not_silently_skipped", any(item["classification"] in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE"} for item in empty_actions if item["dataset"] in {"material_overlays", "user_records", "stars"}), failures)
        check("missing_academic_event_not_manufactured", any(item["dataset"] == "whats_new_state" and item["classification"] == "UNSUPPORTED_BY_V1" for item in empty_actions), failures)

        source_file.unlink()
        missing_plan = match.preview_staged_restore(match_restore_id)
        check("existing_material_missing_file_still_requires_relink", any(item["dataset"] == "local_sources" and item["classification"] == "RELINK_REQUIRED" for item in plan_actions(missing_plan)), failures)
        source_file.write_bytes(b"DIFFERENT-SYNTHETIC-BYTES")
        conflict_plan = match.preview_staged_restore(match_restore_id)
        check("same_path_different_bytes_is_conflict", any(item["dataset"] == "local_sources" and item["classification"] == "CONFLICT" for item in plan_actions(conflict_plan)), failures)
        source_file.write_bytes(LOCAL_BYTES)

        match_conn.execute("UPDATE files SET metadata_locked=0 WHERE stable_id='material_syn_local'")
        match_conn.commit()
        overlay_plan = match.preview_staged_restore(match_restore_id)
        check("material_overlay_safe_update_candidate", any(item["dataset"] == "material_overlays" and item["classification"] == "UPDATE_USER_STATE" for item in plan_actions(overlay_plan)), failures)
        match_conn.execute("UPDATE files SET metadata_locked=1 WHERE stable_id='material_syn_local'")
        match_conn.execute("DELETE FROM notes WHERE id=(SELECT MAX(id) FROM notes)")
        match_conn.execute("DELETE FROM attempts WHERE id=(SELECT MAX(id) FROM attempts)")
        match_conn.execute("DELETE FROM study_sessions WHERE id=(SELECT MAX(id) FROM study_sessions)")
        match_conn.execute("UPDATE wrong_questions SET mastery='mastered', attempts=99 WHERE id=(SELECT MIN(id) FROM wrong_questions)")
        match_conn.execute("DELETE FROM stars")
        match_conn.execute("DELETE FROM bookmarks")
        match_conn.execute("UPDATE academic_change_event_state SET seen_at=NULL")
        match_conn.commit()
        partial_plan = match.preview_staged_restore(match_restore_id)
        partial_actions = plan_actions(partial_plan)
        check("duplicate_notes_preserve_multiplicity", any(item["dataset"] == "notes" and item["classification"] == "CREATE" for item in partial_actions), failures)
        check("duplicate_attempts_preserve_multiplicity", any(item["label"] == "Attempt" and item["classification"] == "CREATE" for item in partial_actions), failures)
        check("duplicate_sessions_preserve_multiplicity", any(item["label"] == "Study session" and item["classification"] == "CREATE" for item in partial_actions), failures)
        check("wrong_question_mutable_state_conflict", any(item["label"] == "Wrong question" and item["classification"] == "CONFLICT" and "mastery" in item["differing_fields"] for item in partial_actions), failures)
        check("missing_star_and_bookmark_create", sum(item["classification"] == "CREATE" for item in partial_actions if item["label"] in {"Star", "Bookmark"}) == 2, failures)
        check("whats_new_ack_reattaches_only_existing_event", any(item["dataset"] == "whats_new_state" and item["classification"] == "UPDATE_USER_STATE" for item in partial_actions), failures)
        check("conflicting_local_state_not_overwritten", partial_plan["summary"]["conflict"] > 0 and "restore_apply" not in json.dumps(partial_plan), failures)

        def make_relink(payload: dict[str, Any]) -> None:
            payload["records"][0]["location"] = {"kind": "relink_required", "basename": "synthetic-guide.txt"}

        relink_archive = mutate_dataset(source, archive, "data/local_sources.json", make_relink)
        relink_id = "d" * 32
        stage(match, relink_archive, relink_id)
        relink_plan = match.preview_staged_restore(relink_id)
        check("external_relink_required_remains_unresolved", any(item["dataset"] == "local_sources" and item["classification"] == "RELINK_REQUIRED" for item in plan_actions(relink_plan)), failures)

        malformed = base / "malformed.zip"
        malformed.write_bytes(b"not-a-zip")
        check(
            "malformed_archive_rejected_before_planning",
            assert_preview_rejected_before_planning(match, malformed, "e" * 32),
            failures,
        )

        valid_members = archive_members(archive)

        def future_version(members: dict[str, bytes]) -> None:
            manifest = json.loads(members["manifest.json"])
            manifest["format_version"] = 999
            members["manifest.json"] = match.canonical_backup_json(manifest)

        def checksum_mismatch(members: dict[str, bytes]) -> None:
            members["data/notes.json"] += b" "

        def nonempty_connector(payload: dict[str, Any]) -> None:
            payload["records"] = [{"stable_id": "connector_state_disallowed"}]

        def nonempty_ai(payload: dict[str, Any]) -> None:
            payload["records"] = [{"stable_id": "ai_history_disallowed"}]

        def invalid_reference(payload: dict[str, Any]) -> None:
            payload["records"][0]["target_ref"] = "../invalid"

        invalid_archives = {
            "unsupported_version": rewrite_archive(archive, future_version),
            "checksum_mismatch": rewrite_archive(archive, checksum_mismatch),
            "nonempty_connector_state": mutate_dataset(
                source, archive, "data/connector_user_state.json", nonempty_connector
            ),
            "nonempty_ai_history": mutate_dataset(
                source, archive, "data/ai_history.json", nonempty_ai
            ),
            "invalid_stable_reference": mutate_dataset(
                source, archive, "data/notes.json", invalid_reference
            ),
            "path_traversal": write_archive(
                base / "traversal.zip",
                [*valid_members.items(), ("../escape.json", b"{}")],
            ),
            "duplicate_member": write_archive(
                base / "duplicate.zip",
                [*valid_members.items(), ("data/notes.json", valid_members["data/notes.json"])],
            ),
        }
        for index, (name, invalid_archive) in enumerate(invalid_archives.items(), start=1):
            check(
                f"{name}_rejected_before_planning",
                assert_preview_rejected_before_planning(match, invalid_archive, f"{index:032x}"),
                failures,
            )

        staged_symlink = match.RESTORE_INBOX_DIR / f"{'8' * 32}.zip"
        staged_symlink.symlink_to(archive)
        symlink_rejected = False
        try:
            match.preview_staged_restore("8" * 32)
        except (FileNotFoundError, PermissionError, ValueError):
            symlink_rejected = True
        finally:
            staged_symlink.unlink(missing_ok=True)
        check("staged_archive_symlink_rejected", symlink_rejected, failures)

        old_revision = plan["plan_revision"]
        staged.write_bytes(b"tampered")
        tamper_rejected = False
        try:
            match.preview_staged_restore(match_restore_id)
        except ValueError:
            tamper_rejected = True
        check("staged_archive_tampering_invalidates_old_plan", tamper_rejected and bool(old_revision), failures)
        staged.write_bytes(archive.read_bytes())

        status, arbitrary = http_post(match, "/api/backup/restore-preview", {"path": str(archive)})
        check("browser_cannot_submit_arbitrary_path", status == 400 and "path" not in json.dumps(arbitrary), failures)
        status, response = http_post(match, "/api/backup/restore-preview", {"restore_id": match_restore_id})
        response_plan = response.get("plan", {})
        check(
            "restore_preview_api_returns_safe_plan",
            status == 200
            and bool(response_plan.get("plan_revision"))
            and not contains_absolute_path(response_plan, [source_root, match_root]),
            failures,
        )
        check("staging_not_static_or_mcp_exposed", "/restore-inbox" not in (ROOT / "server.py").read_text(encoding="utf-8").split("def serve_static", 1)[-1] and "restore" not in json.dumps(match.mcp_tool_descriptors()), failures)
        discard_id = "f" * 32
        discard_copy = stage(match, archive, discard_id)
        status, discard = http_post(match, "/api/backup/restore-discard", {"restore_id": discard_id})
        check("discard_removes_only_staged_copy", status == 200 and discard.get("discarded") == discard_id and not discard_copy.exists() and archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest() == original_hash, failures)

        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        check("restore_apply_is_separate_narrow_endpoint", 'parsed.path == "/api/backup/restore-apply"' in server_source, failures)
        check("no_schema_change_for_preview", len(match.SCHEMA_MIGRATIONS) == 5, failures)

        source_conn.close()
        match_conn.close()
        reverse_conn.close()
        empty_conn.close()

    if failures:
        print("Backup restore preview acceptance: FAIL")
        return 1
    print("Backup restore preview acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
