#!/usr/bin/env python3
"""PDF text excerpts acceptance for Phase 4."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

from source_linked_notes_acceptance import load_server, synthetic_pdf  # noqa: E402


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def post_note(server, payload):
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: payload
    sent = {}
    handler.send_json = lambda data, status=200: sent.update({"status": status, "data": data})
    server.StudyHubHandler.handle_note(handler)
    return sent


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-excerpts-") as raw:
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

        # Reader metadata carries the source-version identity.
        reader_handler = object.__new__(server.StudyHubHandler)
        reader_payload = {}
        reader_handler.send_json = lambda data, status=200: reader_payload.update(data)
        reader_handler.handle_api_get("/api/reader", {"id": [str(file_id)]})
        check("reader_metadata_exposes_source_version", reader_payload.get("source_version_stable_id") == version_stable, failures)

        base = {"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"]}

        # A matching source version saves an anchored excerpt note.
        matching = post_note(server, {**base, "body": "> Page one text", "anchorPage": 1, "pageCount": 3, "sourceVersion": version_stable})
        check("matching_source_version_saves", matching.get("status") == 200, failures)

        # A changed source version fails safely before persisting.
        try:
            post_note(server, {**base, "body": "> Page one text", "anchorPage": 1, "pageCount": 3, "sourceVersion": "matver_changed"})
            check("changed_source_version_rejected", False, failures)
        except ValueError:
            check("changed_source_version_rejected", True, failures)

        # Malformed explicit sourceVersion values fail closed.
        for bad_version in ("", None, False, 123, {"id": "x"}, ["x"]):
            try:
                post_note(server, {**base, "body": "> Page one text", "anchorPage": 1, "pageCount": 3, "sourceVersion": bad_version})
                check(f"malformed_source_version_{bad_version!r}_rejected", False, failures)
            except ValueError:
                check(f"malformed_source_version_{bad_version!r}_rejected", True, failures)

        # Physical PDF replacement without rescan rejects the excerpt save.
        pdf.write_bytes(synthetic_pdf(["Replaced one", "Replaced two", "Replaced three"]))
        replaced_ok = False
        try:
            post_note(server, {**base, "body": "> Page one text", "anchorPage": 1, "pageCount": 3, "sourceVersion": version_stable})
            replaced_ok = True
        except ValueError:
            pass
        check("physical_replacement_without_rescan_rejected", not replaced_ok, failures)
        notes_after = server.StudyHubHandler.handle_notes_get(None, conn, {"targetType": ["file"], "targetId": [str(file_id)]})
        check("rejected_excerpt_inserts_no_anchored_note", sum(1 for n in notes_after if n.get("anchor_page") == 1) == 1, failures)
        pdf.write_bytes(pdf_bytes)

        # Ordinary unanchored notes remain unaffected.
        unanchored = post_note(server, {**base, "body": "Plain manual note"})
        check("plain_note_unchanged", unanchored.get("status") == 200, failures)

        notes = server.StudyHubHandler.handle_notes_get(None, conn, {"targetType": ["file"], "targetId": [str(file_id)]})
        check("excerpt_note_anchored_to_selected_page", any(n.get("body", "").startswith("> Page one text") and n.get("anchor_page") == 1 for n in notes), failures)
        check("plain_note_is_unanchored", any(n.get("body") == "Plain manual note" and n.get("anchor_page") is None for n in notes), failures)

        check("original_pdf_unchanged", hashlib.sha256(pdf.read_bytes()).hexdigest() == before, failures)
        conn.close()

    if failures:
        print("PDF text excerpts acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("PDF text excerpts acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
