#!/usr/bin/env python3
"""No-Poppler PDF page-grounding acceptance using only synthetic local data."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import reader_extractors


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


def load_server(tmp: Path, helper: Path):
    root = tmp / "StudyLibrary"
    os.environ.update(
        {
            "STUDY_LIBRARY_PATH": str(root),
            "DATABASE_PATH": str(tmp / "studyhub.sqlite"),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
            "STUDYHUB_MARKITDOWN_HELPER": str(helper),
        }
    )
    spec = importlib.util.spec_from_file_location("studyhub_server_pdf_grounding_test", ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA_DIR = tmp / "data"
    module.CACHE_DIR = tmp / "cache"
    module.TEXT_CACHE_DIR = module.CACHE_DIR / "text"
    module.LOG_DIR = tmp / "logs"
    module.DB_PATH = tmp / "studyhub.sqlite"
    module.DEFAULT_STUDY_ROOT = root
    return module


def call_ask(server, file_id: int) -> dict[str, object]:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: {
        "context": {"fileId": file_id},
        "prompt": "Explain the Synthetic Beta Question.",
    }
    sent: dict[str, object] = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_ask(handler)
    return sent["data"]


def main() -> int:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="studyhub-pdf-grounding-") as raw:
        tmp = Path(raw)
        helper = tmp / "studyhub-markitdown"
        helper.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " "
            + shlex.quote(str(ROOT / "bin" / "markitdown_helper.py"))
            + "\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        server = load_server(tmp, helper)
        source = (
            server.DEFAULT_STUDY_ROOT
            / "TEST4100 - Synthetic PDF Grounding"
            / "Week 02"
            / "02 Exercises"
            / "Tutorial"
            / "Synthetic Tutorial.pdf"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(
            synthetic_pdf(
                [
                    "Synthetic Alpha Concept: local-first course material grounding",
                    "Q2. Synthetic Beta Question: explore derivatives with limits",
                ]
            )
        )
        before = hashlib.sha256(source.read_bytes()).hexdigest()

        original_configured_tool = server.configured_tool
        server.configured_tool = lambda name, env: "" if name in {"pdftotext", "pdfinfo"} else original_configured_tool(name, env)
        network_calls: list[str] = []

        def blocked_network(*_args, **_kwargs):
            network_calls.append("network")
            raise AssertionError("network access is forbidden in PDF grounding acceptance")

        server.urllib.request.urlopen = blocked_network
        server.default_canvas_http_get = blocked_network
        server.default_canvas_download_open = blocked_network
        server.initialize_credential_client = lambda: None

        result = server.extract_document(source)
        chunks = server.extract_document_chunks(source, result)
        stats = server.scan_library(server.DEFAULT_STUDY_ROOT)
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        file_row = conn.execute("SELECT * FROM files WHERE filename=?", (source.name,)).fetchone()
        assert file_row is not None
        stored_chunks = conn.execute(
            "SELECT source_location, page_start, text FROM document_chunks WHERE file_id=? ORDER BY chunk_index",
            (file_row["id"],),
        ).fetchall()
        question = conn.execute(
            "SELECT question_number, source_location, question_text FROM questions WHERE file_id=?",
            (file_row["id"],),
        ).fetchone()
        search = server.mcp_search_content({"query": "Synthetic Beta Question", "course": "TEST4100"})
        ask = call_ask(server, int(file_row["id"]))
        legacy_cache = server.TEXT_CACHE_DIR / f"{file_row['sha256']}-extract-v3-reader-local.txt"
        legacy_cache.write_text(result.text, encoding="utf-8")
        conn.execute("UPDATE files SET text_cache_path=? WHERE id=?", (str(legacy_cache), file_row["id"]))
        conn.execute(
            "UPDATE document_chunks SET source_location='file', page_start=NULL, page_end=NULL WHERE file_id=?",
            (file_row["id"],),
        )
        conn.commit()
        conn.close()
        upgrade_stats = server.scan_library(server.DEFAULT_STUDY_ROOT)
        upgrade_conn = sqlite3.connect(server.DB_PATH)
        upgrade_conn.row_factory = sqlite3.Row
        upgraded_page_two = upgrade_conn.execute(
            "SELECT source_location, page_start FROM document_chunks WHERE file_id=? AND page_start=2",
            (file_row["id"],),
        ).fetchone()
        upgrade_conn.close()
        reader_extractors._stop_markitdown_helper()

        page_one = next((chunk for chunk in chunks if chunk["page_start"] == 1), None)
        page_two = next((chunk for chunk in chunks if chunk["page_start"] == 2), None)
        stored_page_two = next((chunk for chunk in stored_chunks if chunk["page_start"] == 2), None)
        search_page_two = next(
            (item for item in search["matches"] if item.get("source_location") == "p.2"),
            None,
        )
        ask_page_two = next(
            (item for item in ask.get("sources", []) if item.get("source_location") == "p.2"),
            None,
        )
        checks.update(
            {
                "real_two_page_pdf": len(result.page_texts) == 2,
                "no_poppler_local_extraction": result.method == "pypdfium2"
                and "Synthetic Alpha Concept" in result.text
                and "Synthetic Beta Question" in result.text,
                "page_1_chunk_grounded": bool(page_one)
                and page_one["source_location"] == "p.1"
                and "Synthetic Alpha Concept" in page_one["text"],
                "page_2_chunk_grounded": bool(page_two)
                and page_two["source_location"] == "p.2"
                and "Synthetic Beta Question" in page_two["text"],
                "normal_index_preserves_page_2": stats.new_files == 1
                and bool(stored_page_two)
                and stored_page_two["source_location"] == "p.2",
                "search_page_2_grounding": bool(search_page_two)
                and search_page_two.get("page_start") == 2,
                "ask_page_2_grounding": bool(ask_page_two)
                and ask_page_two.get("page_start") == 2,
                "teacher_question_page_2_grounding": bool(question)
                and question["question_number"] == "Q2"
                and question["source_location"] == "p.2"
                and "Synthetic Beta Question" in question["question_text"],
                "unchanged_legacy_cache_reindexed_page_2": upgrade_stats.updated_files == 1
                and bool(upgraded_page_two)
                and upgraded_page_two["source_location"] == "p.2",
                "original_pdf_unchanged": before == hashlib.sha256(source.read_bytes()).hexdigest(),
                "no_network_or_credentials": not network_calls,
                "no_generic_file_source": all(chunk["source_location"] != "file" for chunk in stored_chunks),
            }
        )

    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
