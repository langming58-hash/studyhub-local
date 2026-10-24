#!/usr/bin/env python3
"""Synthetic acceptance for transactional explicit restore source relink apply."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from backup_export_acceptance import LOCAL_BYTES, load_server, seed_state  # noqa: E402
from backup_restore_relink_preview_acceptance import relink_archive, write_capability  # noqa: E402
from backup_restore_preview_acceptance import export_fixture, http_post, mutate_dataset, stage  # noqa: E402


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


def logical_dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def decisions_for(plan: dict[str, Any], source_id: str) -> list[dict[str, str]]:
    decisions: list[dict[str, str]] = []
    for section in plan["sections"]:
        for action in section["actions"]:
            classification = action["classification"]
            if classification == "CONFLICT":
                decision = "KEEP_LOCAL"
            elif classification == "RELINK_REQUIRED" and action["dataset"] == "local_sources" and action["stable_id"] == source_id:
                decision = "RELINK"
            elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                decision = "SKIP"
            else:
                continue
            decisions.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return decisions


def fixture(
    base: Path,
    suffix: str,
    *,
    seed_target: bool = True,
) -> tuple[Any, sqlite3.Connection, str, str, Path, dict[str, Any]]:
    source_root = base / f"source-{suffix}"
    target_root = base / f"target-{suffix}"
    source = load_server(source_root)
    source_conn = seed_state(source, source_root, 100)
    archive = relink_archive(source, export_fixture(source, source_conn, "2026-05-01T00:00:00+00:00"))
    target = load_server(target_root)
    if seed_target:
        target_conn = seed_state(target, target_root, 500, reverse_insert_order=True)
    else:
        target_conn = target.connect_db()
        target.init_db(target_conn)
        target_conn.commit()
    restore_id = hashlib.sha256(suffix.encode()).hexdigest()[:32]
    stage(target, archive, restore_id)
    plan = target.preview_staged_restore(restore_id)
    source_action = next(
        action
        for section in plan["sections"]
        if section["dataset"] == "local_sources"
        for action in section["actions"]
        if action["classification"] == "RELINK_REQUIRED"
    )
    source_id = str(source_action["stable_id"])
    selected = base / f"selected-{suffix}" / "synthetic-guide.txt"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(LOCAL_BYTES)
    relink_id = hashlib.sha256(f"relink-{suffix}".encode()).hexdigest()[:32]
    write_capability(
        target,
        relink_id=relink_id,
        restore_id=restore_id,
        source_stable_id=source_id,
        selected=selected,
    )
    relink = target.preview_restore_relink_candidate(
        restore_id, plan["plan_revision"], source_id, relink_id
    )
    request = {
        "restore_id": restore_id,
        "expected_plan_revision": relink["plan"]["plan_revision"],
        "decisions": decisions_for(relink["plan"], source_id),
        "relinks": [{
            "source_stable_id": source_id,
            "relink_id": relink_id,
            "relink_revision": relink["relink_revision"],
        }],
    }
    return target, target_conn, restore_id, source_id, selected, request


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-relink-apply-") as temporary:
        base = Path(temporary)

        target, conn, restore_id, source_id, selected, request = fixture(base, "success")
        original_hash = hashlib.sha256(selected.read_bytes()).hexdigest()
        before_counts = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("sources", "materials", "material_versions", "blobs")
        }
        network = {"canvas": 0, "openai": 0, "credential": 0}

        def deny(kind: str) -> Callable[..., Any]:
            def blocked(*_args: Any, **_kwargs: Any) -> Any:
                network[kind] += 1
                raise AssertionError(f"{kind} boundary called")
            return blocked

        target.with_canvas_connector = deny("canvas")
        target.openai_responses_request = deny("openai")
        target.initialize_credential_client = deny("credential")
        result = target.apply_staged_restore(
            request["restore_id"], request["expected_plan_revision"], request["decisions"], request["relinks"]
        )
        row = conn.execute("SELECT * FROM files WHERE stable_id='material_syn_local'").fetchone()
        source_row = conn.execute(
            """
            SELECT s.* FROM sources s JOIN material_versions mv ON mv.source_id=s.id
            JOIN materials m ON m.id=mv.material_id
            WHERE m.stable_id='material_syn_local' AND mv.id=m.current_version_id
            """
        ).fetchone()
        after_counts = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in before_counts
        }
        check("exact_candidate_relink_apply", result["applied"]["relinked"] == 1, failures)
        check("canonical_material_identity_preserved", row["stable_id"] == "material_syn_local", failures)
        check("canonical_source_projection_updated", source_row["authority"] == "USER_OWNED" and source_row["import_mode"] == "reference", failures)
        check("no_duplicate_domain_rows", before_counts == after_counts, failures)
        check("original_bytes_preserved_on_success", hashlib.sha256(selected.read_bytes()).hexdigest() == original_hash, failures)
        check("capability_consumed_after_success", not target.restore_relink_descriptor_path(request["relinks"][0]["relink_id"]).exists(), failures)
        check("no_network_or_credentials", not any(network.values()), failures)
        check("public_result_contains_no_path", str(base) not in json.dumps(result) and "selected_path" not in json.dumps(result), failures)
        search = conn.execute("SELECT extracted_text FROM files_fts WHERE rowid=?", (row["id"],)).fetchone()
        check("search_metadata_consistent", search is not None and "synthetic" in str(search["extracted_text"]).lower(), failures)
        restarted = load_server(base / "target-success")
        restarted_conn = restarted.connect_db()
        restarted.init_db(restarted_conn)
        restarted_row = restarted_conn.execute("SELECT stable_id, original_path FROM files WHERE stable_id='material_syn_local'").fetchone()
        check("relink_survives_restart", restarted_row is not None and restarted_row["stable_id"] == "material_syn_local", failures)
        restarted_conn.close()

        empty_target, empty_conn, _, empty_source_id, empty_file, empty_request = fixture(
            base, "empty-target", seed_target=False
        )
        empty_result = empty_target.apply_staged_restore(
            empty_request["restore_id"],
            empty_request["expected_plan_revision"],
            empty_request["decisions"],
            empty_request["relinks"],
        )
        canonical_counts = {
            table: empty_conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("sources", "materials", "material_versions", "blobs")
        }
        empty_material = empty_conn.execute(
            "SELECT stable_id, import_mode FROM files WHERE stable_id='material_syn_local'"
        ).fetchone()
        check(
            "absent_material_relinked_into_canonical_model",
            empty_result["applied"]["relinked"] == 1
            and empty_material is not None
            and empty_material["import_mode"] == "reference",
            failures,
        )
        check(
            "absent_material_creates_single_canonical_graph",
            canonical_counts == {"sources": 1, "materials": 1, "material_versions": 1, "blobs": 1},
            failures,
        )
        check("absent_material_original_bytes_preserved", empty_file.read_bytes() == LOCAL_BYTES, failures)
        check("relink_decision_bound_to_source", empty_source_id == empty_request["relinks"][0]["source_stable_id"], failures)

        stale_target, stale_conn, _, _, stale_file, stale_request = fixture(base, "stale-revision")
        stale_request["relinks"][0]["relink_revision"] = "relink_" + "0" * 24
        stale_before = logical_dump(stale_conn)
        check(
            "stale_relink_revision_rejected",
            expect_code(
                lambda: stale_target.apply_staged_restore(
                    stale_request["restore_id"], stale_request["expected_plan_revision"], stale_request["decisions"], stale_request["relinks"]
                ),
                "stale_restore_relink_revision",
            ),
            failures,
        )
        check("stale_revision_zero_mutation", logical_dump(stale_conn) == stale_before, failures)
        check("failed_relink_preserves_original", stale_file.read_bytes() == LOCAL_BYTES, failures)

        changed_target, changed_conn, _, _, changed_file, changed_request = fixture(base, "changed-source")
        changed_file.write_bytes(b"X" * len(LOCAL_BYTES))
        changed_before = logical_dump(changed_conn)
        check(
            "candidate_changed_after_preview_rejected",
            expect_code(
                lambda: changed_target.apply_staged_restore(
                    changed_request["restore_id"], changed_request["expected_plan_revision"], changed_request["decisions"], changed_request["relinks"]
                ),
                "restore_relink_candidate_mismatch", "restore_relink_source_changed",
            ),
            failures,
        )
        check("changed_candidate_zero_db_mutation", logical_dump(changed_conn) == changed_before, failures)

        local_target, local_conn, _, _, _, local_request = fixture(base, "changed-local-state")
        local_conn.execute("UPDATE terms SET name='Changed after preview' WHERE stable_id='term_syn_alpha'")
        local_conn.commit()
        check(
            "local_db_changed_after_preview_rejected",
            expect_code(
                lambda: local_target.apply_staged_restore(
                    local_request["restore_id"], local_request["expected_plan_revision"], local_request["decisions"], local_request["relinks"]
                ),
                "stale_restore_plan",
            ),
            failures,
        )

        archive_target, archive_conn, archive_restore_id, _, _, archive_request = fixture(base, "changed-archive")
        staged_archive = archive_target.staged_restore_path(archive_restore_id)
        changed_archive = mutate_dataset(
            archive_target,
            staged_archive,
            "data/terms.json",
            lambda payload: payload["records"][0].update(name="Archive changed after preview"),
        )
        shutil.copyfile(changed_archive, staged_archive)
        archive_before = logical_dump(archive_conn)
        check(
            "archive_changed_after_preview_rejected",
            expect_code(
                lambda: archive_target.apply_staged_restore(
                    archive_request["restore_id"], archive_request["expected_plan_revision"], archive_request["decisions"], archive_request["relinks"]
                ),
                "stale_restore_plan",
            ),
            failures,
        )
        check("changed_archive_zero_mutation", logical_dump(archive_conn) == archive_before, failures)

        rollback_target, rollback_conn, _, _, rollback_file, rollback_request = fixture(base, "rollback")
        cache = rollback_target.expected_text_cache_path(hashlib.sha256(LOCAL_BYTES).hexdigest())
        cache.unlink(missing_ok=True)
        rollback_before = logical_dump(rollback_conn)
        descriptor = rollback_target.restore_relink_descriptor_path(rollback_request["relinks"][0]["relink_id"])
        check(
            "transaction_rollback_on_injected_failure",
            expect_code(
                lambda: rollback_target.apply_staged_restore(
                    rollback_request["restore_id"], rollback_request["expected_plan_revision"], rollback_request["decisions"], rollback_request["relinks"], failure_point="after_relink"
                ),
                "synthetic_restore_failure_after_relink",
            ),
            failures,
        )
        check("rollback_has_no_partial_db_mutation", logical_dump(rollback_conn) == rollback_before, failures)
        check("rollback_has_no_partial_index_cache", not cache.exists(), failures)
        check("failed_apply_preserves_capability", descriptor.exists(), failures)
        check("rollback_preserves_original_bytes", rollback_file.read_bytes() == LOCAL_BYTES, failures)

        override_target, _, _, _, _, override_request = fixture(base, "http-override")
        status, response = http_post(
            override_target,
            "/api/backup/restore-apply",
            override_request | {"path": str(base / "forbidden")},
        )
        check("browser_path_override_rejected", status == 400 and "error" in response, failures)

        demo = load_server(base / "demo-profile", profile="demo-test")
        check(
            "demo_test_relink_apply_denied_in_backend",
            expect_code(
                lambda: demo.prepare_restore_relinks("a" * 32, [{}], {}),
                "restore_relink_unavailable",
            ),
            failures,
        )

        check("no_schema_change", "restore_relink" not in json.dumps([row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]), failures)

    if failures:
        print("Backup restore relink apply acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore relink apply acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
