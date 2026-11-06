#!/usr/bin/env python3
"""Source-linked page notes acceptance for Phase 4."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
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


def load_server(tmp: Path, library: Path, database: Path):
    for key in (
        "STUDYHUB_RUNTIME_PROFILE", "STUDY_LIBRARY_PATH", "STUDYHUB_RUNTIME_DIR",
        "STUDYHUB_DATA_DIR", "STUDYHUB_CACHE_DIR", "STUDYHUB_LOG_DIR",
        "STUDYHUB_CONFIG_PATH", "DATABASE_PATH", "OPENAI_API_KEY",
        "OPENAI_VECTOR_STORE_ID", "STUDYHUB_MARKITDOWN_HELPER",
    ):
        os.environ.pop(key, None)
    runtime = database.parent
    os.environ.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": "production",
            "STUDY_LIBRARY_PATH": str(library),
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "DATABASE_PATH": str(database),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    module_name = f"studyhub_notes_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = runtime / "data"
    server.CACHE_DIR = runtime / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = runtime / "logs"
    server.DB_PATH = database
    server.ENV_LOCAL_PATH = runtime / "settings.env"
    server.ENV_LOCAL_EXISTS = False
    server.DEFAULT_STUDY_ROOT = library
    server.ensure_dirs()
    return server


def synthetic_pdf(page_texts: list[str]) -> bytes:
    page_ids = [3 + index * 2 for index in range(len(page_texts))]
    font_id = 3 + len(page_texts) * 2
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(),
    ]
    for page_id, text in zip(page_ids, page_texts):
        content_id = page_id + 1
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
        objects.extend(
            [
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
                ).encode(),
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            ]
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(body)


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def note_create(server, payload):
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: payload
    sent = {}
    handler.send_json = lambda data, status=200: sent.update({"status": status, "data": data})
    server.StudyHubHandler.handle_note(handler)
    return sent


def notes_snapshot(conn: sqlite3.Connection) -> list[dict]:
    columns = (
        "id", "target_type", "target_id", "course_id", "week_label", "body",
        "created_at", "updated_at", "anchor_version", "anchor_page",
        "anchor_material_stable_id", "anchor_version_stable_id",
    )
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM notes ORDER BY id"
    ).fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def decisions_for_notes_restore(plan: dict) -> list[dict]:
    result = []
    for section in plan.get("sections", []):
        for action in section.get("actions", []):
            classification = action.get("classification")
            if classification == "CONFLICT":
                decision = "RESTORE_BACKUP" if action.get("dataset") == "notes" else "KEEP_LOCAL"
            elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                decision = "SKIP"
            else:
                continue
            result.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return result


def rejected_archive(server, archive_path: Path) -> bool:
    try:
        server.validate_backup_archive(archive_path)
    except ValueError:
        return True
    return False


def prepare_target(suffix: str, pdf_bytes: bytes):
    target_tmp = Path(tempfile.mkdtemp(prefix=f"studyhub-notes-{suffix}-"))
    target_library = target_tmp / "StudyLibrary"
    target_db = target_tmp / "runtime" / "studyhub.sqlite"
    target_pdf = target_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
    target_pdf.parent.mkdir(parents=True)
    target_pdf.write_bytes(pdf_bytes)
    target = load_server(target_tmp, target_library, target_db)
    target.scan_library(target_library)
    conn = target.connect_db()
    target.init_db(conn)
    target.run_schema_migrations(conn)
    conn.commit()
    target.RESTORE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    return target, conn, target_tmp


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-notes-") as raw:
        tmp = Path(raw)
        library = tmp / "StudyLibrary"
        database = tmp / "runtime" / "studyhub.sqlite"
        server = load_server(tmp, library, database)

        pdf = library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(synthetic_pdf(["Page one text", "Page two text", "Page three text"]))
        before = hashlib.sha256(pdf.read_bytes()).hexdigest()
        server.scan_library(library)

        conn = server.connect_db()
        server.init_db(conn)
        file_row = conn.execute("SELECT * FROM files WHERE filename='notes.pdf'").fetchone()
        file_id = int(file_row["id"])
        material_stable, version_stable = server.note_anchor_for_file(conn, file_id)
        check("anchor_resolves_material_and_version", bool(material_stable) and bool(version_stable), failures)

        base = {"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"]}
        note_create(server, {**base, "body": "Page 1 summary", "anchorPage": 1, "pageCount": 3})
        note_create(server, {**base, "body": "Page 3 detail", "anchorPage": 3, "pageCount": 3})
        note_create(server, {**base, "body": "Unanchored note"})

        notes = server.StudyHubHandler.handle_notes_get(None, conn, {"targetType": ["file"], "targetId": [str(file_id)]})
        by_page = {note["anchor_page"]: note for note in notes if note["anchor_page"]}
        check("anchored_notes_have_pages", set(by_page) == {1, 3}, failures)
        check("anchored_notes_anchor_state_ok", all(note["anchor_state"] == "ok" for note in by_page.values()), failures)
        check("unanchored_note_has_no_page", any(note.get("anchor_page") is None for note in notes), failures)
        check("anchored_note_has_stable_material", all(note["anchor_material_stable_id"] == material_stable for note in by_page.values()), failures)

        for bad in (0, -1, 2.5, True, "2"):
            try:
                note_create(server, {**base, "body": "bad", "anchorPage": bad, "pageCount": 3})
                check(f"invalid_anchor_{bad}_rejected", False, failures)
            except ValueError:
                check(f"invalid_anchor_{bad}_rejected", True, failures)
        try:
            note_create(server, {**base, "body": "bad", "anchorPage": 4, "pageCount": 3})
            check("beyond_page_count_rejected", False, failures)
        except ValueError:
            check("beyond_page_count_rejected", True, failures)

        check("original_pdf_unchanged", hashlib.sha256(pdf.read_bytes()).hexdigest() == before, failures)
        conn.close()

        backup = server.export_current_profile_backup()
        archive_path = server.EXPORT_DIR / backup["filename"]
        with zipfile.ZipFile(archive_path) as archive:
            dataset = json.loads(archive.read("data/notes.json").decode("utf-8"))
        records = dataset.get("records", []) if isinstance(dataset, dict) else dataset
        anchored = [record for record in records if record.get("anchor_page")]
        check("backup_includes_anchor_fields", len(anchored) == 2 and all(record.get("anchor_material_stable_id") for record in anchored), failures)
        check("backup_keeps_target_ref_not_path", all("target_ref" in record and "target_id" not in record for record in records), failures)

        # Negative archive validation: strict anchor types and combinations.
        negative_anchor_cases = [
            ("boolean_material_identity", {"anchor_material_stable_id": True}),
            ("integer_material_identity", {"anchor_material_stable_id": 7}),
            ("array_material_identity", {"anchor_material_stable_id": ["material"]}),
            ("object_material_identity", {"anchor_material_stable_id": {"id": "material"}}),
            ("overlong_material_identity", {"anchor_material_stable_id": "x" * 201}),
            ("malformed_material_identity", {"anchor_material_stable_id": "!bad"}),
            ("boolean_version_identity", {"anchor_version_stable_id": True}),
            ("missing_version_identity", {"anchor_version_stable_id": ""}),
            ("mismatched_material_identity", {"anchor_material_stable_id": "mat_mismatch"}),
            ("invalid_anchor_version", {"anchor_version": 2}),
            ("boolean_anchor_version", {"anchor_version": True}),
            ("zero_anchor_page", {"anchor_page": 0}),
            ("negative_anchor_page", {"anchor_page": -2}),
            ("boolean_anchor_page", {"anchor_page": True}),
            ("string_anchor_page", {"anchor_page": "2"}),
            ("float_anchor_page", {"anchor_page": 2.5}),
            ("page_without_material", {"anchor_material_stable_id": ""}),
            ("material_without_page", {"anchor_page": None}),
        ]
        for case_name, changes in negative_anchor_cases:
            def mutate_anchor(payload, changes=changes):
                record = next(record for record in payload["records"] if record.get("anchor_page"))
                record.update(changes)
            mutated = mutate_backup_dataset(server, archive_path, "data/notes.json", mutate_anchor)
            check(f"backup_{case_name}_rejected", rejected_archive(server, mutated), failures)

        def strip_anchor_fields(payload):
            for record in payload["records"]:
                for key in ("anchor_version", "anchor_page", "anchor_material_stable_id", "anchor_version_stable_id"):
                    record.pop(key, None)
        legacy_path = mutate_backup_dataset(server, archive_path, "data/notes.json", strip_anchor_fields)
        check("legacy_archive_without_anchor_fields_valid", not rejected_archive(server, legacy_path), failures)

        # Full restore round-trip with exact anchor metadata and Undo evidence.
        target, tconn, restore_tmp = prepare_target("restore", pdf.read_bytes())
        target_file = tconn.execute(
            "SELECT id, course_id, week_label FROM files WHERE filename='notes.pdf'"
        ).fetchone()
        target_file_id = int(target_file["id"])
        target_material, target_version = target.note_anchor_for_file(tconn, target_file_id)

        page_one_record = next(record for record in records if record.get("anchor_page") == 1)
        tconn.execute(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at, anchor_version, anchor_page, anchor_material_stable_id, anchor_version_stable_id) VALUES ('file', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                target_file_id,
                target_file["course_id"],
                target_file["week_label"],
                page_one_record["body"],
                page_one_record["created_at"],
                "2020-01-01T00:00:00+00:00",
                2,
                target_material,
                target_version,
            ),
        )
        tconn.execute(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at, anchor_version, anchor_page, anchor_material_stable_id, anchor_version_stable_id) VALUES ('file', ?, ?, ?, 'Target local note', '2019-01-01T00:00:00+00:00', '2019-01-01T00:00:00+00:00', 1, NULL, '', '')",
            (target_file_id, target_file["course_id"], target_file["week_label"]),
        )
        tconn.commit()
        prior_notes = notes_snapshot(tconn)

        restore_id = "b" * 32
        stage_restore(target, archive_path, restore_id)
        plan = target.preview_staged_restore(restore_id)
        result = target.apply_staged_restore(
            restore_id, plan["plan_revision"], decisions_for_notes_restore(plan)
        )
        check(
            "restore_apply_succeeds",
            result.get("ok") is True and bool(result.get("applied", {}).get("restore_operation_id")),
            failures,
        )
        operation_id = result["applied"]["restore_operation_id"]

        restored = target.StudyHubHandler.handle_notes_get(
            None, tconn, {"targetType": ["file"], "targetId": [str(target_file_id)]}
        )
        restored_by_page = {note["anchor_page"]: note for note in restored if note["anchor_page"]}
        check("restore_preserves_exact_anchor_pages", set(restored_by_page) == {1, 3}, failures)
        current_material, current_version = target.note_anchor_for_file(tconn, target_file_id)
        check(
            "restore_preserves_exact_anchor_identities",
            all(
                note["anchor_material_stable_id"] == current_material
                and note["anchor_version_stable_id"] == current_version
                for note in restored_by_page.values()
            ),
            failures,
        )
        check(
            "restore_anchored_notes_report_ok",
            all(note["anchor_state"] == "ok" for note in restored_by_page.values()),
            failures,
        )
        check(
            "restore_preserves_unanchored_notes",
            sum(1 for note in restored if note.get("anchor_page") is None) == 2,
            failures,
        )

        undo_preview = target.preview_restore_undo(operation_id)
        check("restore_undo_preview_available", undo_preview.get("undo_available") is True, failures)
        undo_result = target.apply_restore_undo(operation_id, undo_preview["undo_revision"])
        check(
            "restore_undo_restores_exact_prior_notes",
            undo_result.get("ok") is True and notes_snapshot(tconn) == prior_notes,
            failures,
        )
        after_undo = target.StudyHubHandler.handle_notes_get(
            None, tconn, {"targetType": ["file"], "targetId": [str(target_file_id)]}
        )
        check(
            "restore_undo_preserves_local_anchored_note",
            any(note["anchor_page"] == 2 and note["anchor_state"] == "ok" for note in after_undo),
            failures,
        )
        tconn.close()
        shutil.rmtree(restore_tmp, ignore_errors=True)

        # Legacy archive round-trip without anchor metadata.
        legacy_target, legacy_conn, legacy_tmp = prepare_target("legacy", pdf.read_bytes())
        legacy_file = legacy_conn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        legacy_restore_id = "c" * 32
        stage_restore(legacy_target, legacy_path, legacy_restore_id)
        legacy_plan = legacy_target.preview_staged_restore(legacy_restore_id)
        legacy_result = legacy_target.apply_staged_restore(
            legacy_restore_id, legacy_plan["plan_revision"], decisions_for_notes_restore(legacy_plan)
        )
        check("legacy_archive_restore_succeeds", legacy_result.get("ok") is True, failures)
        legacy_notes = legacy_target.StudyHubHandler.handle_notes_get(
            None, legacy_conn, {"targetType": ["file"], "targetId": [str(legacy_file["id"])]}
        )
        check(
            "legacy_archive_restores_unanchored_notes",
            bool(legacy_notes) and all(note.get("anchor_page") is None for note in legacy_notes),
            failures,
        )
        legacy_conn.close()
        shutil.rmtree(legacy_tmp, ignore_errors=True)

        # Regression: a note that was already stale at export time must not be
        # silently re-anchored to the destination's current version.
        pdf_v2 = synthetic_pdf(["Version two one", "Version two two", "Version two three"])
        stale_profile = Path(tempfile.mkdtemp(prefix="studyhub-notes-stale-"))
        stale_library = stale_profile / "StudyLibrary"
        stale_db = stale_profile / "runtime" / "studyhub.sqlite"
        stale_pdf = stale_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        stale_pdf.parent.mkdir(parents=True)
        stale_pdf.write_bytes(pdf.read_bytes())
        stale_server = load_server(stale_profile, stale_library, stale_db)
        stale_server.scan_library(stale_library)
        stale_conn = stale_server.connect_db()
        stale_server.init_db(stale_conn)
        stale_file = stale_conn.execute("SELECT * FROM files WHERE filename='notes.pdf'").fetchone()
        stale_file_id = int(stale_file["id"])
        stale_base = {"targetType": "file", "targetId": stale_file_id, "courseId": stale_file["course_id"], "week": stale_file["week_label"]}
        note_create(stale_server, {**stale_base, "body": "Stale anchor note", "anchorPage": 1, "pageCount": 3})
        _stale_material, stale_version = stale_server.note_anchor_for_file(stale_conn, stale_file_id)
        stale_pdf.write_bytes(pdf_v2)
        stale_server.scan_library(stale_library)
        stale_conn.commit()
        _stale_material_2, stale_version_2 = stale_server.note_anchor_for_file(stale_conn, stale_file_id)
        check("stale_note_source_advanced_version", stale_version != stale_version_2, failures)
        stale_archive = stale_server.export_current_profile_backup()
        stale_target, stale_tconn, stale_tmp = prepare_target("stale", pdf_v2)
        stale_target_file = stale_tconn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        stage_restore(stale_target, stale_server.EXPORT_DIR / stale_archive["filename"], "d" * 32)
        stale_plan = stale_target.preview_staged_restore("d" * 32)
        stale_result = stale_target.apply_staged_restore(
            "d" * 32, stale_plan["plan_revision"], decisions_for_notes_restore(stale_plan)
        )
        stale_notes = stale_target.StudyHubHandler.handle_notes_get(
            None, stale_tconn, {"targetType": ["file"], "targetId": [str(stale_target_file["id"])]}
        )
        check(
            "stale_note_stays_stale_after_restore",
            stale_result.get("ok") is True
            and any(
                note.get("anchor_page") == 1 and note.get("anchor_state") == "stale"
                for note in stale_notes
            ),
            failures,
        )
        stale_conn.close()
        stale_tconn.close()
        shutil.rmtree(stale_profile, ignore_errors=True)
        shutil.rmtree(stale_tmp, ignore_errors=True)

        # Regression: same material identity but different destination bytes must
        # not fabricate a valid current anchor.
        diff_profile = Path(tempfile.mkdtemp(prefix="studyhub-notes-diff-"))
        diff_library = diff_profile / "StudyLibrary"
        diff_db = diff_profile / "runtime" / "studyhub.sqlite"
        diff_pdf = diff_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        diff_pdf.parent.mkdir(parents=True)
        diff_pdf.write_bytes(pdf.read_bytes())
        diff_server = load_server(diff_profile, diff_library, diff_db)
        diff_server.scan_library(diff_library)
        diff_conn = diff_server.connect_db()
        diff_server.init_db(diff_conn)
        diff_file = diff_conn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        diff_material = diff_conn.execute(
            "SELECT id, current_version_id FROM materials WHERE legacy_file_id=?", (diff_file["id"],)
        ).fetchone()
        diff_conn.execute(
            "UPDATE materials SET stable_id=? WHERE legacy_file_id=?",
            (material_stable, diff_file["id"]),
        )
        diff_conn.execute(
            "UPDATE material_versions SET sha256=? WHERE id=?",
            (hashlib.sha256(pdf_v2).hexdigest(), diff_material["current_version_id"]),
        )
        diff_conn.commit()
        stage_restore(diff_server, archive_path, "e" * 32)
        diff_plan = diff_server.preview_staged_restore("e" * 32)
        material_conflicts = [
            action
            for section in diff_plan.get("sections", [])
            for action in section.get("actions", [])
            if action.get("dataset") == "local_sources"
            and action.get("classification") == "CONFLICT"
            and action.get("reason") == "material_content_differs"
        ]
        check("preview_explains_material_content_conflict", bool(material_conflicts), failures)
        diff_result = diff_server.apply_staged_restore(
            "e" * 32, diff_plan["plan_revision"], decisions_for_notes_restore(diff_plan)
        )
        diff_notes = diff_server.StudyHubHandler.handle_notes_get(
            None, diff_conn, {"targetType": ["file"], "targetId": [str(diff_file["id"])]}
        )
        check(
            "differing_content_anchor_stays_not_ok",
            diff_result.get("ok") is True
            and any(
                note.get("anchor_page") == 1 and note.get("anchor_state") == "stale"
                for note in diff_notes
            ),
            failures,
        )
        diff_conn.close()
        shutil.rmtree(diff_profile, ignore_errors=True)

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
