#!/usr/bin/env python3
"""Source-linked page notes acceptance for Phase 4."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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


def synthetic_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
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


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-notes-") as raw:
        tmp = Path(raw)
        library = tmp / "StudyLibrary"
        database = tmp / "runtime" / "studyhub.sqlite"
        server = load_server(tmp, library, database)

        pdf = library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(synthetic_pdf("Synthetic page one"))
        before = hashlib.sha256(pdf.read_bytes()).hexdigest()
        server.scan_library(library)

        conn = server.connect_db()
        server.init_db(conn)
        file_row = conn.execute("SELECT * FROM files WHERE filename='notes.pdf'").fetchone()
        file_id = int(file_row["id"])
        material_stable, version_stable = server.note_anchor_for_file(conn, file_id)
        check("anchor_resolves_material_and_version", bool(material_stable) and bool(version_stable), failures)

        def note_create(payload):
            handler = object.__new__(server.StudyHubHandler)
            handler.parse_body_json = lambda: payload
            sent = {}
            handler.send_json = lambda data, status=200: sent.update({"status": status, "data": data})
            server.StudyHubHandler.handle_note(handler)
            return sent

        note_create({"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"], "body": "Page two summary", "anchorPage": 2})
        note_create({"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"], "body": "Unanchored note"})
        note_create({"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"], "body": "Page three detail", "anchorPage": 3})

        notes = server.StudyHubHandler.handle_notes_get(None, conn, {"targetType": ["file"], "targetId": [str(file_id)]})
        check("multiple_notes_created", len(notes) == 3, failures)
        by_page = {note["anchor_page"]: note for note in notes if note["anchor_page"]}
        check("anchored_notes_have_pages", set(by_page) == {2, 3}, failures)
        check("anchored_notes_have_anchor_state_ok", all(note["anchor_state"] == "ok" for note in by_page.values()), failures)
        check("unanchored_note_has_no_page", any(note.get("anchor_page") is None for note in notes), failures)
        check("anchored_note_has_stable_material", all(note["anchor_material_stable_id"] == material_stable for note in by_page.values()), failures)

        try:
            note_create({"targetType": "file", "targetId": file_id, "body": "bad", "anchorPage": 0})
            check("invalid_page_zero_rejected", False, failures)
        except ValueError:
            check("invalid_page_zero_rejected", True, failures)
        try:
            note_create({"targetType": "file", "targetId": file_id, "body": "bad", "anchorPage": 10001})
            check("out_of_range_page_rejected", False, failures)
        except ValueError:
            check("out_of_range_page_rejected", True, failures)

        check("original_pdf_unchanged", hashlib.sha256(pdf.read_bytes()).hexdigest() == before, failures)

        backup = server.export_current_profile_backup()
        archive_path = server.EXPORT_DIR / backup["filename"]
        with zipfile.ZipFile(archive_path) as archive:
            dataset = json.loads(archive.read("data/notes.json").decode("utf-8"))
        records = dataset.get("records", []) if isinstance(dataset, dict) else dataset
        anchored = [record for record in records if record.get("anchor_page")]
        check("backup_includes_anchor_fields", len(anchored) == 2 and all(record.get("anchor_material_stable_id") for record in anchored), failures)
        check("backup_keeps_target_ref_not_path", all("target_ref" in record and "target_id" not in record for record in records), failures)

        conn.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
