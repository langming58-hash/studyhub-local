#!/usr/bin/env python3
"""Persistent PDF Highlights V1 acceptance for Phase 4."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

from backup_restore_preview_acceptance import (  # noqa: E402
    mutate_dataset as mutate_backup_dataset,
    stage as stage_restore,
)
from source_linked_notes_acceptance import (  # noqa: E402
    load_server,
    prepare_target,
    synthetic_pdf,
)


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def dispatch(server, conn, body: dict) -> dict:
    return server.handle_highlights_post(conn, body)


def decisions_with_highlight_restore(plan: dict) -> list[dict]:
    result = []
    for section in plan.get("sections", []):
        for action in section.get("actions", []):
            classification = action.get("classification")
            if classification == "CONFLICT":
                decision = "RESTORE_BACKUP" if action.get("label") == "PDF highlight" else "KEEP_LOCAL"
            elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                decision = "SKIP"
            else:
                continue
            result.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return result


def highlights_snapshot(conn) -> list[dict]:
    columns = (
        "id", "stable_id", "material_id", "source_version_stable_id",
        "anchor_page", "rects_json", "label", "created_at", "updated_at",
    )
    rows = conn.execute(f"SELECT {', '.join(columns)} FROM pdf_highlights ORDER BY id").fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-highlights-") as raw:
        tmp = Path(raw)
        library = tmp / "StudyLibrary"
        database = tmp / "runtime" / "studyhub.sqlite"
        server = load_server(tmp, library, database)

        pdf = library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        pdf.parent.mkdir(parents=True)
        pdf_bytes = synthetic_pdf(["Page one text", "Page two text", "Page three text"])
        pdf.write_bytes(pdf_bytes)
        before = hashlib.sha256(pdf.read_bytes()).hexdigest()
        server.scan_library(library)

        conn = server.connect_db()
        server.init_db(conn)
        server.run_schema_migrations(conn)
        file_row = conn.execute("SELECT * FROM files WHERE filename='notes.pdf'").fetchone()
        file_id = int(file_row["id"])
        _material_stable, version_stable = server.note_anchor_for_file(conn, file_id)

        rects = [[10.5, 20.25, 120.0, 12.0], [10.5, 36.0, 120.0, 12.0]]
        created = dispatch(server, conn, {
            "action": "create", "fileId": file_id, "anchorPage": 3,
            "rects": rects, "label": "Selected passage", "sourceVersion": version_stable,
        })
        check("highlight_create_persists", created.get("ok") is True and created.get("id") and created.get("anchor_state") == "ok", failures)
        check("highlight_has_no_secret_fields", not any(key in created for key in ("sha256", "hash", "path", "locator")), failures)
        check("highlight_list_by_file", len(server.pdf_highlight_rows(conn, file_id)) == 1, failures)

        # Deletion.
        dispatch(server, conn, {"action": "delete", "id": created["id"]})
        check("highlight_delete_persists", len(server.pdf_highlight_rows(conn, file_id)) == 0, failures)

        # Recreate for round-trip testing.
        created = dispatch(server, conn, {
            "action": "create", "fileId": file_id, "anchorPage": 2,
            "rects": rects, "label": "Round trip", "sourceVersion": version_stable,
        })

        # Geometry and input validation.
        bad_rects = (
            [], [[0, 0, 0, 0]], [[0, 0, 10]], [[True, 0, 10, 5]], [[0, 0, -1, 5]],
            [[200000, 0, 10, 5]], ["not-a-rect"], [[0, 0, 10, 5, 1]],
        )
        for bad in bad_rects:
            try:
                dispatch(server, conn, {"action": "create", "fileId": file_id, "anchorPage": 1, "rects": bad, "label": "", "sourceVersion": version_stable})
                check(f"invalid_rects_{bad!r}_rejected", False, failures)
            except ValueError:
                check(f"invalid_rects_{bad!r}_rejected", True, failures)
        for bad_version in ("", None, False, 123, "matver_wrong"):
            try:
                dispatch(server, conn, {"action": "create", "fileId": file_id, "anchorPage": 1, "rects": [[0, 0, 10, 5]], "label": "", "sourceVersion": bad_version})
                check(f"invalid_source_version_{bad_version!r}_rejected", False, failures)
            except ValueError:
                check(f"invalid_source_version_{bad_version!r}_rejected", True, failures)

        # Physical replacement without rescan rejects highlight creation.
        pdf.write_bytes(synthetic_pdf(["Replaced one", "Replaced two", "Replaced three"]))
        rejected = False
        try:
            dispatch(server, conn, {"action": "create", "fileId": file_id, "anchorPage": 1, "rects": [[0, 0, 10, 5]], "label": "", "sourceVersion": version_stable})
            rejected = True
        except ValueError:
            pass
        check("physical_replacement_without_rescan_rejected", not rejected, failures)
        pdf.write_bytes(pdf_bytes)

        check("original_pdf_unchanged", hashlib.sha256(pdf.read_bytes()).hexdigest() == before, failures)

        # Backup round-trip.
        archive = server.export_current_profile_backup()
        archive_path = server.EXPORT_DIR / archive["filename"]
        with zipfile.ZipFile(archive_path) as zf:
            study = json.loads(zf.read("data/study_records.json").decode("utf-8"))
        highlight_records = [r for r in study["records"] if r["record_type"] == "pdf_highlight"]
        check("backup_exports_highlights", len(highlight_records) == 1 and highlight_records[0]["rects"] == rects, failures)
        check("archive_has_no_absolute_paths", str(tmp) not in json.dumps(study) and "/Users/" not in json.dumps(study), failures)

        target, tconn, target_tmp = prepare_target("highlights", pdf.read_bytes())
        target_file = tconn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        stage_restore(target, archive_path, "a" * 32)
        plan = target.preview_staged_restore("a" * 32)
        result = target.apply_staged_restore("a" * 32, plan["plan_revision"], decisions_with_highlight_restore(plan))
        check("restore_applies_highlights", result.get("ok") is True, failures)
        restored = target.pdf_highlight_rows(tconn, int(target_file["id"]))
        check("restore_preserves_highlights", len(restored) == 1 and restored[0]["rects"] == rects and restored[0]["anchor_state"] == "ok", failures)

        # Restore conflict + exact Undo.
        first_record = highlight_records[0]
        tconn.execute("UPDATE pdf_highlights SET label='Prior local label' WHERE stable_id=?", (first_record["stable_id"],))
        tconn.commit()
        prior = highlights_snapshot(tconn)
        plan2 = target.preview_staged_restore("a" * 32)
        apply2 = target.apply_staged_restore("a" * 32, plan2["plan_revision"], decisions_with_highlight_restore(plan2))
        operation_id = apply2["applied"]["restore_operation_id"]
        check(
            "restore_backup_applies_highlight_conflict",
            tconn.execute("SELECT label FROM pdf_highlights WHERE stable_id=?", (first_record["stable_id"],)).fetchone()["label"] == first_record["label"],
            failures,
        )
        undo_preview = target.preview_restore_undo(operation_id)
        undo_result = target.apply_restore_undo(operation_id, undo_preview["undo_revision"])
        check("undo_restores_exact_highlight_state", undo_result.get("ok") is True and highlights_snapshot(tconn) == prior, failures)
        tconn.close()
        shutil.rmtree(target_tmp, ignore_errors=True)

        # Legacy archives without highlight records remain valid.
        def strip_highlights(payload):
            payload["records"] = [r for r in payload["records"] if r.get("record_type") != "pdf_highlight"]

        legacy_path = mutate_backup_dataset(server, archive_path, "data/study_records.json", strip_highlights)
        try:
            server.validate_backup_archive(legacy_path)
            check("legacy_archive_without_highlights_valid", True, failures)
        except ValueError:
            check("legacy_archive_without_highlights_valid", False, failures)

        # Profile isolation.
        other_profile = Path(tempfile.mkdtemp(prefix="studyhub-hl-other-"))
        other_library = other_profile / "StudyLibrary"
        other_db = other_profile / "runtime" / "studyhub.sqlite"
        other = load_server(other_profile, other_library, other_db)
        other_pdf = other_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        other_pdf.parent.mkdir(parents=True)
        other_pdf.write_bytes(pdf.read_bytes())
        other.scan_library(other_library)
        other_conn = other.connect_db()
        other.init_db(other_conn)
        other.run_schema_migrations(other_conn)
        other_file = other_conn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        check("profile_isolation_no_shared_highlights", len(other.pdf_highlight_rows(other_conn, int(other_file["id"]))) == 0, failures)
        other_conn.close()
        shutil.rmtree(other_profile, ignore_errors=True)

        conn.close()

    if failures:
        print("PDF highlights acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("PDF highlights acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
