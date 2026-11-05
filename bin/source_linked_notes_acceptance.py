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

        # Full restore round-trip into an isolated workspace.
        restore_tmp = Path(tempfile.mkdtemp(prefix="studyhub-notes-restore-"))
        restore_library = restore_tmp / "StudyLibrary"
        restore_db = restore_tmp / "runtime" / "studyhub.sqlite"
        restored_pdf = restore_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        restored_pdf.parent.mkdir(parents=True)
        restored_pdf.write_bytes(pdf.read_bytes())
        target = load_server(restore_tmp, restore_library, restore_db)
        target.scan_library(restore_library)
        tconn_init = target.connect_db()
        target.init_db(tconn_init)
        target.run_schema_migrations(tconn_init)
        tconn_init.commit()
        tconn_init.close()
        target.RESTORE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        restore_id = "a" * 32
        staged = target.RESTORE_INBOX_DIR / f"{restore_id}.zip"
        shutil.copyfile(archive_path, staged)
        plan = target.preview_staged_restore(restore_id)
        def decisions_for(plan):
            result = []
            for section in plan.get("sections", []):
                for action in section.get("actions", []):
                    classification = action.get("classification")
                    if classification == "CONFLICT":
                        decision = "KEEP_LOCAL"
                    elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                        decision = "SKIP"
                    else:
                        continue
                    result.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
            return result
        result = target.apply_staged_restore(restore_id, plan["plan_revision"], decisions_for(plan))
        check("restore_apply_succeeds", result.get("ok") is True or "applied" in result, failures)
        tconn = target.connect_db()
        target.init_db(tconn)
        restored_file = tconn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        if restored_file:
            restored = target.StudyHubHandler.handle_notes_get(None, tconn, {"targetType": ["file"], "targetId": [str(restored_file["id"])]})
            check("restore_preserves_anchor_metadata", any(n.get("anchor_page") for n in restored), failures)
            check("restore_preserves_unanchored_note", any(n.get("anchor_page") is None for n in restored), failures)
        else:
            check("restore_preserves_anchor_metadata", False, failures)
            check("restore_preserves_unanchored_note", False, failures)
        tconn.close()
        shutil.rmtree(restore_tmp, ignore_errors=True)

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
