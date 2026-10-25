#!/usr/bin/env python3
"""Synthetic acceptance for composable multi-source restore relink."""

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

from backup_export_acceptance import LOCAL_BYTES, insert_file, load_server, seed_state  # noqa: E402
from backup_restore_relink_preview_acceptance import write_capability  # noqa: E402
from backup_restore_preview_acceptance import export_fixture, mutate_dataset, stage  # noqa: E402


SECOND_BYTES = b"SYNTHETIC-SECOND-USER-OWNED-ORIGINAL"
SECOND_TEXT = "Synthetic second searchable relink content."
MATERIALS = ("material_syn_local", "material_syn_second")


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


def add_second_material(server: Any, conn: sqlite3.Connection, root: Path, id_base: int) -> Path:
    path = root / "library" / "SYN9200 - Synthetic Course" / "Module Alpha" / "Lecture" / "second-guide.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SECOND_BYTES)
    server.TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (server.TEXT_CACHE_DIR / "material_syn_second.txt").write_text(SECOND_TEXT, encoding="utf-8")
    course_id = int(conn.execute("SELECT id FROM courses WHERE stable_id='course_syn_alpha'").fetchone()["id"])
    week_id = int(conn.execute("SELECT id FROM weeks WHERE stable_id='unit_syn_alpha'").fetchone()["id"])
    insert_file(
        server,
        conn,
        file_id=id_base + 40,
        course_id=course_id,
        week_id=week_id,
        path=path,
        stable_id="material_syn_second",
        import_mode="reference",
        display_name="My second guide",
        metadata_locked=1,
        source_type="Local Reference",
    )
    conn.execute(
        "UPDATE document_chunks SET text=? WHERE file_id=?",
        (SECOND_TEXT, id_base + 40),
    )
    server.reconcile_domain_projection(conn)
    conn.commit()
    return path


def relink_all_archive(server: Any, archive: Path) -> Path:
    def mutate(payload: dict[str, Any]) -> None:
        for record in payload["records"]:
            record["location"] = {
                "kind": "relink_required",
                "basename": record["display_name"],
            }
            record["missing"] = True

    return mutate_dataset(server, archive, "data/local_sources.json", mutate)


def decisions_for(plan: dict[str, Any], source_ids: set[str]) -> list[dict[str, str]]:
    decisions: list[dict[str, str]] = []
    for section in plan["sections"]:
        for action in section["actions"]:
            classification = action["classification"]
            if classification == "CONFLICT":
                decision = "KEEP_LOCAL"
            elif (
                classification == "RELINK_REQUIRED"
                and action["dataset"] == "local_sources"
                and action["stable_id"] in source_ids
            ):
                decision = "RELINK"
            elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                decision = "SKIP"
            else:
                continue
            decisions.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return decisions


def multi_fixture(
    base: Path,
    suffix: str,
    order: tuple[str, str] = MATERIALS,
) -> tuple[Any, sqlite3.Connection, dict[str, Any]]:
    source_root = base / f"source-{suffix}"
    target_root = base / f"target-{suffix}"
    source = load_server(source_root)
    source_conn = seed_state(source, source_root, 100)
    add_second_material(source, source_conn, source_root, 100)
    archive = relink_all_archive(source, export_fixture(source, source_conn, "2026-05-01T00:00:00+00:00"))

    target = load_server(target_root)
    target_conn = seed_state(target, target_root, 500, reverse_insert_order=True)
    add_second_material(target, target_conn, target_root, 500)
    restore_id = hashlib.sha256(suffix.encode()).hexdigest()[:32]
    stage(target, archive, restore_id)
    base_plan = target.preview_staged_restore(restore_id)
    _validation, _manifest, datasets = target.read_validated_backup_archive(target.staged_restore_path(restore_id))
    sources_by_material = {
        str(record["material_ref"]): record for record in datasets["data/local_sources.json"]
    }
    selected_root = base / f"selected-{suffix}"
    selected_root.mkdir(parents=True)
    selected_paths = {
        "material_syn_local": selected_root / "first-original.txt",
        "material_syn_second": selected_root / "second-original.txt",
    }
    selected_paths["material_syn_local"].write_bytes(LOCAL_BYTES)
    selected_paths["material_syn_second"].write_bytes(SECOND_BYTES)

    active: list[dict[str, str]] = []
    previews: dict[str, dict[str, Any]] = {}
    for index, material_ref in enumerate(order, start=1):
        source_id = str(sources_by_material[material_ref]["stable_id"])
        relink_id = hashlib.sha256(f"{suffix}-{material_ref}-{index}".encode()).hexdigest()[:32]
        write_capability(
            target,
            relink_id=relink_id,
            restore_id=restore_id,
            source_stable_id=source_id,
            selected=selected_paths[material_ref],
        )
        preview = target.preview_restore_relink_candidate(
            restore_id,
            base_plan["plan_revision"],
            source_id,
            relink_id,
            active,
        )
        previews[material_ref] = preview
        active.append({
            "source_stable_id": source_id,
            "relink_id": relink_id,
            "relink_revision": preview["relink_revision"],
        })

    final_plan = previews[order[-1]]["plan"]
    request = {
        "restore_id": restore_id,
        "expected_plan_revision": base_plan["plan_revision"],
        "decisions": decisions_for(final_plan, {item["source_stable_id"] for item in active}),
        "relinks": active,
    }
    return target, target_conn, {
        "archive": archive,
        "base_plan": base_plan,
        "previews": previews,
        "selected_paths": selected_paths,
        "sources_by_material": sources_by_material,
        "request": request,
    }


def apply_request(target: Any, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return target.apply_staged_restore(
        request["restore_id"],
        request["expected_plan_revision"],
        request["decisions"],
        request["relinks"],
        **kwargs,
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-multi-relink-") as temporary:
        base = Path(temporary)

        target, conn, fixture = multi_fixture(base, "success")
        previews = fixture["previews"]
        request = fixture["request"]
        base_revision = fixture["base_plan"]["plan_revision"]
        final_actions = [action for section in previews[MATERIALS[-1]]["plan"]["sections"] for action in section["actions"]]
        check("preview_a_exact_ready", previews[MATERIALS[0]]["status"] == "RELINK_READY", failures)
        check("preview_b_exact_ready", previews[MATERIALS[1]]["status"] == "RELINK_READY", failures)
        check("base_revision_stable_across_previews", all(item["plan"]["plan_revision"] == base_revision for item in previews.values()), failures)
        check("candidate_a_revision_survives_b", request["relinks"][0]["relink_revision"] == previews[MATERIALS[0]]["relink_revision"], failures)
        check(
            "final_plan_exposes_two_relink_decisions",
            sum(1 for item in request["decisions"] if item["decision"] == "RELINK") == 2
            and sum(1 for item in final_actions if item["dataset"] == "local_sources" and item["classification"] == "RELINK_REQUIRED") == 2,
            failures,
        )

        reverse_target, reverse_conn, reverse_fixture = multi_fixture(base, "reverse", tuple(reversed(MATERIALS)))
        check("preview_order_b_then_a", all(item["status"] == "RELINK_READY" for item in reverse_fixture["previews"].values()), failures)
        reverse_conn.close()

        before_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
            for table in ("sources", "materials", "material_versions", "blobs")
        }
        original_hashes = {
            material_ref: hashlib.sha256(path.read_bytes()).hexdigest()
            for material_ref, path in fixture["selected_paths"].items()
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
        result = apply_request(target, request)
        check("two_relinks_apply_in_one_transaction", result["applied"]["relinked"] == 2, failures)
        check("no_network_canvas_openai_or_credentials", not any(network.values()), failures)
        after_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
            for table in before_counts
        }
        check("canonical_graph_has_no_duplicates", before_counts == after_counts, failures)
        rows = conn.execute(
            "SELECT stable_id, original_path FROM files WHERE stable_id IN (?, ?) ORDER BY stable_id",
            MATERIALS,
        ).fetchall()
        check("stable_material_identities_preserved", [row["stable_id"] for row in rows] == sorted(MATERIALS), failures)
        check(
            "both_originals_referenced",
            {row["stable_id"]: row["original_path"] for row in rows}
            == {material_ref: str(path.resolve()) for material_ref, path in fixture["selected_paths"].items()},
            failures,
        )
        check(
            "both_original_byte_streams_unchanged",
            all(hashlib.sha256(path.read_bytes()).hexdigest() == original_hashes[material_ref] for material_ref, path in fixture["selected_paths"].items()),
            failures,
        )
        fts = {
            row["stable_id"]: str(row["extracted_text"])
            for row in conn.execute(
                "SELECT f.stable_id, x.extracted_text FROM files f JOIN files_fts x ON x.rowid=f.id WHERE f.stable_id IN (?, ?)",
                MATERIALS,
            ).fetchall()
        }
        check("search_metadata_consistent_for_both", set(fts) == set(MATERIALS) and all(value.strip() for value in fts.values()), failures)
        check("successful_apply_consumes_both_capabilities", all(not target.restore_relink_descriptor_path(item["relink_id"]).exists() for item in request["relinks"]), failures)
        check("public_result_contains_no_absolute_path", str(base) not in json.dumps(result) and "selected_path" not in json.dumps(result), failures)
        restarted = load_server(base / "target-success")
        restarted_conn = restarted.connect_db()
        restarted.init_db(restarted_conn)
        check(
            "both_relinks_survive_restart",
            int(restarted_conn.execute("SELECT COUNT(*) AS c FROM files WHERE stable_id IN (?, ?) AND source_missing=0", MATERIALS).fetchone()["c"]) == 2,
            failures,
        )
        restarted_conn.close()

        for changed_index, material_ref in enumerate(MATERIALS):
            changed_target, changed_conn, changed_fixture = multi_fixture(base, f"changed-{changed_index}")
            changed_fixture["selected_paths"][material_ref].write_bytes(b"X" * len(changed_fixture["selected_paths"][material_ref].read_bytes()))
            before = logical_dump(changed_conn)
            check(
                f"changed_candidate_{changed_index + 1}_rejects_whole_apply",
                expect_code(lambda t=changed_target, r=changed_fixture["request"]: apply_request(t, r), "restore_relink_candidate_mismatch", "restore_relink_source_changed")
                and logical_dump(changed_conn) == before,
                failures,
            )

        for discarded_index in range(2):
            discarded_target, discarded_conn, discarded_fixture = multi_fixture(base, f"discarded-{discarded_index}")
            item = discarded_fixture["request"]["relinks"][discarded_index]
            discarded_target.discard_restore_relink_capability(
                discarded_fixture["request"]["restore_id"], item["source_stable_id"], item["relink_id"]
            )
            before = logical_dump(discarded_conn)
            check(
                f"discarded_capability_{discarded_index + 1}_rejects_whole_apply",
                expect_code(lambda t=discarded_target, r=discarded_fixture["request"]: apply_request(t, r), "restore_relink_capability_invalid")
                and logical_dump(discarded_conn) == before,
                failures,
            )

        stale_target, stale_conn, stale_fixture = multi_fixture(base, "stale-base")
        stale_fixture["request"]["expected_plan_revision"] = "restore_" + "0" * 24
        check("stale_base_restore_revision_rejected", expect_code(lambda: apply_request(stale_target, stale_fixture["request"]), "stale_restore_plan"), failures)

        for stale_index in range(2):
            revision_target, revision_conn, revision_fixture = multi_fixture(base, f"stale-revision-{stale_index}")
            revision_fixture["request"]["relinks"][stale_index]["relink_revision"] = "relink_" + "0" * 24
            check(
                f"stale_per_source_revision_{stale_index + 1}_rejected",
                expect_code(lambda t=revision_target, r=revision_fixture["request"]: apply_request(t, r), "stale_restore_relink_revision"),
                failures,
            )
            revision_conn.close()

        local_target, local_conn, local_fixture = multi_fixture(base, "changed-local")
        local_conn.execute("UPDATE terms SET name='Changed after preview' WHERE stable_id='term_syn_alpha'")
        local_conn.commit()
        check("changed_relevant_local_state_rejected", expect_code(lambda: apply_request(local_target, local_fixture["request"]), "stale_restore_plan"), failures)

        archive_target, archive_conn, archive_fixture = multi_fixture(base, "changed-archive")
        staged_archive = archive_target.staged_restore_path(archive_fixture["request"]["restore_id"])
        changed_archive = mutate_dataset(
            archive_target,
            staged_archive,
            "data/terms.json",
            lambda payload: payload["records"][0].update(name="Changed archive"),
        )
        shutil.copyfile(changed_archive, staged_archive)
        check("changed_archive_rejected", expect_code(lambda: apply_request(archive_target, archive_fixture["request"]), "stale_restore_plan"), failures)

        for point in ("after_first_relink", "after_all_relinks"):
            rollback_target, rollback_conn, rollback_fixture = multi_fixture(base, f"rollback-{point}")
            before = logical_dump(rollback_conn)
            hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in rollback_fixture["selected_paths"].items()}
            cache_paths = [rollback_target.expected_text_cache_path(hashlib.sha256(path.read_bytes()).hexdigest()) for path in rollback_fixture["selected_paths"].values()]
            for cache_path in cache_paths:
                cache_path.unlink(missing_ok=True)
            check(
                f"atomic_rollback_{point}",
                expect_code(lambda t=rollback_target, r=rollback_fixture["request"], p=point: apply_request(t, r, failure_point=p), f"synthetic_restore_failure_{point}"),
                failures,
            )
            check(f"zero_partial_db_or_search_{point}", logical_dump(rollback_conn) == before, failures)
            check(f"no_generated_cache_leak_{point}", all(not path.exists() for path in cache_paths), failures)
            check(
                f"both_originals_untouched_{point}",
                all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[key] for key, path in rollback_fixture["selected_paths"].items()),
                failures,
            )
            check(
                f"capabilities_preserved_{point}",
                all(rollback_target.restore_relink_descriptor_path(item["relink_id"]).exists() for item in rollback_fixture["request"]["relinks"]),
                failures,
            )

        wrong_target, wrong_conn, wrong_fixture = multi_fixture(base, "wrong-binding")
        swapped = [dict(item) for item in wrong_fixture["request"]["relinks"]]
        swapped[0]["source_stable_id"], swapped[1]["source_stable_id"] = swapped[1]["source_stable_id"], swapped[0]["source_stable_id"]
        wrong_fixture["request"]["relinks"] = swapped
        check("capability_bound_to_source", expect_code(lambda: apply_request(wrong_target, wrong_fixture["request"]), "restore_relink_capability_mismatch"), failures)

        other_target, other_conn, other_fixture = multi_fixture(base, "wrong-restore")
        alternate_restore_id = "f" * 32
        stage(other_target, other_fixture["archive"], alternate_restore_id)
        other_fixture["request"]["restore_id"] = alternate_restore_id
        check("capability_bound_to_restore", expect_code(lambda: apply_request(other_target, other_fixture["request"]), "restore_relink_capability_mismatch"), failures)

        demo = load_server(base / "demo-profile", profile="demo-test")
        check(
            "demo_test_multi_relink_denied",
            expect_code(lambda: demo.prepare_restore_relinks("a" * 32, request["relinks"], {}), "restore_relink_unavailable"),
            failures,
        )
        check("no_schema_change", not any("restore_relink" in row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")), failures)

        for active_conn in (conn, stale_conn, local_conn, archive_conn, wrong_conn, other_conn):
            active_conn.close()

    if failures:
        print("Backup restore multi-relink acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Backup restore multi-relink acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
