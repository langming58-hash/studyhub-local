#!/usr/bin/env python3
"""Private packaged helper for bounded local MarkItDown conversion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from markitdown import MarkItDown
from pypdfium2 import PdfDocument


MAX_REQUEST_BYTES = 256 * 1024
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 200_000
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".pptx", ".xlsx"}


def local_input(raw: object) -> Path:
    value = str(raw or "")
    if value.startswith(("http://", "https://", "file://")):
        raise ValueError("remote_input_denied")
    path = Path(value).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("unsupported_input")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input_too_large")
    return path


CONVERTER = MarkItDown(enable_plugins=False)


def handle(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("invalid_request")
    request = json.loads(raw)
    source = local_input(request.get("path"))
    operation = str(request.get("operation") or "")
    if operation == "pdf_page_count" and source.suffix.lower() == ".pdf":
        return {"ok": True, "page_count": len(PdfDocument(source))}
    if operation == "convert":
        result = CONVERTER.convert_local(str(source))
        return {
            "ok": True,
            "text": str(getattr(result, "text_content", "") or "")[:MAX_EXTRACTED_CHARS],
        }
    raise ValueError("unsupported_operation")


def main() -> int:
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        try:
            output = handle(raw)
        except Exception:
            output = {"ok": False, "error": "conversion_failed"}
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
