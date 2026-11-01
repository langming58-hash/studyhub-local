"""Bounded, local-only document extraction adapters for Reader V1."""

from __future__ import annotations

import json
import importlib
import importlib.util
import os
import atexit
import select
import subprocess
import tempfile
import threading
from subprocess import run as run_process
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

MAX_EXTRACTED_CHARS = 200_000
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_OCR_PAGES = 60
MAX_PDF_TEXT_PAGES = 500
MAX_HELPER_RESPONSE_CHARS = MAX_EXTRACTED_CHARS * 2 + 64_000
MIN_USEFUL_TEXT_CHARS = 64
MARKITDOWN_VERSION = "0.1.9"
MAC_OCR_VERSION = "1.1.1"
MARKITDOWN_HELPER_ENV = "STUDYHUB_MARKITDOWN_HELPER"
_HELPER_LOCK = threading.Lock()
_HELPER_PROCESS: subprocess.Popen[str] | None = None
_HELPER_PATH: Path | None = None
_HELPER_TEMP: tempfile.TemporaryDirectory[str] | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    method: str
    version: str
    page_texts: tuple[tuple[int, str], ...] = ()
    page_labels: tuple[str, ...] = ()
    slide_labels: tuple[str, ...] = ()
    ocr_used: bool = False


def _bounded_local_file(path: Path, allowed_roots: Iterable[Path]) -> Path:
    if str(path).startswith(("http://", "https://", "file://")):
        raise ValueError("remote_input_denied")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("unsupported_input")
    roots = tuple(root.resolve() for root in allowed_roots)
    if not roots or not any(resolved == root or root in resolved.parents for root in roots):
        raise PermissionError("source_outside_allowlist")
    return resolved


def _stop_markitdown_helper() -> None:
    global _HELPER_PROCESS, _HELPER_PATH, _HELPER_TEMP
    process = _HELPER_PROCESS
    _HELPER_PROCESS = None
    _HELPER_PATH = None
    if process is not None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    if _HELPER_TEMP is not None:
        _HELPER_TEMP.cleanup()
        _HELPER_TEMP = None


atexit.register(_stop_markitdown_helper)


def _helper_process(helper: Path) -> subprocess.Popen[str]:
    global _HELPER_PROCESS, _HELPER_PATH, _HELPER_TEMP
    if _HELPER_PROCESS is not None and _HELPER_PROCESS.poll() is None and _HELPER_PATH == helper:
        return _HELPER_PROCESS
    _stop_markitdown_helper()
    _HELPER_TEMP = tempfile.TemporaryDirectory(prefix="studyhub-document-helper-")
    _HELPER_PROCESS = subprocess.Popen(
        [str(helper)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=_HELPER_TEMP.name,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": _HELPER_TEMP.name},
        bufsize=1,
    )
    _HELPER_PATH = helper
    return _HELPER_PROCESS


def _run_markitdown_helper(source: Path, operation: str) -> dict[str, object] | None:
    configured = os.environ.get(MARKITDOWN_HELPER_ENV, "").strip()
    if not configured:
        return None
    try:
        helper = Path(configured).resolve(strict=True)
    except OSError:
        return None
    if not helper.is_file() or not os.access(helper, os.X_OK):
        return None
    request = json.dumps({"operation": operation, "path": str(source)})
    with _HELPER_LOCK:
        try:
            process = _helper_process(helper)
            if process.stdin is None or process.stdout is None:
                raise OSError("helper_pipe_unavailable")
            process.stdin.write(request + "\n")
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], 90)
            if not ready:
                raise subprocess.TimeoutExpired(str(helper), 90)
            response = process.stdout.readline()
        except (OSError, BrokenPipeError, subprocess.TimeoutExpired):
            _stop_markitdown_helper()
            return {}
    if not response or len(response) > MAX_HELPER_RESPONSE_CHARS:
        return {}
    try:
        payload = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _bounded_page_texts(raw_pages: object) -> tuple[tuple[int, str], ...]:
    if not isinstance(raw_pages, list) or len(raw_pages) > MAX_PDF_TEXT_PAGES:
        return ()
    pages: list[tuple[int, str]] = []
    remaining = MAX_EXTRACTED_CHARS
    for expected, item in enumerate(raw_pages, start=1):
        if not isinstance(item, dict):
            return ()
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            return ()
        if page != expected or page < 1:
            return ()
        text = str(item.get("text") or "").strip()
        if len(text) > remaining:
            text = text[:remaining]
        pages.append((page, text))
        remaining -= len(text)
        if remaining <= 0:
            break
    return tuple(pages)


def _page_document(page_texts: tuple[tuple[int, str], ...], method: str, version: str) -> ExtractedDocument:
    text = "\n\n".join(value for _page, value in page_texts if value)[:MAX_EXTRACTED_CHARS]
    return ExtractedDocument(
        text,
        method,
        version,
        page_texts=page_texts,
        page_labels=tuple(f"Page {page}" for page, _value in page_texts),
    )


def extract_pdf_pages(path: Path, allowed_roots: Iterable[Path]) -> ExtractedDocument:
    """Extract bounded page text locally while preserving page identity."""
    source = _bounded_local_file(path, allowed_roots)
    helper_result = _run_markitdown_helper(source, "pdf_pages")
    if helper_result and helper_result.get("ok") is True:
        helper_pages = _bounded_page_texts(helper_result.get("pages"))
        if helper_pages:
            return _page_document(helper_pages, "pypdfium2", "packaged")
    try:
        pypdfium2 = importlib.import_module("pypdfium2")
        document = pypdfium2.PdfDocument(source)
    except Exception:
        return ExtractedDocument("", "pypdfium2", "missing")
    try:
        if len(document) < 1 or len(document) > MAX_PDF_TEXT_PAGES:
            return ExtractedDocument("", "pypdfium2", "bounded")
        raw_pages: list[dict[str, object]] = []
        remaining = MAX_EXTRACTED_CHARS
        for number, page in enumerate(document, start=1):
            try:
                text_page = page.get_textpage()
                try:
                    text = str(text_page.get_text_range() or "").strip()[:remaining]
                finally:
                    text_page.close()
            finally:
                page.close()
            raw_pages.append({"page": number, "text": text})
            remaining -= len(text)
            if remaining <= 0:
                break
        return _page_document(_bounded_page_texts(raw_pages), "pypdfium2", "local")
    except Exception:
        return ExtractedDocument("", "pypdfium2", "failed")
    finally:
        document.close()


def local_pdf_extraction_available() -> bool:
    configured = os.environ.get(MARKITDOWN_HELPER_ENV, "").strip()
    if configured:
        try:
            helper = Path(configured).resolve(strict=True)
            if helper.is_file() and os.access(helper, os.X_OK):
                return True
        except OSError:
            pass
    try:
        return importlib.util.find_spec("pypdfium2") is not None or importlib.util.find_spec("markitdown") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def extract_with_markitdown(path: Path, allowed_roots: Iterable[Path]) -> ExtractedDocument:
    source = _bounded_local_file(path, allowed_roots)
    helper_result = _run_markitdown_helper(source, "convert")
    if helper_result is not None:
        text = str(helper_result.get("text") or "")[:MAX_EXTRACTED_CHARS]
        return ExtractedDocument(text, "markitdown", MARKITDOWN_VERSION)
    try:
        MarkItDown = importlib.import_module("markitdown").MarkItDown
    except ImportError:
        return ExtractedDocument("", "markitdown", "missing")
    try:
        result = MarkItDown(enable_plugins=False).convert_local(str(source))
    except Exception:
        return ExtractedDocument("", "markitdown", MARKITDOWN_VERSION)
    text = str(getattr(result, "text_content", "") or "")[:MAX_EXTRACTED_CHARS]
    return ExtractedDocument(text, "markitdown", MARKITDOWN_VERSION)


def _pdf_page_count(source: Path) -> int | None:
    helper_result = _run_markitdown_helper(source, "pdf_page_count")
    if helper_result and helper_result.get("ok") is True:
        try:
            page_count = int(helper_result.get("page_count") or 0)
            if page_count > 0:
                return page_count
        except (TypeError, ValueError):
            pass
    try:
        pypdfium2 = importlib.import_module("pypdfium2")
        return len(pypdfium2.PdfDocument(source))
    except Exception:
        # PDF parsers expose backend-specific format errors; all must fail closed.
        return None


def extract_with_mac_ocr(
    path: Path,
    allowed_roots: Iterable[Path],
    executable: Path,
    *,
    timeout_seconds: int = 90,
) -> ExtractedDocument:
    source = _bounded_local_file(path, allowed_roots)
    binary = executable.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return ExtractedDocument("", "mac-ocr", "missing")
    with source.open("rb") as source_stream:
        is_pdf = source_stream.read(5) == b"%PDF-"
    if is_pdf:
        page_count = _pdf_page_count(source)
        if page_count is None or page_count < 1 or page_count > MAX_OCR_PAGES:
            return ExtractedDocument("", "mac-ocr", MAC_OCR_VERSION)
    try:
        with tempfile.TemporaryDirectory(prefix="studyhub-ocr-") as tmp:
            completed = run_process(
                [str(binary), str(source), "--format", "jsonl", "--pdf-dpi", "180"],
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=tmp,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": tmp},
            )
    except (OSError, subprocess.TimeoutExpired):
        return ExtractedDocument("", "mac-ocr", MAC_OCR_VERSION)
    if completed.returncode != 0:
        return ExtractedDocument("", "mac-ocr", MAC_OCR_VERSION)
    page_texts: list[tuple[int, str]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return ExtractedDocument("", "mac-ocr", MAC_OCR_VERSION)
        page = int(item.get("page") or 1)
        value = str(item.get("text") or "").strip()
        page_texts.append((page, value))
    bounded_pages = _bounded_page_texts(
        [{"page": page, "text": value} for page, value in page_texts]
    )
    return ExtractedDocument(
        "\n\n".join(value for _page, value in bounded_pages if value)[:MAX_EXTRACTED_CHARS],
        "mac-ocr",
        MAC_OCR_VERSION,
        page_texts=bounded_pages,
        page_labels=tuple(f"Page {page}" for page, _value in bounded_pages),
        ocr_used=True,
    )


def extract_document(
    path: Path,
    allowed_roots: Iterable[Path],
    *,
    pdftotext: Callable[[Path], str],
    legacy_office: Callable[[Path], str],
    plain_text: Callable[[Path], str],
    ocr_executable: Path | None = None,
) -> ExtractedDocument:
    """Run the deterministic local extraction chain without mutating the source."""
    source = _bounded_local_file(path, allowed_roots)
    ext = source.suffix.lower()
    if ext == ".pdf":
        text = pdftotext(source).strip()
        if len(text) >= MIN_USEFUL_TEXT_CHARS:
            return ExtractedDocument(text[:MAX_EXTRACTED_CHARS], "pdftotext", "system")
        pages = extract_pdf_pages(source, allowed_roots)
        if pages.text.strip():
            return pages
        result = extract_with_markitdown(source, allowed_roots)
        if len(result.text.strip()) >= MIN_USEFUL_TEXT_CHARS:
            return result
        if ocr_executable:
            ocr = extract_with_mac_ocr(source, allowed_roots, ocr_executable)
            if ocr.text.strip():
                return ocr
        if result.text.strip():
            return result
        return ExtractedDocument(text[:MAX_EXTRACTED_CHARS], "pdftotext", "system")
    if ext in {".docx", ".pptx", ".xlsx"}:
        result = extract_with_markitdown(source, allowed_roots)
        if result.text.strip():
            return result
        fallback = legacy_office(source)
        return ExtractedDocument(fallback[:MAX_EXTRACTED_CHARS], "builtin-office", "1")
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".heic"} and ocr_executable:
        return extract_with_mac_ocr(source, allowed_roots, ocr_executable)
    return ExtractedDocument(plain_text(source)[:MAX_EXTRACTED_CHARS], "builtin-text", "1")
