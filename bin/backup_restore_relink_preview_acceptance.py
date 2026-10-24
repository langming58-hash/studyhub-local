#!/usr/bin/env python3
"""Synthetic acceptance for explicit zero-mutation restore source relink preview."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import LOCAL_BYTES, load_server, seed_state  # noqa: E402
from backup_restore_apply_acceptance import decisions_for  # noqa: E402
from backup_restore_preview_acceptance import (  # noqa: E402
    export_fixture,
    http_post,
    mutate_dataset,
    plan_actions,
    stage,
)


def check(name: str, passed: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        failures.append(name)


def expect_code(operation: Callable[[], Any], code: str) -> bool:
    try:
        operation()
    except Exception as exc:  # acceptance checks sanitized public failure codes
        return str(exc) == code
    return False


def db_snapshot(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def write_capability(
    server: Any,
    *,
    relink_id: str,
    restore_id: str,
    source_stable_id: str,
    selected: Path,
    display_name: str | None = None,
) -> Path:
    server.RESTORE_RELINK_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        server.RESTORE_RELINK_INBOX_DIR.chmod(0o700)
    descriptor = server.RESTORE_RELINK_INBOX_DIR / f"{relink_id}.json"
    descriptor.write_text(
        json.dumps(
            {
                "capability_version": 1,
                "restore_id": restore_id,
                "source_stable_id": source_stable_id,
                "selected_path": str(selected),
                "display_name": display_name or selected.name,
                "created_at": 1_800_000_000,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        descriptor.chmod(0o600)
    return descriptor


def relink_archive(server: Any, archive: Path) -> Path:
    def mutate(payload: dict[str, Any]) -> None:
        payload["records"][0]["location"] = {
            "kind": "relink_required",
            "basename": payload["records"][0]["display_name"],
        }
        payload["records"][0]["missing"] = True

    return mutate_dataset(server, archive, "data/local_sources.json", mutate)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-relink-preview-") as temporary:
        base = Path(temporary)
        source_root = base / "source-profile"
        target_root = base / "target-profile"
        other_root = base / "other-profile"
        candidates = base / "user-owned-originals"
        candidates.mkdir(parents=True)

        source = load_server(source_root)
        source_conn = seed_state(source, source_root, 100)
        archive = export_fixture(source, source_conn, "2026-05-01T00:00:00+00:00")
        archive = relink_archive(source, archive)

        target = load_server(target_root)
        target_conn = seed_state(target, target_root, 500, reverse_insert_order=True)
        restore_id = "a" * 32
        stage(target, archive, restore_id)
        plan = target.preview_staged_restore(restore_id)
        source_action = next(
            action
            for action in plan_actions(plan, "local_sources")
            if action["classification"] == "RELINK_REQUIRED"
        )
        source_stable_id = source_action["stable_id"]
        _validation, _manifest, datasets = target.read_validated_backup_archive(
            target.staged_restore_path(restore_id)
        )
        source_record = next(
            record
            for record in datasets["data/local_sources.json"]
            if record["stable_id"] == source_stable_id
        )
        expected_size = int(source_record["byte_size"])
        expected_sha = str(source_record["sha256"])
        exact_bytes = LOCAL_BYTES
        check("fixture_bytes_match_backup_identity", len(exact_bytes) == expected_size and hashlib.sha256(exact_bytes).hexdigest() == expected_sha, failures)

        exact_file = candidates / "exact-source.txt"
        exact_file.write_bytes(exact_bytes)
        exact_original_hash = hashlib.sha256(exact_file.read_bytes()).hexdigest()
        exact_descriptor = write_capability(
            target,
            relink_id="1" * 32,
            restore_id=restore_id,
            source_stable_id=source_stable_id,
            selected=exact_file,
        )

        rust = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
        frontend = (ROOT / "static/app.js").read_text(encoding="utf-8")
        catalog = (ROOT / "static/i18n/catalog.js").read_text(encoding="utf-8")
        build_rs = (ROOT / "src-tauri/build.rs").read_text(encoding="utf-8")
        capability = (ROOT / "src-tauri/capabilities/default.json").read_text(encoding="utf-8")
        check("pr30_merge_foundation_present", hasattr(target, "apply_staged_restore") and hasattr(target, "verify_restore_source_candidates"), failures)
        check("native_picker_command_exists", "choose_restore_relink_file" in rust and '"choose_restore_relink_file"' in build_rs, failures)
        check("native_picker_minimum_permission", '"allow-choose-restore-relink-file"' in capability, failures)
        check("browser_receives_no_absolute_path", "selected_path" not in frontend and "selected_path" not in rust.split("Ok((relink_id", 1)[-1], failures)
        check("http_endpoint_accepts_no_path", "Restore relink preview accepts only restore_id, expected_plan_revision, source_stable_id, and relink_id" in (ROOT / "server.py").read_text(encoding="utf-8"), failures)
        check("opaque_relink_id_only", len("1" * 32) == 32 and "/api/backup/restore-relink-preview" in frontend, failures)

        network_calls = {"canvas": 0, "openai": 0, "credential": 0, "index": 0}

        def deny(kind: str) -> Callable[..., Any]:
            def blocked(*_args: Any, **_kwargs: Any) -> Any:
                network_calls[kind] += 1
                raise AssertionError(f"{kind} boundary called")

            return blocked

        target.with_canvas_connector = deny("canvas")
        target.openai_responses_request = deny("openai")
        target.initialize_credential_client = deny("credential")
        target.extract_and_cache = deny("index")
        before_db = db_snapshot(target_conn)
        exact = target.preview_restore_relink_candidate(
            restore_id,
            plan["plan_revision"],
            source_stable_id,
            "1" * 32,
        )
        after_db = db_snapshot(target_conn)
        check("exact_sha_and_size_ready", exact["status"] == "RELINK_READY" and exact["exact_match"] is True, failures)
        check("relink_ready_is_preview_only", exact["apply_available"] is False, failures)
        check("response_contains_no_absolute_path", str(base) not in json.dumps(exact) and "selected_path" not in exact, failures)
        check("selected_original_untouched", hashlib.sha256(exact_file.read_bytes()).hexdigest() == exact_original_hash, failures)
        check("zero_database_mutation", before_db == after_db, failures)
        check("no_network_credentials_or_indexing", not any(network_calls.values()), failures)

        mismatch_same_name = candidates / "other" / exact_file.name
        mismatch_same_name.parent.mkdir()
        mismatch_same_name.write_bytes(b"X" * expected_size)
        write_capability(target, relink_id="2" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=mismatch_same_name)
        same_name_result = target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "2" * 32)
        check("same_basename_different_bytes_mismatch", same_name_result["status"] == "RELINK_MISMATCH", failures)
        check("same_size_different_sha_mismatch", same_name_result["candidate_byte_size"] == expected_size and not same_name_result["exact_match"], failures)

        different_size = candidates / "different-size.txt"
        different_size.write_bytes(b"different-size")
        write_capability(target, relink_id="3" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=different_size)
        size_result = target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "3" * 32)
        check("different_size_mismatch", size_result["status"] == "RELINK_MISMATCH" and size_result["candidate_byte_size"] != expected_size, failures)

        duplicate_exact = candidates / "duplicate-exact.txt"
        duplicate_exact.write_bytes(exact_bytes)
        write_capability(target, relink_id="4" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=duplicate_exact)
        duplicate_result = target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "4" * 32)
        check("candidate_revision_deterministic", exact["relink_revision"] == duplicate_result["relink_revision"], failures)
        check("revision_excludes_path_and_random_id", str(exact_file) not in exact["relink_revision"] and "1" * 32 not in exact["relink_revision"], failures)
        check("changed_candidate_changes_revision", exact["relink_revision"] != same_name_result["relink_revision"], failures)

        exact_file.write_bytes(b"Y" * expected_size)
        changed = target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "1" * 32)
        check("post_preview_source_change_not_claimed_safe", changed["status"] == "RELINK_MISMATCH" and changed["relink_revision"] != exact["relink_revision"], failures)
        exact_file.write_bytes(exact_bytes)

        check("capability_bound_to_restore", expect_code(lambda: target.load_restore_relink_capability("1" * 32, restore_id="b" * 32, source_stable_id=source_stable_id), "restore_relink_capability_mismatch"), failures)
        wrong_source_id = "source:wrong-binding"
        check("capability_bound_to_source", expect_code(lambda: target.load_restore_relink_capability("1" * 32, restore_id=restore_id, source_stable_id=wrong_source_id), "restore_relink_capability_mismatch"), failures)

        other = load_server(other_root)
        check("capability_cannot_cross_profiles", expect_code(lambda: other.load_restore_relink_capability("1" * 32, restore_id=restore_id, source_stable_id=source_stable_id), "restore_relink_capability_invalid"), failures)
        check("demo_test_picker_denied", "Demo/Test mode cannot select external restore sources" in rust, failures)

        unsupported = candidates / "unsupported.exe"
        unsupported.write_bytes(exact_bytes)
        write_capability(target, relink_id="5" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=unsupported)
        check("unsupported_extension_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "5" * 32), "restore_relink_source_unsupported"), failures)

        directory = candidates / "directory.pdf"
        directory.mkdir()
        write_capability(target, relink_id="6" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=directory)
        check("directory_candidate_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "6" * 32), "restore_relink_source_invalid"), failures)
        if hasattr(os, "symlink"):
            symlink = candidates / "symlink.pdf"
            symlink.symlink_to(duplicate_exact)
            write_capability(target, relink_id="7" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=symlink, display_name=symlink.name)
            check("symlink_candidate_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "7" * 32), "restore_relink_source_invalid"), failures)

        private_candidate = target.CACHE_DIR / "private.pdf"
        private_candidate.parent.mkdir(parents=True, exist_ok=True)
        private_candidate.write_bytes(exact_bytes)
        write_capability(target, relink_id="8" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=private_candidate)
        check("runtime_candidate_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "8" * 32), "restore_relink_source_private"), failures)
        canvas_candidate = target.canvas_remote_content_root() / "retained.pdf"
        canvas_candidate.parent.mkdir(parents=True, exist_ok=True)
        canvas_candidate.write_bytes(exact_bytes)
        write_capability(target, relink_id="9" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=canvas_candidate)
        check("canvas_retained_candidate_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "9" * 32), "restore_relink_source_private"), failures)

        status, response = http_post(target, "/api/backup/restore-relink-preview", {"restore_id": restore_id, "expected_plan_revision": plan["plan_revision"], "source_stable_id": source_stable_id, "relink_id": "4" * 32, "path": str(duplicate_exact)})
        check("browser_path_override_rejected", status == 400 and "path" not in response, failures)

        target_conn.execute("UPDATE courses SET display_name='Changed after preview' WHERE stable_id='course_syn_alpha'")
        target_conn.commit()
        check("stale_target_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, plan["plan_revision"], source_stable_id, "4" * 32), "stale_restore_plan"), failures)
        refreshed_plan = target.preview_staged_restore(restore_id)

        def change_backup_source(payload: dict[str, Any]) -> None:
            payload["records"][0]["location"] = {
                "kind": "relink_required",
                "basename": "changed-source-label.txt",
            }
            payload["records"][0]["missing"] = True
            payload["records"][0]["display_name"] = "Changed source label"

        replacement_archive = mutate_dataset(
            source,
            archive,
            "data/local_sources.json",
            change_backup_source,
        )
        stage(target, replacement_archive, restore_id)
        check("changed_backup_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, refreshed_plan["plan_revision"], source_stable_id, "4" * 32), "stale_restore_plan"), failures)
        stage(target, archive, restore_id)
        current_plan = target.preview_staged_restore(restore_id)

        malformed = target.RESTORE_RELINK_INBOX_DIR / f"{'c' * 32}.json"
        malformed.write_text("not-json", encoding="utf-8")
        if os.name != "nt":
            malformed.chmod(0o600)
        check("malformed_capability_rejected", expect_code(lambda: target.preview_restore_relink_candidate(restore_id, current_plan["plan_revision"], source_stable_id, "c" * 32), "restore_relink_capability_invalid"), failures)

        staged_archive = target.staged_restore_path(restore_id)
        valid_archive_bytes = staged_archive.read_bytes()
        staged_archive.write_bytes(b"not-a-backup")
        malformed_backup_rejected = False
        try:
            target.preview_restore_relink_candidate(
                restore_id,
                current_plan["plan_revision"],
                source_stable_id,
                "4" * 32,
            )
        except (ValueError, zipfile.BadZipFile):
            malformed_backup_rejected = True
        finally:
            staged_archive.write_bytes(valid_archive_bytes)
        check("malformed_backup_rejected", malformed_backup_rejected, failures)

        normal_plan = target.preview_staged_restore(restore_id)
        check("normal_plan_remains_relink_required", any(action["stable_id"] == source_stable_id and action["classification"] == "RELINK_REQUIRED" for action in plan_actions(normal_plan, "local_sources")), failures)
        relink_action = next(action for action in plan_actions(normal_plan, "local_sources") if action["stable_id"] == source_stable_id)
        check("current_apply_still_skip_only", expect_code(lambda: target.normalize_restore_decisions([{"dataset": "local_sources", "stable_id": source_stable_id, "decision": "RELINK"}], [relink_action]), "unsupported_restore_decision") and target.normalize_restore_decisions([{"dataset": "local_sources", "stable_id": source_stable_id, "decision": "SKIP"}], [relink_action])[("local_sources", source_stable_id)] == "SKIP", failures)

        discard_candidate = candidates / "discard-survives.txt"
        discard_candidate.write_bytes(exact_bytes)
        discard_original = hashlib.sha256(discard_candidate.read_bytes()).hexdigest()
        discard_descriptor = write_capability(target, relink_id="d" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=discard_candidate)
        target.discard_restore_relink_capability(restore_id, source_stable_id, "d" * 32)
        check("capability_discard_descriptor_only", not discard_descriptor.exists(), failures)
        check("original_survives_capability_discard", discard_candidate.exists() and hashlib.sha256(discard_candidate.read_bytes()).hexdigest() == discard_original, failures)

        restore_discard_file = candidates / "restore-discard-survives.txt"
        restore_discard_file.write_bytes(exact_bytes)
        associated = write_capability(target, relink_id="e" * 32, restore_id=restore_id, source_stable_id=source_stable_id, selected=restore_discard_file)
        target.discard_staged_restore(restore_id)
        check("restore_discard_invalidates_capability", not associated.exists(), failures)
        check("restore_discard_preserves_original", restore_discard_file.exists(), failures)

        check("english_and_chinese_ui_strings", catalog.count('"restore.chooseReplacementFile"') == 2 and "Choose replacement file" in catalog and "选择替代文件" in catalog, failures)
        check("accessible_busy_and_controls", 'aria-busy=\\"true\\"' in frontend and "data-discard-restore-relink" in frontend and "data-choose-restore-relink" in frontend, failures)
        check("no_relink_now_control", "Relink now" not in frontend and "立即重新关联" not in frontend, failures)
        check("no_database_schema_change", "restore_relink" not in json.dumps([row[0] for row in target_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]), failures)

        # Keep imports used and prove existing decision generation remains valid for the complete plan.
        check("existing_safe_apply_decisions_remain_constructible", len(decisions_for(normal_plan)) >= 1, failures)

        source_conn.close()
        target_conn.close()

    if failures:
        print("Backup restore relink preview acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore relink preview acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
