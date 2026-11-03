#!/usr/bin/env python3
"""Low-text PDF OCR fallback acceptance for the Reader V1 extraction chain."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shlex
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import reader_extractors


def synthetic_pdf(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
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


def load_server(tmp: Path):
    os.environ.update(
        {
            "STUDY_LIBRARY_PATH": str(tmp / "StudyLibrary"),
            "DATABASE_PATH": str(tmp / "studyhub.sqlite"),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    os.environ.pop("STUDYHUB_MARKITDOWN_HELPER", None)
    spec = importlib.util.spec_from_file_location("studyhub_server_low_text_ocr_test", ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA_DIR = tmp / "data"
    module.CACHE_DIR = tmp / "cache"
    module.TEXT_CACHE_DIR = module.CACHE_DIR / "text"
    module.LOG_DIR = tmp / "logs"
    module.DB_PATH = tmp / "studyhub.sqlite"
    module.DEFAULT_STUDY_ROOT = tmp / "StudyLibrary"
    return module


def main() -> int:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="studyhub-low-text-ocr-") as raw:
        tmp = Path(raw)
        server = load_server(tmp)

        source = tmp / "low-text.pdf"
        source.write_bytes(synthetic_pdf("x"))
        before = hashlib.sha256(source.read_bytes()).hexdigest()

        fake_ocr = tmp / "fake-mac-ocr"
        invocation_log = tmp / "ocr-invocations.txt"
        fake_ocr.write_text(
            "#!/bin/sh\n"
            f"printf 'invoked\\n' >> {shlex.quote(str(invocation_log))}\n"
            "printf '%s\\n' '{\"page\":1,\"pageCount\":1,\"text\":\"Synthetic OCR recovered page content\"}'\n",
            encoding="utf-8",
        )
        fake_ocr.chmod(0o755)

        server.extract_text_from_pdf = lambda _path: ""
        server.pdf_page_count = lambda _path: 0
        server.reader_ocr_executable = lambda: fake_ocr

        network_calls: list[str] = []

        def blocked_network(*_args, **_kwargs):
            network_calls.append("network")
            raise AssertionError("network access is forbidden in low-text OCR acceptance")

        server.urllib.request.urlopen = blocked_network
        server.default_canvas_http_get = blocked_network
        server.default_canvas_download_open = blocked_network
        server.initialize_credential_client = lambda: None

        page_candidate = reader_extractors.extract_pdf_pages(source, [source])
        result = server.extract_document(source)
        chunks = server.extract_document_chunks(source, result)
        reader_extractors._stop_markitdown_helper()

        page_one = next((chunk for chunk in chunks if chunk["page_start"] == 1), None)
        checks.update(
            {
                "pypdfium2_candidate_exists": page_candidate.method == "pypdfium2"
                and page_candidate.text.strip() == "x"
                and len(page_candidate.page_texts) == 1,
                "pypdfium2_candidate_below_threshold": len(page_candidate.text.strip())
                < reader_extractors.MIN_USEFUL_TEXT_CHARS,
                "ocr_actually_invoked": invocation_log.read_text(encoding="utf-8").strip()
                == "invoked",
                "result_ocr_used": result.ocr_used is True,
                "result_method_is_mac_ocr": result.method == "mac-ocr",
                "result_page_texts_grounded": result.page_texts
                == ((1, "Synthetic OCR recovered page content"),),
                "chunk_page_one_location": bool(page_one)
                and page_one["source_location"] == "p.1"
                and page_one["page_start"] == 1
                and "Synthetic OCR recovered page content" in page_one["text"],
                "original_pdf_unchanged": before == hashlib.sha256(source.read_bytes()).hexdigest(),
                "no_network_call": not network_calls,
                "no_canvas_call": not network_calls,
                "no_credential_store_call": not network_calls,
            }
        )

    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
