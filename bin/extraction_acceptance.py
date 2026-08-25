#!/usr/bin/env python3
"""Synthetic extraction recovery regression checks. Uses no real course material."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "StudyLibrary")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["OPENAI_VECTOR_STORE_ID"] = ""
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_extraction_test", server_path)
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    server.DEFAULT_STUDY_ROOT = tmp / "StudyLibrary"
    return server


def call_ask(server, body: dict) -> dict:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: body
    sent: dict = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_ask(handler)
    return sent["data"]


def file_row(server, filename: str) -> sqlite3.Row:
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM files WHERE filename=? AND active=1", (filename,)).fetchone()
    conn.close()
    assert row is not None
    return row


def counts_for_file(server, file_id: int) -> dict[str, int | str]:
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    chunks = conn.execute("SELECT COUNT(*) AS c FROM document_chunks WHERE file_id=?", (file_id,)).fetchone()["c"]
    conn.close()
    return {
        "chunks": chunks,
        "text_cache_path": row["text_cache_path"] or "",
        "ai_index_status": row["ai_index_status"] or "",
        "ai_index_error": row["ai_index_error"] or "",
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        root = server.DEFAULT_STUDY_ROOT
        pdf = root / "TEST1001 TEST1002 - Synthetic Shared Course" / "Week 04" / "02 Exercises" / "Tutorial" / "Tutorial_04.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\n% synthetic placeholder for scanner regression\n")

        old_extract_text = server.extract_text
        old_extract_chunks = server.extract_document_chunks
        old_error_for = server.extraction_error_for

        server.extract_text = lambda path: ""
        server.extract_document_chunks = lambda path, text: []
        server.extraction_error_for = lambda path, text: "PDF text extraction unavailable: pdftotext was not found. Install Poppler and rescan the library."
        stats1 = server.scan_library(root)
        first = file_row(server, "Tutorial_04.pdf")
        first_state = counts_for_file(server, first["id"])

        recovered_text = "Recovered synthetic source text. The tutorial asks about separable differential equations. Q1. Explain the recovered concept."
        server.extract_text = lambda path: recovered_text
        server.extract_document_chunks = lambda path, text: server.chunk_plain_text(text, "p.1", page_start=1)
        server.extraction_error_for = lambda path, text: "" if text else old_error_for(path, text)
        stats2 = server.scan_library(root)
        second = file_row(server, "Tutorial_04.pdf")
        second_state = counts_for_file(server, second["id"])

        stats3 = server.scan_library(root)
        third_state = counts_for_file(server, second["id"])

        cache_path = Path(third_state["text_cache_path"])
        cache_path.unlink()
        stats4 = server.scan_library(root)
        fourth_state = counts_for_file(server, second["id"])

        ask = call_ask(
            server,
            {
                "context": {"fileId": second["id"]},
                "prompt": "Explain the recovered concept.",
            },
        )
        generic_ask = call_ask(
            server,
            {
                "context": {"fileId": second["id"]},
                "prompt": "讲解一下",
            },
        )

        server.extract_text = old_extract_text
        server.extract_document_chunks = old_extract_chunks
        server.extraction_error_for = old_error_for

        checks = {
            "first_scan_records_pdf_dependency_error": stats1.new_files == 1
            and first_state["chunks"] == 0
            and first_state["ai_index_status"] == "not_indexed"
            and "pdftotext was not found" in first_state["ai_index_error"],
            "unchanged_failed_pdf_retried_after_extractor_recovers": stats2.updated_files == 1
            and second_state["chunks"] > 0
            and second_state["ai_index_status"] == "indexed"
            and second_state["ai_index_error"] == "",
            "healthy_unchanged_pdf_skipped": stats3.unchanged_files == 1 and stats3.updated_files == 0,
            "missing_text_cache_rebuilt": stats4.updated_files == 1
            and Path(fourth_state["text_cache_path"]).exists()
            and fourth_state["chunks"] > 0,
            "askgpt_current_file_source_backed": ask["status"] == "local"
            and ask["sources"]
            and ask["sources"][0]["source_file_id"] == second["id"]
            and "Recovered synthetic source text" in ask["response"],
            "askgpt_current_file_generic_prompt_uses_current_file": generic_ask["status"] == "local"
            and generic_ask["sources"]
            and generic_ask["sources"][0]["source_file_id"] == second["id"]
            and "Recovered synthetic source text" in generic_ask["response"],
            "shared_course_display_label": server.display_course_code("TEST1001 TEST1002") == "TEST1001/1002",
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
