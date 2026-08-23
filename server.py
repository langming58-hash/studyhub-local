#!/usr/bin/env python3
"""StudyHub Local: private local-first study library server."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import mimetypes
import os
import ipaddress
import re
import shutil
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

APP_NAME = "StudyHub Local"
APP_ROOT = Path(__file__).resolve().parent


def load_local_env() -> None:
    env_path = APP_ROOT / ".env.local"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8765"))
DATA_DIR = APP_ROOT / "data"
CACHE_DIR = APP_ROOT / "cache"
TEXT_CACHE_DIR = CACHE_DIR / "text"
LOG_DIR = APP_ROOT / "logs"
STATIC_DIR = APP_ROOT / "static"
DEMO_MODE = os.environ.get("DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def configured_path(env_name: str, fallback: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    path = Path(raw).expanduser() if raw else fallback
    if not path.is_absolute():
        path = APP_ROOT / path
    return path


DB_PATH = configured_path("DATABASE_PATH", DATA_DIR / "studyhub.sqlite")
DEFAULT_STUDY_ROOT = configured_path(
    "STUDY_LIBRARY_PATH",
    APP_ROOT / "demo-data" if DEMO_MODE else Path.home() / "StudyLibrary",
)
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
CSRF_TOKEN = secrets.token_urlsafe(32)
MAX_JSON_BODY_SIZE = int(os.environ.get("MAX_JSON_BODY_SIZE", str(512 * 1024)))
MAX_MCP_BODY_SIZE = int(os.environ.get("MAX_MCP_BODY_SIZE", str(512 * 1024)))
MAX_UPLOAD_FILE_SIZE = int(os.environ.get("MAX_UPLOAD_FILE_SIZE", str(50 * 1024 * 1024)))
MAX_UPLOAD_REQUEST_SIZE = int(os.environ.get("MAX_UPLOAD_REQUEST_SIZE", str(60 * 1024 * 1024)))
CHUNK_TARGET_CHARS = 3200
QUESTION_RE = re.compile(r"^\s*(?:Q(?:uestion)?\.?\s*)?(?P<num>\d{1,3}|[A-Z])[\).\:]\s+(?P<body>.{12,})", re.IGNORECASE)
SOLUTION_RE = re.compile(r"\b(solution|solutions|answer|answers|worked|key)\b", re.IGNORECASE)
QUESTION_CATEGORY_RE = re.compile(r"\b(tutorial|workshop|lab|practice|revision|quiz)\b", re.IGNORECASE)
ASK_QUESTION_RE = re.compile(r"\bQ(?:uestion)?\.?\s*(?P<num>\d{1,3}|[A-Z])\b|题\s*(?P<cnum>\d{1,3})", re.IGNORECASE)
SOLUTION_INTENT_RE = re.compile(r"\b(solution|answer|solve|calculate|work(?:ed)?\s*out)\b|答案|解答|求解|计算", re.IGNORECASE)

ACADEMIC_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".tsv",
    ".txt",
    ".md",
    ".py",
    ".ipynb",
    ".r",
    ".html",
    ".htm",
    ".xhtml",
    ".xml",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
ACTIVE_WEB_PREVIEW_EXTS = {".html", ".htm", ".xhtml", ".xml", ".svg"}
OOXML_PREVIEW_EXTS = {".docx", ".pptx", ".xlsx"}
INTERNAL_FILENAMES = {
    "source index.csv",
    "question bank.csv",
    "folder tree.txt",
    "sync report - 2026-08-20.txt",
    "sync-state.json",
    "readme.txt",
}
COURSE_RE = re.compile(r"^(?P<code>[A-Z]{3,5}\d{4}(?:\s+[A-Z]{3,5}\d{4})*)\s+-\s+(?P<name>.+)$")
WEEK_RE = re.compile(r"^Week\s+(?P<num>\d{2})$")
SAFE_COMPONENT_RE = re.compile(r"^[\w .(),&+-]{1,120}$", re.UNICODE)
ALLOWED_SECTIONS = {"00 Course Information", "01 Course Materials", "02 Exercises", "My_Work", "Review"}


class PayloadTooLarge(ValueError):
    pass


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, TEXT_CACHE_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_event(event: str, detail: str) -> None:
    ensure_dirs()
    with (LOG_DIR / "studyhub.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now_iso(), "event": event, "detail": detail}, ensure_ascii=False) + "\n")


def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=None).encode("utf-8")


def safe_relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_path_component(component: Any) -> str:
    value = str(component or "").strip()
    if (
        not value
        or "\x00" in value
        or ".." in value
        or "/" in value
        or "\\" in value
        or Path(value).is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or not SAFE_COMPONENT_RE.fullmatch(value)
    ):
        raise PermissionError("Unsafe path component")
    return value


def safe_child_path(root: Path, *components: Any) -> Path:
    base = root.expanduser().resolve()
    validated = [validate_path_component(component) for component in components]
    target = base.joinpath(*validated).resolve()
    if not is_inside(target, base):
        raise PermissionError("Path escapes configured study library")
    return target


def safe_cache_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not is_inside(path, TEXT_CACHE_DIR):
        raise PermissionError("Cache path outside StudyHub cache")
    return path.resolve()


def read_cached_text(row: sqlite3.Row | dict[str, Any], limit: int) -> str:
    path = safe_cache_path(row["text_cache_path"])
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    return ""


def is_loopback_host(host: str) -> bool:
    candidate = (host or "").strip().strip("[]").lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def validate_loopback_bind_host(host: str) -> str:
    if not is_loopback_host(host):
        raise PermissionError("StudyHub Local refuses non-loopback binding for privacy reasons.")
    return host


def header_host_without_port(value: str) -> str:
    host = (value or "").strip()
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    return host.rsplit(":", 1)[0] if ":" in host and host.count(":") == 1 else host


def split_host_port(value: str) -> tuple[str, int | None]:
    host = (value or "").strip()
    if not host:
        return "", None
    if host.startswith("[") and "]" in host:
        name = host[1 : host.index("]")]
        rest = host[host.index("]") + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            return name.lower(), int(rest[1:])
        return name.lower(), None
    if ":" in host and host.count(":") == 1:
        name, port = host.rsplit(":", 1)
        return name.lower(), int(port) if port.isdigit() else None
    return host.lower(), None


def default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def is_safe_loopback_origin(origin: str, host_header: str) -> bool:
    if not origin:
        return True
    parsed = urllib.parse.urlparse(origin)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    origin_host = parsed.hostname.lower()
    if not is_loopback_host(origin_host):
        return False
    request_host, request_port = split_host_port(host_header)
    if not request_host or not is_loopback_host(request_host):
        return False
    origin_port = parsed.port or default_port_for_scheme(parsed.scheme)
    request_port = request_port or default_port_for_scheme("http")
    return origin_host == request_host and origin_port == request_port


def request_host_is_loopback(host_header: str) -> bool:
    host = header_host_without_port(host_header)
    return bool(host and is_loopback_host(host))


def public_course(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in ("id", "code", "name", "folder_name", "created_at", "updated_at", "file_count", "latest_week")
        if key in row.keys()
    }


def public_week(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "course_id", "week_label", "week_number", "has_materials", "file_count") if key in row.keys()}


def public_file(row: sqlite3.Row | dict[str, Any], include_text: str | None = None) -> dict[str, Any]:
    allowed = (
        "id",
        "course_id",
        "week_id",
        "course_code",
        "course_name",
        "week_label",
        "week_number",
        "section",
        "category",
        "exercise_type",
        "filename",
        "rel_path",
        "source",
        "source_label",
        "size",
        "modified_at",
        "indexed_at",
        "extension",
        "mime_type",
        "is_official",
        "suspicious",
        "stable_id",
        "source_type",
        "file_extension",
        "file_size",
        "sha256",
        "is_solution",
        "is_question_source",
        "active",
        "missing_at",
        "ai_index_status",
        "ai_index_error",
        "star_id",
        "rank",
    )
    data = {key: row[key] for key in allowed if key in row.keys()}
    if include_text is not None:
        data["extractedText"] = include_text
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_category(parts: list[str]) -> tuple[str, str, str]:
    section = ""
    category = ""
    source = "Official Canvas Material"
    lower_parts = [p.lower() for p in parts]
    if any(p in {"my_work", "my work"} for p in lower_parts):
        source = "My Work"
    if any(p in {"review", "revision"} for p in lower_parts):
        source = "AI Explanation" if "ai" in " ".join(lower_parts) else source
    if "01 course materials" in lower_parts:
        section = "01 Course Materials"
        idx = lower_parts.index("01 course materials")
        category = parts[idx + 1] if idx + 1 < len(parts) else "Other"
    elif "02 exercises" in lower_parts:
        section = "02 Exercises"
        idx = lower_parts.index("02 exercises")
        category = parts[idx + 1] if idx + 1 < len(parts) else "Other"
    elif "00 course information" in lower_parts:
        section = "00 Course Information"
        idx = lower_parts.index("00 course information")
        category = parts[idx + 1] if idx + 1 < len(parts) else "General Materials"
    elif "my_work" in lower_parts or "my work" in lower_parts:
        section = "My_Work"
        idx = lower_parts.index("my_work") if "my_work" in lower_parts else lower_parts.index("my work")
        category = parts[idx + 1] if idx + 1 < len(parts) else "My Work"
    elif "review" in lower_parts:
        section = "Review"
        idx = lower_parts.index("review")
        category = parts[idx + 1] if idx + 1 < len(parts) else "Review"
    return section, category, source


def extract_week(parts: list[str]) -> tuple[str, int | None]:
    for part in parts:
        m = WEEK_RE.match(part)
        if m:
            return part, int(m.group("num"))
    return "", None


def parse_course_dir(path: Path) -> tuple[str, str]:
    m = COURSE_RE.match(path.name)
    if m:
        return m.group("code"), m.group("name")
    return path.name, path.name


def guess_is_official(rel: str, source: str) -> int:
    lower = rel.lower()
    if "my_work" in lower or "my work" in lower:
        return 0
    if "ai explanation" in source.lower() or "my work" in source.lower():
        return 0
    return 1


def is_solution_file(path: Path, category: str) -> int:
    return 1 if SOLUTION_RE.search(f"{path.name} {category}") else 0


def is_question_source_file(path: Path, section: str, category: str) -> int:
    haystack = f"{section} {category} {path.name}"
    return 1 if QUESTION_CATEGORY_RE.search(haystack) and not is_solution_file(path, category) else 0


def source_type_for(source_label: str) -> str:
    if "official" in source_label.lower() or "canvas" in source_label.lower():
        return "Canvas"
    if "my work" in source_label.lower():
        return "My Work"
    return source_label or "Local"


def is_suspicious_file(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.stat().st_size == 0:
        return "zero-byte file"
    ext = path.suffix.lower()
    try:
        head = path.open("rb").read(512).lower()
    except OSError as exc:
        return f"unreadable: {exc}"
    if ext == ".pdf" and not head.startswith(b"%pdf"):
        if b"<html" in head or b"login" in head:
            return "PDF extension but looks like HTML/login page"
        return "PDF extension but missing PDF header"
    if ext in {".docx", ".pptx", ".xlsx"}:
        if not zipfile.is_zipfile(path):
            return f"{ext} extension but not a valid OOXML zip"
    return ""


def run_text_command(args: list[str], timeout: int = 20) -> str:
    try:
        res = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except Exception:
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout


def extract_text_from_pdf(path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        return run_text_command([pdftotext, "-layout", str(path), "-"], timeout=30)[:200_000]
    return ""


def pdf_page_count(path: Path) -> int:
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return 0
    out = run_text_command([pdfinfo, str(path)], timeout=10)
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else 0


def extract_pdf_page_text(path: Path, page: int) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return ""
    return run_text_command([pdftotext, "-layout", "-f", str(page), "-l", str(page), str(path), "-"], timeout=15)


def xml_text_from_zip(path: Path, prefixes: tuple[str, ...]) -> str:
    if not zipfile.is_zipfile(path):
        return ""
    out: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".xml") or not name.startswith(prefixes):
                continue
            try:
                root = ElementTree.fromstring(zf.read(name))
            except Exception:
                continue
            for node in root.iter():
                if node.text and node.text.strip():
                    out.append(node.text.strip())
    return "\n".join(out)[:200_000]


def xml_text_items_from_zip(path: Path, prefix: str) -> list[tuple[str, str]]:
    if not zipfile.is_zipfile(path):
        return []
    items: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(name for name in zf.namelist() if name.endswith(".xml") and name.startswith(prefix))
        for name in names:
            parts: list[str] = []
            try:
                root = ElementTree.fromstring(zf.read(name))
            except Exception:
                continue
            for node in root.iter():
                if node.text and node.text.strip():
                    parts.append(node.text.strip())
            if parts:
                items.append((name, "\n".join(parts)))
    return items


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return extract_text_from_pdf(path)
        if ext == ".docx":
            return xml_text_from_zip(path, ("word/",))
        if ext == ".pptx":
            return xml_text_from_zip(path, ("ppt/slides/", "ppt/notesSlides/"))
        if ext in {".csv", ".tsv", ".txt", ".md", ".py", ".r", ".html", ".htm", ".xhtml", ".xml", ".svg"}:
            return path.read_text(encoding="utf-8", errors="ignore")[:200_000]
        if ext == ".ipynb":
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            chunks = []
            for cell in data.get("cells", []):
                chunks.append("".join(cell.get("source", [])))
            return "\n".join(chunks)[:200_000]
    except Exception:
        return ""
    return ""


def chunk_plain_text(text: str, source_location: str = "", page_start: int | None = None, slide_start: int | None = None) -> list[dict[str, Any]]:
    clean = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not clean:
        return []
    chunks: list[dict[str, Any]] = []
    cursor = 0
    idx = 0
    while cursor < len(clean):
        piece = clean[cursor : cursor + CHUNK_TARGET_CHARS].strip()
        if piece:
            chunks.append(
                {
                    "chunk_index": idx,
                    "text": piece,
                    "source_location": source_location,
                    "page_start": page_start,
                    "page_end": page_start,
                    "slide_start": slide_start,
                    "slide_end": slide_start,
                    "heading": "",
                    "token_estimate": max(1, len(piece) // 4),
                }
            )
            idx += 1
        cursor += CHUNK_TARGET_CHARS
    return chunks


def extract_document_chunks(path: Path, fallback_text: str) -> list[dict[str, Any]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages = pdf_page_count(path)
        if pages:
            out: list[dict[str, Any]] = []
            for page in range(1, pages + 1):
                page_text = extract_pdf_page_text(path, page)
                out.extend(chunk_plain_text(page_text, f"p.{page}", page_start=page))
            for i, chunk in enumerate(out):
                chunk["chunk_index"] = i
            return out
    if ext == ".pptx":
        out = []
        slide_items = xml_text_items_from_zip(path, "ppt/slides/")
        for idx, (_name, text) in enumerate(slide_items, start=1):
            chunks = chunk_plain_text(text, f"Slide {idx}", slide_start=idx)
            out.extend(chunks)
        for i, chunk in enumerate(out):
            chunk["chunk_index"] = i
        return out
    if ext == ".docx":
        items = xml_text_items_from_zip(path, "word/")
        text = "\n\n".join(item_text for _name, item_text in items)
        return chunk_plain_text(text or fallback_text, "document")
    if ext == ".ipynb":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            out = []
            for idx, cell in enumerate(data.get("cells", []), start=1):
                text = "".join(cell.get("source", []))
                out.extend(chunk_plain_text(text, f"Notebook cell {idx}"))
            for i, chunk in enumerate(out):
                chunk["chunk_index"] = i
            return out
        except Exception:
            pass
    return chunk_plain_text(fallback_text, "file")


def detect_questions_from_chunks(file_row: sqlite3.Row | dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for chunk in chunks:
        lines = chunk["text"].splitlines()
        for idx, line in enumerate(lines):
            m = QUESTION_RE.match(line.strip())
            if not m:
                continue
            body_lines = [m.group("body").strip()]
            for follow in lines[idx + 1 :]:
                stripped = follow.strip()
                if not stripped:
                    continue
                if QUESTION_RE.match(stripped) or re.match(r"^(Source note|Note|Solution|Answer)\b", stripped, re.IGNORECASE):
                    break
                body_lines.append(stripped)
            questions.append(
                {
                    "file_id": file_row["id"],
                    "course_code": file_row["course_code"],
                    "week_label": file_row["week_label"],
                    "exercise_type": file_row["exercise_type"] or file_row["category"],
                    "question_number": f"Q{m.group('num')}",
                    "question_text": "\n".join(body_lines).strip()[:5000],
                    "source_location": chunk["source_location"] or file_row["filename"],
                    "official_source": 1 if file_row["is_official"] else 0,
                    "chunk_index": chunk["chunk_index"],
                }
            )
    return questions


def connect_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL,
          name TEXT NOT NULL,
          folder_name TEXT NOT NULL UNIQUE,
          path TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS weeks (
          id INTEGER PRIMARY KEY,
          course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          week_label TEXT NOT NULL,
          week_number INTEGER,
          path TEXT NOT NULL,
          has_materials INTEGER NOT NULL DEFAULT 0,
          file_count INTEGER NOT NULL DEFAULT 0,
          UNIQUE(course_id, week_label)
        );
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY,
          course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          week_id INTEGER REFERENCES weeks(id) ON DELETE SET NULL,
          course_code TEXT NOT NULL,
          week_label TEXT,
          section TEXT,
          category TEXT,
          exercise_type TEXT,
          filename TEXT NOT NULL,
          original_path TEXT NOT NULL UNIQUE,
          rel_path TEXT NOT NULL,
          source TEXT NOT NULL,
          source_label TEXT NOT NULL,
          hash TEXT NOT NULL,
          size INTEGER NOT NULL,
          modified_at TEXT NOT NULL,
          indexed_at TEXT NOT NULL,
          extension TEXT,
          mime_type TEXT,
          is_official INTEGER NOT NULL DEFAULT 1,
          suspicious TEXT DEFAULT '',
          text_cache_path TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS file_versions (
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
          stable_id TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          file_size INTEGER NOT NULL,
          modified_at TEXT NOT NULL,
          indexed_at TEXT NOT NULL,
          text_cache_path TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(file_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS document_chunks (
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
          version_id INTEGER REFERENCES file_versions(id) ON DELETE CASCADE,
          chunk_index INTEGER NOT NULL,
          course_code TEXT NOT NULL,
          week_label TEXT,
          category TEXT,
          exercise_type TEXT,
          filename TEXT NOT NULL,
          source_location TEXT,
          page_start INTEGER,
          page_end INTEGER,
          slide_start INTEGER,
          slide_end INTEGER,
          heading TEXT,
          text TEXT NOT NULL,
          token_estimate INTEGER DEFAULT 0,
          created_at TEXT NOT NULL,
          UNIQUE(file_id, chunk_index)
        );
        CREATE TABLE IF NOT EXISTS questions (
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
          chunk_id INTEGER REFERENCES document_chunks(id) ON DELETE SET NULL,
          course_code TEXT NOT NULL,
          week_label TEXT,
          exercise_type TEXT,
          question_number TEXT,
          question_text TEXT NOT NULL,
          source_location TEXT NOT NULL,
          official_source INTEGER NOT NULL DEFAULT 1,
          solution_id INTEGER,
          created_at TEXT NOT NULL,
          UNIQUE(file_id, question_number, source_location)
        );
        CREATE TABLE IF NOT EXISTS solutions (
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
          course_code TEXT NOT NULL,
          week_label TEXT,
          exercise_type TEXT,
          solution_label TEXT,
          source_location TEXT NOT NULL,
          official_source INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id INTEGER,
          course_id INTEGER,
          week_label TEXT,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stars (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(target_type, target_id)
        );
        CREATE TABLE IF NOT EXISTS attempts (
          id INTEGER PRIMARY KEY,
          file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
          prompt TEXT,
          answer_text TEXT,
          result TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wrong_questions (
          id INTEGER PRIMARY KEY,
          course_id INTEGER,
          week_label TEXT,
          type TEXT,
          source_file_id INTEGER,
          question_ref TEXT,
          mistake TEXT,
          concept TEXT,
          attempts INTEGER DEFAULT 0,
          last_reviewed TEXT,
          mastery TEXT DEFAULT 'new',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bookmarks (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id INTEGER NOT NULL,
          label TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(target_type, target_id)
        );
        CREATE TABLE IF NOT EXISTS study_sessions (
          id INTEGER PRIMARY KEY,
          course_id INTEGER,
          week_label TEXT,
          mode TEXT,
          started_at TEXT NOT NULL,
          ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ai_interactions (
          id INTEGER PRIMARY KEY,
          context_json TEXT NOT NULL,
          prompt TEXT NOT NULL,
          response TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_events (
          id INTEGER PRIMARY KEY,
          event_type TEXT NOT NULL,
          detail TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_index_state (
          id INTEGER PRIMARY KEY,
          file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
          stable_id TEXT,
          sha256 TEXT,
          provider TEXT NOT NULL DEFAULT 'local',
          vector_store_id TEXT,
          provider_file_id TEXT,
          status TEXT NOT NULL,
          error TEXT DEFAULT '',
          last_synced_at TEXT,
          metadata_json TEXT DEFAULT '{}',
          UNIQUE(file_id, provider)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
          filename,
          course_code,
          week_label,
          category,
          source_label,
          extracted_text,
          tokenize='unicode61'
        );
        """
    )
    ensure_columns(
        conn,
        "files",
        {
            "stable_id": "TEXT DEFAULT ''",
            "course_name": "TEXT DEFAULT ''",
            "week_number": "INTEGER",
            "absolute_path": "TEXT DEFAULT ''",
            "source_type": "TEXT DEFAULT 'Canvas'",
            "file_extension": "TEXT DEFAULT ''",
            "file_size": "INTEGER DEFAULT 0",
            "sha256": "TEXT DEFAULT ''",
            "is_solution": "INTEGER NOT NULL DEFAULT 0",
            "is_question_source": "INTEGER NOT NULL DEFAULT 0",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "missing_at": "TEXT",
            "ai_index_status": "TEXT DEFAULT 'not_indexed'",
            "ai_index_error": "TEXT DEFAULT ''",
        },
    )
    conn.execute("UPDATE files SET stable_id=COALESCE(NULLIF(stable_id, ''), hash) WHERE stable_id='' OR stable_id IS NULL")
    conn.execute("UPDATE files SET absolute_path=original_path WHERE absolute_path='' OR absolute_path IS NULL")
    conn.execute("UPDATE files SET file_extension=extension WHERE file_extension='' OR file_extension IS NULL")
    conn.execute("UPDATE files SET file_size=size WHERE file_size=0 OR file_size IS NULL")
    conn.execute("UPDATE files SET sha256=hash WHERE sha256='' OR sha256 IS NULL")
    conn.commit()


@dataclass
class ScanStats:
    courses: int = 0
    files: int = 0
    new_files: int = 0
    updated_files: int = 0
    unchanged_files: int = 0
    removed_files: int = 0
    failed_files: int = 0
    suspicious: int = 0
    indexed_text: int = 0
    ai_indexed: int = 0
    ai_index_failed: int = 0
    questions_detected: int = 0
    solutions_detected: int = 0


def source_index_map(study_root: Path) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    p = study_root / "Source Index.csv"
    if not p.exists():
        return mapping
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                local = row.get("Local Path", "").strip()
                if local:
                    mapping[local] = row
    except Exception:
        return mapping
    return mapping


def upsert_file_version(conn: sqlite3.Connection, file_id: int, stable_id: str, digest: str, size: int, modified: str, text_cache: str) -> int:
    conn.execute(
        """
        INSERT INTO file_versions(file_id, stable_id, sha256, file_size, modified_at, indexed_at, text_cache_path, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(file_id, sha256) DO UPDATE SET active=1, indexed_at=excluded.indexed_at, text_cache_path=excluded.text_cache_path
        """,
        (file_id, stable_id, digest, size, modified, now_iso(), text_cache),
    )
    conn.execute("UPDATE file_versions SET active=0 WHERE file_id=? AND sha256!=?", (file_id, digest))
    return int(conn.execute("SELECT id FROM file_versions WHERE file_id=? AND sha256=?", (file_id, digest)).fetchone()["id"])


def rebuild_file_index(conn: sqlite3.Connection, file_id: int, text: str, chunks: list[dict[str, Any]], file_row: sqlite3.Row) -> tuple[int, int, int]:
    conn.execute("DELETE FROM files_fts WHERE rowid=?", (file_id,))
    conn.execute(
        "INSERT INTO files_fts(rowid, filename, course_code, week_label, category, source_label, extracted_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, file_row["filename"], file_row["course_code"], file_row["week_label"], file_row["category"], file_row["source_label"], text),
    )
    version = conn.execute("SELECT id FROM file_versions WHERE file_id=? AND active=1 ORDER BY id DESC LIMIT 1", (file_id,)).fetchone()
    version_id = int(version["id"]) if version else None
    conn.execute("DELETE FROM document_chunks WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM questions WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM solutions WHERE file_id=?", (file_id,))
    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO document_chunks(
              file_id, version_id, chunk_index, course_code, week_label, category, exercise_type,
              filename, source_location, page_start, page_end, slide_start, slide_end, heading, text, token_estimate, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                version_id,
                chunk["chunk_index"],
                file_row["course_code"],
                file_row["week_label"],
                file_row["category"],
                file_row["exercise_type"],
                file_row["filename"],
                chunk["source_location"],
                chunk["page_start"],
                chunk["page_end"],
                chunk["slide_start"],
                chunk["slide_end"],
                chunk["heading"],
                chunk["text"],
                chunk["token_estimate"],
                now_iso(),
            ),
        )
    question_count = 0
    if file_row["is_question_source"] and file_row["is_official"]:
        chunk_rows = conn.execute("SELECT id, chunk_index FROM document_chunks WHERE file_id=?", (file_id,)).fetchall()
        chunk_id_by_index = {row["chunk_index"]: row["id"] for row in chunk_rows}
        for q in detect_questions_from_chunks(file_row, chunks):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO questions(
                  file_id, chunk_id, course_code, week_label, exercise_type, question_number,
                  question_text, source_location, official_source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    chunk_id_by_index.get(q["chunk_index"]),
                    q["course_code"],
                    q["week_label"],
                    q["exercise_type"],
                    q["question_number"],
                    q["question_text"],
                    q["source_location"],
                    q["official_source"],
                    now_iso(),
                ),
            )
            question_count += max(cur.rowcount, 0)
    solution_count = 0
    if file_row["is_solution"] and file_row["is_official"]:
        conn.execute(
            """
            INSERT INTO solutions(file_id, course_code, week_label, exercise_type, solution_label, source_location, official_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                file_id,
                file_row["course_code"],
                file_row["week_label"],
                file_row["exercise_type"] or file_row["category"],
                file_row["filename"],
                file_row["rel_path"],
                now_iso(),
            ),
        )
        solution_count = 1
    status = "indexed" if file_row["is_official"] and not file_row["suspicious"] and text else "not_indexed"
    error = "" if status == "indexed" else ("No extracted text" if not text else file_row["suspicious"])
    conn.execute(
        """
        INSERT INTO ai_index_state(file_id, stable_id, sha256, provider, status, error, last_synced_at, metadata_json)
        VALUES (?, ?, ?, 'local', ?, ?, ?, ?)
        ON CONFLICT(file_id, provider) DO UPDATE SET
          stable_id=excluded.stable_id, sha256=excluded.sha256, status=excluded.status,
          error=excluded.error, last_synced_at=excluded.last_synced_at, metadata_json=excluded.metadata_json
        """,
        (
            file_id,
            file_row["stable_id"],
            file_row["sha256"],
            status,
            error,
            now_iso(),
            json.dumps(file_metadata(file_row), ensure_ascii=False),
        ),
    )
    conn.execute("UPDATE files SET ai_index_status=?, ai_index_error=? WHERE id=?", (status, error, file_id))
    return len(chunks), question_count, solution_count


def file_metadata(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_id": row["stable_id"],
        "course_code": row["course_code"],
        "week": row["week_number"],
        "week_label": row["week_label"],
        "category": row["category"],
        "exercise_type": row["exercise_type"],
        "filename": row["filename"],
        "source": row["source_type"],
        "is_official": bool(row["is_official"]),
        "is_solution": bool(row["is_solution"]),
    }


def seed_demo_records(conn: sqlite3.Connection) -> None:
    """Add small synthetic records so demo-only views are not empty."""
    if not DEMO_MODE:
        return
    existing = conn.execute("SELECT COUNT(*) AS c FROM wrong_questions").fetchone()["c"]
    if existing:
        return
    course = conn.execute("SELECT id FROM courses WHERE code='TEST1001'").fetchone()
    source = conn.execute("SELECT id FROM files WHERE filename='Tutorial 01 Questions.txt'").fetchone()
    if not course:
        return
    conn.execute(
        """
        INSERT INTO wrong_questions(course_id, week_label, type, source_file_id, question_ref, mistake, concept, attempts, mastery, created_at)
        VALUES (?, 'Week 01', 'Tutorial', ?, 'Q1', 'Mixed up opportunity cost with total money spent.', 'Opportunity cost', 1, 'new', ?)
        """,
        (course["id"], source["id"] if source else None, now_iso()),
    )


def scan_library(study_root: Path = DEFAULT_STUDY_ROOT) -> ScanStats:
    study_root = study_root.expanduser().resolve()
    if not study_root.exists():
        raise FileNotFoundError(f"Study library not found: {study_root}")
    ensure_dirs()
    conn = connect_db()
    init_db(conn)
    stats = ScanStats()
    src_index = source_index_map(study_root)
    indexed_paths: set[str] = set()
    for course_dir in sorted([p for p in study_root.iterdir() if p.is_dir()]):
        if course_dir.name.startswith(".") or course_dir.name == "Needs Review":
            continue
        code, name = parse_course_dir(course_dir)
        now = now_iso()
        cur = conn.execute(
            """
            INSERT INTO courses(code, name, folder_name, path, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(folder_name) DO UPDATE SET
              code=excluded.code, name=excluded.name, path=excluded.path, updated_at=excluded.updated_at
            RETURNING id
            """,
            (code, name, course_dir.name, str(course_dir), now, now),
        )
        course_id = int(cur.fetchone()["id"])
        stats.courses += 1
        week_id_by_label: dict[str, int] = {}
        for week_num in range(1, 13):
            label = f"Week {week_num:02d}"
            week_path = course_dir / label
            cur = conn.execute(
                """
                INSERT INTO weeks(course_id, week_label, week_number, path)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(course_id, week_label) DO UPDATE SET path=excluded.path
                RETURNING id
                """,
                (course_id, label, week_num, str(week_path)),
            )
            week_id_by_label[label] = int(cur.fetchone()["id"])
        for path in sorted(course_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.name.startswith("~$") or path.name.startswith(".~"):
                continue
            if path.name.lower() in INTERNAL_FILENAMES:
                continue
            if "Needs Review" in path.parts:
                continue
            ext = path.suffix.lower()
            if ext not in ACADEMIC_EXTS:
                continue
            rel = safe_relative(path, study_root)
            indexed_paths.add(str(path.resolve()))
            rel_parts = list(Path(rel).parts)
            week_label, week_number = extract_week(rel_parts)
            week_id = week_id_by_label.get(week_label)
            section, category, source_label = human_category(rel_parts)
            source_row = src_index.get(rel, {})
            if source_row.get("Original or Saved Page"):
                source_label = "Official Canvas Material" if "official" in source_row.get("Original or Saved Page", "").lower() else source_label
            suspicious = is_suspicious_file(path)
            if suspicious:
                stats.suspicious += 1
                stats.failed_files += 1
            digest = sha256_file(path)
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            source = "official" if guess_is_official(rel, source_label) else "user"
            exercise_type = category if section == "02 Exercises" else ""
            solution_flag = is_solution_file(path, category)
            question_source_flag = is_question_source_file(path, section, category)
            stable_row = conn.execute("SELECT stable_id FROM files WHERE hash=? OR sha256=? ORDER BY id LIMIT 1", (digest, digest)).fetchone()
            stable_id = stable_row["stable_id"] if stable_row and stable_row["stable_id"] else digest
            existing = conn.execute("SELECT * FROM files WHERE original_path=?", (str(path.resolve()),)).fetchone()
            needs_reindex = True
            text_cache = ""
            text = ""
            chunks: list[dict[str, Any]] = []
            if existing:
                text_cache = existing["text_cache_path"] or ""
                chunk_count = conn.execute("SELECT COUNT(*) AS c FROM document_chunks WHERE file_id=?", (existing["id"],)).fetchone()["c"]
                ai_seen = conn.execute("SELECT id FROM ai_index_state WHERE file_id=? AND provider='local'", (existing["id"],)).fetchone()
                same_file = existing["sha256"] == digest and existing["modified_at"] == modified
                needs_reindex = not same_file or (chunk_count == 0 and ai_seen is None)
                if needs_reindex:
                    stats.updated_files += 1
                else:
                    stats.unchanged_files += 1
            else:
                stats.new_files += 1
            if needs_reindex and not suspicious:
                text = extract_text(path)
                if text:
                    text_cache_path = TEXT_CACHE_DIR / f"{digest}.txt"
                    text_cache_path.write_text(text, encoding="utf-8", errors="ignore")
                    text_cache = str(text_cache_path)
                    chunks = extract_document_chunks(path, text)
                    stats.indexed_text += 1
            elif text_cache and Path(text_cache).exists():
                text = Path(text_cache).read_text(encoding="utf-8", errors="ignore")
            conn.execute(
                """
                INSERT INTO files(
                  course_id, week_id, course_code, week_label, section, category, exercise_type,
                  filename, original_path, rel_path, source, source_label, hash, size, modified_at,
                  indexed_at, extension, mime_type, is_official, suspicious, text_cache_path,
                  stable_id, course_name, week_number, absolute_path, source_type, file_extension,
                  file_size, sha256, is_solution, is_question_source, active, missing_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(original_path) DO UPDATE SET
                  course_id=excluded.course_id, week_id=excluded.week_id, course_code=excluded.course_code,
                  week_label=excluded.week_label, section=excluded.section, category=excluded.category,
                  exercise_type=excluded.exercise_type, filename=excluded.filename, rel_path=excluded.rel_path,
                  source=excluded.source, source_label=excluded.source_label, hash=excluded.hash,
                  size=excluded.size, modified_at=excluded.modified_at, indexed_at=excluded.indexed_at,
                  extension=excluded.extension, mime_type=excluded.mime_type, is_official=excluded.is_official,
                  suspicious=excluded.suspicious, text_cache_path=excluded.text_cache_path,
                  stable_id=excluded.stable_id, course_name=excluded.course_name, week_number=excluded.week_number,
                  absolute_path=excluded.absolute_path, source_type=excluded.source_type, file_extension=excluded.file_extension,
                  file_size=excluded.file_size, sha256=excluded.sha256, is_solution=excluded.is_solution,
                  is_question_source=excluded.is_question_source, active=1, missing_at=NULL
                """,
                (
                    course_id,
                    week_id,
                    code,
                    week_label,
                    section,
                    category,
                    exercise_type,
                    path.name,
                    str(path.resolve()),
                    rel,
                    source,
                    source_label,
                    digest,
                    stat.st_size,
                    modified,
                    now_iso(),
                    ext,
                    mime,
                    1 if source == "official" else 0,
                    suspicious,
                    text_cache,
                    stable_id,
                    name,
                    week_number,
                    str(path.resolve()),
                    source_type_for(source_label),
                    ext,
                    stat.st_size,
                    digest,
                    solution_flag,
                    question_source_flag,
                    1,
                    None,
                ),
            )
            file_id = conn.execute("SELECT id FROM files WHERE original_path=?", (str(path.resolve()),)).fetchone()["id"]
            upsert_file_version(conn, file_id, stable_id, digest, stat.st_size, modified, text_cache)
            file_row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            if needs_reindex:
                chunk_count, question_count, solution_count = rebuild_file_index(conn, file_id, text, chunks, file_row)
                stats.questions_detected += question_count
                stats.solutions_detected += solution_count
                ai_row = conn.execute("SELECT status FROM ai_index_state WHERE file_id=? AND provider='local'", (file_id,)).fetchone()
                if ai_row and ai_row["status"] == "indexed":
                    stats.ai_indexed += 1
                else:
                    stats.ai_index_failed += 1
            stats.files += 1
    # Mark missing files rather than deleting history.
    all_rows = conn.execute("SELECT id, original_path FROM files").fetchall()
    for row in all_rows:
        if row["original_path"] not in indexed_paths:
            conn.execute("DELETE FROM files_fts WHERE rowid=?", (row["id"],))
            conn.execute("UPDATE files SET active=0, missing_at=COALESCE(missing_at, ?) WHERE id=?", (now_iso(), row["id"]))
            conn.execute("UPDATE file_versions SET active=0 WHERE file_id=?", (row["id"],))
            conn.execute("UPDATE ai_index_state SET status='missing', error='Original file missing', last_synced_at=? WHERE file_id=?", (now_iso(), row["id"]))
            stats.removed_files += 1
    conn.execute("UPDATE weeks SET has_materials=0, file_count=0")
    conn.execute(
        """
        UPDATE weeks SET
          file_count=(SELECT COUNT(*) FROM files WHERE files.week_id=weeks.id AND files.active=1),
          has_materials=CASE WHEN (SELECT COUNT(*) FROM files WHERE files.week_id=weeks.id AND files.active=1) > 0 THEN 1 ELSE 0 END
        """
    )
    conn.execute(
        "INSERT INTO sync_events(event_type, detail, created_at) VALUES (?, ?, ?)",
        ("scan", json.dumps(stats.__dict__), now_iso()),
    )
    seed_demo_records(conn)
    conn.commit()
    conn.close()
    log_event("scan", json.dumps({"files": stats.files, "root": "configured-study-library"}, ensure_ascii=False))
    return stats


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def fts_query(raw: str) -> str:
    tokens = re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", raw, flags=re.UNICODE)
    return " ".join(tokens[:12])


def get_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise FileNotFoundError("file not found")
    path = Path(row["original_path"]).expanduser().resolve()
    if not is_inside(path, DEFAULT_STUDY_ROOT):
        raise PermissionError("file outside study root")
    if not path.exists():
        raise FileNotFoundError("file not found")
    return row


class MultipartUpload:
    def __init__(self, content_type: str, body: bytes):
        m = re.search(r"boundary=(.+)", content_type)
        if not m:
            raise ValueError("Missing multipart boundary")
        boundary = m.group(1).strip().strip('"').encode()
        self.fields: dict[str, str] = {}
        self.files: list[tuple[str, str, bytes]] = []
        for part in body.split(b"--" + boundary):
            part = part.strip(b"\r\n")
            if not part or part == b"--":
                continue
            headers_raw, _, data = part.partition(b"\r\n\r\n")
            headers = headers_raw.decode("utf-8", errors="ignore")
            disp = next((line for line in headers.splitlines() if line.lower().startswith("content-disposition:")), "")
            name_m = re.search(r'name="([^"]+)"', disp)
            filename_m = re.search(r'filename="([^"]*)"', disp)
            if not name_m:
                continue
            name = name_m.group(1)
            if data.endswith(b"\r\n"):
                data = data[:-2]
            if filename_m and filename_m.group(1):
                self.files.append((name, filename_m.group(1), data))
            else:
                self.fields[name] = data.decode("utf-8", errors="ignore")


def safe_filename(filename: str) -> str:
    return validate_path_component(filename)


def unique_path_for_upload(target_dir: Path, filename: str, digest: str) -> tuple[Path, bool]:
    target_dir.mkdir(parents=True, exist_ok=True)
    if not is_inside(target_dir, DEFAULT_STUDY_ROOT):
        raise PermissionError("Upload target outside study root")
    candidate = safe_child_path(target_dir, safe_filename(filename))
    if candidate.exists():
        try:
            if sha256_file(candidate) == digest:
                return candidate, True
        except OSError:
            pass
        stem = candidate.stem
        suffix = candidate.suffix
        candidate = safe_child_path(target_dir, f"{stem} - {datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}")
    return candidate, False


def api_health(conn: sqlite3.Connection) -> dict[str, Any]:
    file_count = conn.execute("SELECT COUNT(*) AS c FROM files WHERE active=1").fetchone()["c"]
    suspicious = conn.execute("SELECT COUNT(*) AS c FROM files WHERE active=1 AND suspicious != ''").fetchone()["c"]
    ai_indexed = conn.execute("SELECT COUNT(*) AS c FROM ai_index_state WHERE provider='local' AND status='indexed'").fetchone()["c"]
    questions = conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    chunks = conn.execute("SELECT COUNT(*) AS c FROM document_chunks").fetchone()["c"]
    last_scan = conn.execute("SELECT created_at FROM sync_events WHERE event_type='scan' ORDER BY id DESC LIMIT 1").fetchone()
    last_ai = conn.execute("SELECT MAX(last_synced_at) AS ts FROM ai_index_state").fetchone()
    vector_store_id = current_vector_store_id(conn)
    return {
        "app": APP_NAME,
        "demoMode": DEMO_MODE,
        "studyLibraryConnected": DEFAULT_STUDY_ROOT.exists(),
        "database": "Healthy",
        "filesIndexed": file_count,
        "suspiciousFiles": suspicious,
        "chunksIndexed": chunks,
        "questionsIndexed": questions,
        "aiIndexedFiles": ai_indexed,
        "vectorStore": "Configured" if vector_store_id else "Not configured",
        "vectorStoreLabel": "Configured" if vector_store_id else "Not configured",
        "lastScan": last_scan["created_at"] if last_scan else None,
        "lastAISync": last_ai["ts"] if last_ai else None,
        "openAI": "Configured" if bool(os.environ.get("OPENAI_API_KEY")) else "Not configured",
        "canvas": "Handled separately by an authenticated user-approved importer workflow",
    }


def api_session() -> dict[str, Any]:
    return {"csrfToken": CSRF_TOKEN}


def build_context_pack(conn: sqlite3.Connection, file_id: int) -> dict[str, Any]:
    row = get_file(conn, file_id)
    text = ""
    text = read_cached_text(row, 4000)
    return {
        "course": row["course_code"],
        "week": row["week_label"],
        "weekNumber": row["week_number"],
        "section": row["section"],
        "category": row["category"],
        "exerciseType": row["exercise_type"],
        "selectedFile": row["filename"],
        "source": row["source_label"],
        "path": row["rel_path"],
        "stableId": row["stable_id"],
        "sha256": row["sha256"],
        "relevantExtractedMaterial": text,
        "sources": sources_for_file(conn, file_id),
        "note": "Extracted text is secondary. The original file remains the authoritative source.",
    }


def sources_for_file(conn: sqlite3.Connection, file_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, filename, course_code, week_label, category, exercise_type, source_location, page_start, page_end, slide_start, slide_end
        FROM document_chunks WHERE file_id=? ORDER BY chunk_index LIMIT 8
        """,
        (file_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def search_local_context(conn: sqlite3.Connection, prompt: str, context: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    query = fts_query(prompt) or fts_query(" ".join(str(v) for v in context.values()))
    params: list[Any] = []
    sql = """
    SELECT dc.*, f.id AS source_file_id, f.rel_path, f.is_official
    FROM document_chunks dc
    JOIN files f ON f.id=dc.file_id
    WHERE f.active=1 AND f.is_official=1
    """
    if context.get("course"):
        sql += " AND f.course_code=?"
        params.append(context["course"])
    if context.get("week"):
        sql += " AND f.week_label=?"
        params.append(context["week"])
    if context.get("fileId"):
        sql += " AND f.id=?"
        params.append(int(context["fileId"]))
    if query:
        words = [w.lower() for w in query.split()]
        rows = conn.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            text = f"{row['filename']} {row['category']} {row['exercise_type']} {row['text']}".lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [dict(row) for _score, row in scored[:limit]]
    sql += " ORDER BY dc.id DESC LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def detect_question_number(*values: Any) -> str:
    for value in values:
        text = str(value or "")
        match = ASK_QUESTION_RE.search(text)
        if not match:
            continue
        number = match.group("num") or match.group("cnum")
        if number:
            return f"Q{number.upper()}"
    return ""


def normalize_ask_context(conn: sqlite3.Connection, raw_context: dict[str, Any], prompt: str) -> dict[str, Any]:
    context = dict(raw_context or {})
    if context.get("fileId"):
        try:
            file_row = get_file(conn, int(context["fileId"]))
            context.setdefault("course", file_row["course_code"])
            context.setdefault("week", file_row["week_label"])
            context.setdefault("weekNumber", file_row["week_number"])
            context.setdefault("section", file_row["section"])
            context.setdefault("category", file_row["category"])
            context.setdefault("materialType", file_row["category"])
            context.setdefault("exerciseType", file_row["exercise_type"] or file_row["category"])
            context.setdefault("file", file_row["filename"])
            context.setdefault("stableId", file_row["stable_id"])
        except Exception:
            context.pop("fileId", None)
    if context.get("week"):
        context["week"] = normalize_week(str(context["week"]))
    if context.get("course"):
        context["course"] = normalize_course(str(context["course"]))
    qn = detect_question_number(context.get("questionNumber"), prompt)
    if qn:
        context["questionNumber"] = qn
    return context


def teacher_questions_for_context(conn: sqlite3.Connection, context: dict[str, Any], prompt: str, limit: int = 5) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = """
    SELECT q.*, f.filename, f.rel_path, f.text_cache_path, f.stable_id, f.sha256
    FROM questions q JOIN files f ON f.id=q.file_id
    WHERE f.active=1 AND q.official_source=1
    """
    if context.get("questionId"):
        sql += " AND q.id=?"
        params.append(int(context["questionId"]))
    if context.get("fileId"):
        sql += " AND q.file_id=?"
        params.append(int(context["fileId"]))
    if context.get("course"):
        sql += " AND q.course_code=?"
        params.append(context["course"])
    if context.get("week"):
        sql += " AND q.week_label=?"
        params.append(context["week"])
    if context.get("exerciseType"):
        sql += " AND lower(q.exercise_type)=lower(?)"
        params.append(context["exerciseType"])
    qn = context.get("questionNumber") or detect_question_number(prompt)
    if qn:
        sql += " AND upper(q.question_number)=?"
        params.append(qn)
    sql += " ORDER BY q.course_code, q.week_label, q.exercise_type, q.question_number LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    questions = []
    for row in rows:
        source_text = read_cached_text(row, 200_000)
        original = extract_question_from_source_text(source_text, row["question_number"]) or row["question_text"]
        questions.append(
            {
                "id": row["id"],
                "course_code": row["course_code"],
                "week_label": row["week_label"],
                "exercise_type": row["exercise_type"],
                "question_number": row["question_number"],
                "question_text": original,
                "source_location": row["source_location"],
                "filename": row["filename"],
                "rel_path": row["rel_path"],
                "file_id": row["file_id"],
                "stable_id": row["stable_id"] or row["sha256"],
            }
        )
    return questions


def official_solutions_for_context(conn: sqlite3.Connection, context: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = """
    SELECT s.*, f.filename, f.rel_path, f.text_cache_path
    FROM solutions s JOIN files f ON f.id=s.file_id
    WHERE f.active=1 AND s.official_source=1
    """
    if context.get("course"):
        sql += " AND s.course_code=?"
        params.append(context["course"])
    if context.get("week"):
        sql += " AND s.week_label=?"
        params.append(context["week"])
    if context.get("exerciseType"):
        sql += " AND lower(s.exercise_type)=lower(?)"
        params.append(context["exerciseType"])
    sql += " ORDER BY s.course_code, s.week_label, s.exercise_type, s.solution_label LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def teacher_question_response(questions: list[dict[str, Any]]) -> str:
    if not questions:
        return "No suitable teacher-provided question was found in the indexed official course materials."
    lines = ["Teacher-provided question(s) found:"]
    for question in questions:
        lines.append(
            "\n".join(
                [
                    f"Course: {question['course_code']}",
                    f"Week: {question['week_label']}",
                    f"Type: {question['exercise_type']}",
                    f"File: {question['filename']}",
                    f"Question: {question['question_number']}",
                    question["question_text"],
                ]
            )
        )
    return "\n\n".join(lines)


def format_sources(chunks: list[dict[str, Any]]) -> str:
    lines = []
    for chunk in chunks:
        location = chunk.get("source_location") or ""
        if chunk.get("page_start"):
            location = f"p.{chunk['page_start']}" if chunk.get("page_start") == chunk.get("page_end") else f"p.{chunk['page_start']}-{chunk['page_end']}"
        if chunk.get("slide_start"):
            location = f"Slide {chunk['slide_start']}"
        lines.append(f"- {chunk['course_code']} {chunk.get('week_label') or ''} — {chunk['filename']} {location}".strip())
    return "\n".join(dict.fromkeys(lines))


def external_file_id(row: sqlite3.Row | dict[str, Any]) -> str:
    stable = (row["stable_id"] or row["sha256"] or row["hash"] or str(row["id"])).replace("-", "")
    return f"file_{stable[:24]}"


def normalize_course(value: str) -> str:
    return value.strip().upper()


def mcp_week(value: Any) -> str:
    if value is None or value == "":
        return ""
    return normalize_week(str(value))


def mcp_file_projection(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": external_file_id(row),
        "filename": row["filename"],
        "course": row["course_code"],
        "course_name": row["course_name"],
        "week": row["week_label"],
        "week_number": row["week_number"],
        "category": row["category"],
        "exercise_type": row["exercise_type"],
        "source": row["source_type"],
        "source_label": row["source_label"],
        "mime_type": row["mime_type"],
        "file_extension": row["file_extension"],
        "file_size": row["file_size"],
        "is_official": bool(row["is_official"]),
        "is_solution": bool(row["is_solution"]),
        "is_question_source": bool(row["is_question_source"]),
        "relative_path": row["rel_path"],
    }


def resolve_mcp_file(conn: sqlite3.Connection, file_id: str) -> sqlite3.Row:
    if not re.fullmatch(r"file_[A-Za-z0-9_-]{8,80}", file_id or ""):
        raise FileNotFoundError("NOT FOUND")
    prefix = file_id.removeprefix("file_")
    row = conn.execute(
        """
        SELECT * FROM files
        WHERE active=1 AND (stable_id LIKE ? OR sha256 LIKE ? OR hash LIKE ?)
        ORDER BY id LIMIT 1
        """,
        (f"{prefix}%", f"{prefix}%", f"{prefix}%"),
    ).fetchone()
    if row is None:
        raise FileNotFoundError("NOT FOUND")
    path = Path(row["original_path"]).expanduser().resolve()
    if not is_inside(path, DEFAULT_STUDY_ROOT):
        raise PermissionError("DENY")
    if not path.exists():
        raise FileNotFoundError("NOT FOUND")
    return row


def mcp_file_filters(args: dict[str, Any]) -> tuple[str, list[Any]]:
    params: list[Any] = []
    sql = " FROM files f WHERE f.active=1"
    course = normalize_course(str(args.get("course", "") or ""))
    week = mcp_week(args.get("week"))
    category = str(args.get("category", "") or "").strip()
    exercise_type = str(args.get("exercise_type", args.get("type", "")) or "").strip()
    filename = str(args.get("filename", "") or "").strip()
    if course:
        sql += " AND f.course_code LIKE ?"
        params.append(f"%{course}%")
    if week:
        sql += " AND f.week_label=?"
        params.append(week)
    if category:
        sql += " AND (lower(f.category)=lower(?) OR lower(f.section)=lower(?) OR lower(f.section) LIKE lower(?))"
        params.extend([category, category, f"%{category}%"])
    if exercise_type:
        sql += " AND (lower(f.exercise_type)=lower(?) OR lower(f.category)=lower(?) OR lower(f.section)=lower(?))"
        params.extend([exercise_type, exercise_type, exercise_type])
    if filename:
        sql += " AND f.filename LIKE ?"
        params.append(f"%{filename}%")
    return sql, params


def mcp_list_courses(_args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    rows = conn.execute(
        """
        SELECT c.code, c.name, c.folder_name, COUNT(f.id) AS file_count,
          MAX(w.week_number) AS latest_week
        FROM courses c
        LEFT JOIN files f ON f.course_id=c.id AND f.active=1
        LEFT JOIN weeks w ON w.course_id=c.id AND w.has_materials=1
        GROUP BY c.id ORDER BY c.code
        """
    ).fetchall()
    conn.close()
    return {"courses": rows_to_dicts(rows)}


def mcp_list_weeks(args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    course = normalize_course(str(args.get("course", "") or ""))
    if not course:
        raise ValueError("course is required")
    rows = conn.execute(
        """
        SELECT w.week_label, w.week_number, w.has_materials, w.file_count
        FROM weeks w JOIN courses c ON c.id=w.course_id
        WHERE c.code LIKE ? ORDER BY w.week_number
        """,
        (f"%{course}%",),
    ).fetchall()
    conn.close()
    return {"weeks": rows_to_dicts(rows)}


def mcp_list_files(args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    filter_sql, params = mcp_file_filters(args)
    rows = conn.execute(
        "SELECT f.*" + filter_sql + " ORDER BY f.course_code, f.week_label, f.category, f.filename LIMIT 100",
        params,
    ).fetchall()
    conn.close()
    return {"files": [mcp_file_projection(row) for row in rows]}


def mcp_search_files(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "") or args.get("topic", "") or "").strip()
    conn = connect_db()
    init_db(conn)
    filter_sql, params = mcp_file_filters(args)
    if query:
        filter_sql += " AND (f.filename LIKE ? OR f.rel_path LIKE ? OR f.category LIKE ? OR f.exercise_type LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"])
    rows = conn.execute("SELECT f.*" + filter_sql + " ORDER BY f.modified_at DESC LIMIT 80", params).fetchall()
    conn.close()
    return {"files": [mcp_file_projection(row) for row in rows]}


def mcp_search_content(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "") or args.get("topic", "") or "").strip()
    if not query:
        raise ValueError("query is required")
    conn = connect_db()
    init_db(conn)
    context = {"course": normalize_course(str(args.get("course", "") or "")), "week": mcp_week(args.get("week"))}
    chunks = search_local_context(conn, query, context, limit=int(args.get("limit", 10) or 10))
    file_rows: dict[int, sqlite3.Row] = {}
    file_ids = sorted({int(chunk["source_file_id"]) for chunk in chunks if chunk.get("source_file_id")})
    if file_ids:
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(f"SELECT * FROM files WHERE id IN ({placeholders}) AND active=1", file_ids).fetchall()
        file_rows = {int(row["id"]): row for row in rows}
    conn.close()
    return {
        "matches": [
            {
                "file_id": external_file_id(file_rows[int(chunk["source_file_id"])])
                if int(chunk["source_file_id"]) in file_rows
                else None,
                "source_file_id": chunk["source_file_id"],
                "course": chunk["course_code"],
                "week": chunk["week_label"],
                "filename": chunk["filename"],
                "source_location": chunk["source_location"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "slide_start": chunk["slide_start"],
                "slide_end": chunk["slide_end"],
                "excerpt": re.sub(r"\s+", " ", chunk["text"]).strip()[:1000],
            }
            for chunk in chunks
        ]
    }


def mcp_get_file_metadata(args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    row = resolve_mcp_file(conn, str(args.get("file_id", "")))
    out = {"file": mcp_file_projection(row)}
    conn.close()
    return out


def mcp_read_file(args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    row = resolve_mcp_file(conn, str(args.get("file_id", "")))
    limit = min(int(args.get("limit", 12000) or 12000), 40000)
    chunks = conn.execute(
        """
        SELECT source_location, page_start, page_end, slide_start, slide_end, heading, text
        FROM document_chunks WHERE file_id=? ORDER BY chunk_index LIMIT 40
        """,
        (row["id"],),
    ).fetchall()
    content_parts = []
    chunk_payload = []
    total = 0
    for chunk in chunks:
        text = chunk["text"] or ""
        if total < limit:
            piece = text[: max(0, limit - total)]
            content_parts.append(piece)
            total += len(piece)
        chunk_payload.append(dict(chunk) | {"text": text[:2500]})
    if not content_parts:
        cached = read_cached_text(row, limit)
        if cached:
            content_parts.append(cached)
    conn.close()
    return {
        "file": mcp_file_projection(row),
        "content": "\n\n".join(content_parts),
        "chunks": chunk_payload,
        "note": "Original file remains authoritative in the configured STUDY_LIBRARY_PATH.",
    }


def extract_question_from_source_text(text: str, question_number: str) -> str:
    num = question_number.upper().removeprefix("Q").strip()
    if not num:
        return ""
    marker = rf"(?:Q(?:uestion)?\.?\s*)?{re.escape(num)}[\).:]"
    next_marker = r"(?:Q(?:uestion)?\.?\s*)?(?:\d{1,3}|[A-Z])[\).:]"
    match = re.search(
        rf"(?ms)(?:^|\n)\s*\*?\s*{marker}\s*(?P<body>.*?)(?=(?:\n\s*\*?\s*{next_marker}\s+)|\Z)",
        text,
    )
    if not match:
        return ""
    body = match.group("body").strip()
    # Keep table-like line breaks from extracted academic files, but collapse runs
    # that are only extraction noise.
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body[:12000]


def mcp_get_question(args: dict[str, Any]) -> dict[str, Any]:
    conn = connect_db()
    init_db(conn)
    params: list[Any] = []
    sql = """
    SELECT q.*, f.filename, f.rel_path, f.stable_id, f.sha256, f.text_cache_path
    FROM questions q JOIN files f ON f.id=q.file_id
    WHERE f.active=1 AND q.official_source=1
    """
    if args.get("course"):
        sql += " AND q.course_code LIKE ?"
        params.append(f"%{normalize_course(str(args['course']))}%")
    if args.get("week"):
        sql += " AND q.week_label=?"
        params.append(mcp_week(args.get("week")))
    if args.get("exercise_type") or args.get("type"):
        sql += " AND lower(q.exercise_type)=lower(?)"
        params.append(str(args.get("exercise_type") or args.get("type")))
    if args.get("question_number"):
        qn = str(args["question_number"]).upper()
        if not qn.startswith("Q"):
            qn = f"Q{qn}"
        sql += " AND upper(q.question_number)=?"
        params.append(qn)
    sql += " ORDER BY q.course_code, q.week_label, q.exercise_type, q.question_number LIMIT 20"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        return {"status": "NOT_FOUND", "message": "No suitable teacher-provided question was found in the indexed official course materials.", "questions": []}
    return {
        "status": "ok",
        "questions": [
            {
                "question_id": row["id"],
                "course": row["course_code"],
                "week": row["week_label"],
                "type": row["exercise_type"],
                "source_file": row["filename"],
                "file_id": f"file_{(row['stable_id'] or row['sha256'])[:24]}",
                "question_number": row["question_number"],
                "original_question": (
                    extract_question_from_source_text(
                        read_cached_text(row, 200_000),
                        row["question_number"],
                    )
                    or row["question_text"]
                ),
                "source_location": row["source_location"],
                "relative_path": row["rel_path"],
            }
            for row in rows
        ],
    }


def mcp_search_study_library(args: dict[str, Any]) -> dict[str, Any]:
    files = mcp_search_files(args)["files"]
    content = mcp_search_content(args)["matches"] if (args.get("query") or args.get("topic")) else []
    questions = mcp_get_question(args).get("questions", [])
    return {"files": files, "questions": questions, "source_locations": content}


MCP_TOOLS: dict[str, dict[str, Any]] = {
    "list_courses": {"handler": mcp_list_courses, "description": "List courses indexed from the configured local study library.", "schema": {"type": "object", "properties": {}}},
    "list_weeks": {"handler": mcp_list_weeks, "description": "List weeks for a course.", "schema": {"type": "object", "properties": {"course": {"type": "string"}}, "required": ["course"]}},
    "list_files": {"handler": mcp_list_files, "description": "List files by course/week/category/exercise type.", "schema": {"type": "object", "properties": {"course": {"type": "string"}, "week": {"type": ["string", "number"]}, "category": {"type": "string"}, "exercise_type": {"type": "string"}, "filename": {"type": "string"}}}},
    "search_files": {"handler": mcp_search_files, "description": "Search file metadata in the Study Hub index.", "schema": {"type": "object", "properties": {"course": {"type": "string"}, "week": {"type": ["string", "number"]}, "category": {"type": "string"}, "exercise_type": {"type": "string"}, "filename": {"type": "string"}, "query": {"type": "string"}, "topic": {"type": "string"}}}},
    "search_content": {"handler": mcp_search_content, "description": "Search extracted readable content with source locations.", "schema": {"type": "object", "properties": {"course": {"type": "string"}, "week": {"type": ["string", "number"]}, "query": {"type": "string"}, "topic": {"type": "string"}, "limit": {"type": "number"}}, "required": ["query"]}},
    "search_study_library": {"handler": mcp_search_study_library, "description": "Unified search returning files, teacher questions, and source locations.", "schema": {"type": "object", "properties": {"course": {"type": "string"}, "week": {"type": ["string", "number"]}, "category": {"type": "string"}, "exercise_type": {"type": "string"}, "filename": {"type": "string"}, "question_number": {"type": "string"}, "query": {"type": "string"}, "topic": {"type": "string"}}}},
    "get_file_metadata": {"handler": mcp_get_file_metadata, "description": "Fetch metadata for a Study Hub file by safe internal file_id.", "schema": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}},
    "read_file": {"handler": mcp_read_file, "description": "Read extracted content and page/slide chunks for a Study Hub file by file_id.", "schema": {"type": "object", "properties": {"file_id": {"type": "string"}, "limit": {"type": "number"}}, "required": ["file_id"]}},
    "fetch_study_file": {"handler": mcp_read_file, "description": "Alias for read_file using a safe internal file_id.", "schema": {"type": "object", "properties": {"file_id": {"type": "string"}, "limit": {"type": "number"}}, "required": ["file_id"]}},
    "get_question": {"handler": mcp_get_question, "description": "Return only teacher-provided questions detected in official materials.", "schema": {"type": "object", "properties": {"course": {"type": "string"}, "week": {"type": ["string", "number"]}, "exercise_type": {"type": "string"}, "type": {"type": "string"}, "question_number": {"type": "string"}}}},
}


def mcp_tool_descriptors() -> list[dict[str, Any]]:
    descriptors = []
    for name, spec in MCP_TOOLS.items():
        descriptors.append(
            {
                "name": name,
                "title": name.replace("_", " ").title(),
                "description": spec["description"],
                "inputSchema": spec["schema"],
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
        )
    return descriptors


def jsonrpc_success(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def jsonrpc_error(rpc_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error}


def mcp_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = MCP_TOOLS.get(name)
    if spec is None:
        raise KeyError(f"Unknown tool: {name}")
    try:
        data = spec["handler"](arguments or {})
        is_error = False
    except FileNotFoundError:
        data = {"status": "NOT_FOUND", "message": "NOT FOUND"}
        is_error = True
    except PermissionError:
        data = {"status": "DENY", "message": "DENY"}
        is_error = True
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": is_error,
    }


def wants_generated_question(prompt: str) -> bool:
    return bool(re.search(r"\b(practice question|new question|give me .*question|generate .*question|出.*题|练习题)\b", prompt, re.IGNORECASE))


def openai_responses_request(
    prompt: str,
    chunks: list[dict[str, Any]],
    context: dict[str, Any],
    questions: list[dict[str, Any]] | None = None,
    solutions: list[dict[str, Any]] | None = None,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI is not configured")
    source_text = "\n\n".join(
        f"Course: {chunk['course_code']}\nWeek: {chunk.get('week_label') or ''}\nFile: {chunk['filename']}\nLocation: {chunk.get('source_location') or ''}\nExcerpt:\n{chunk['text'][:2400]}"
        for chunk in chunks[:6]
    )
    question_text = "\n\n".join(
        f"Course: {q['course_code']}\nWeek: {q['week_label']}\nType: {q['exercise_type']}\nFile: {q['filename']}\nQuestion: {q['question_number']}\nOriginal Question:\n{q['question_text'][:4000]}"
        for q in (questions or [])[:3]
    )
    solution_text = "\n\n".join(
        f"Official Teacher Solution\nCourse: {s['course_code']}\nWeek: {s['week_label']}\nType: {s['exercise_type']}\nFile: {s['filename']}"
        for s in (solutions or [])[:3]
    )
    wants_solution = bool(SOLUTION_INTENT_RE.search(prompt))
    instructions = (
        "You are a StudyHub Local assistant. Use only the provided official course excerpts. "
        "Never invent practice questions or teacher questions. If the answer is not supported, reply exactly: "
        "\"I couldn't find this in the currently indexed official course materials.\" "
        "If the user asks for a practice question, return only teacher-provided questions from the provided question section. "
        "Always include sources with Course, Week, Filename, and page/slide/question number when available. "
        "If an official solution is provided and the user asks for a solution, label it 'Official Teacher Solution'. "
        "Label your own explanation as 'GPT Explanation'. "
    )
    if question_text and not wants_solution:
        instructions += "The user has not explicitly asked for a solution; explain the question or relevant concepts without giving a full worked answer. "
    if question_text and not solution_text and wants_solution:
        instructions += (
            "No official solution was found. Begin the solution section with: "
            "\"No official solution was found. The explanation below is AI reasoning based on the indexed official course materials.\" "
        )
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "developer", "content": instructions},
            {
                "role": "user",
                "content": (
                    f"Context metadata: {json.dumps(context, ensure_ascii=False)}\n\n"
                    f"Teacher-provided questions:\n{question_text or '[none selected]'}\n\n"
                    f"Official teacher solutions:\n{solution_text or '[none found]'}\n\n"
                    f"Official excerpts:\n{source_text}\n\nUser request:\n{prompt}"
                ),
            },
        ],
        "store": False,
    }
    vector_store_id = current_vector_store_id()
    if vector_store_id:
        filters = []
        if context.get("course"):
            filters.append({"type": "eq", "key": "course_code", "value": context["course"]})
        if context.get("weekNumber"):
            filters.append({"type": "eq", "key": "week", "value": int(context["weekNumber"])})
        if context.get("stableId"):
            filters.append({"type": "eq", "key": "stable_id", "value": str(context["stableId"])})
        if context.get("exerciseType"):
            filters.append({"type": "eq", "key": "exercise_type", "value": str(context["exerciseType"])})
        tool: dict[str, Any] = {"type": "file_search", "vector_store_ids": [vector_store_id], "max_num_results": 10}
        if filters:
            tool["filters"] = {"type": "and", "filters": filters} if len(filters) > 1 else filters[0]
        payload["tools"] = [tool]
        payload["include"] = ["file_search_call.results"]
    req = urllib.request.Request(
        f"{OPENAI_API_BASE}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pieces: list[str] = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    pieces.append(content.get("text", ""))
    return "\n".join(pieces).strip() or json.dumps(data, ensure_ascii=False)[:4000]


def openai_json_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI is not configured")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{OPENAI_API_BASE}{path}",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current_vector_store_id(conn: sqlite3.Connection | None = None) -> str:
    env_id = os.environ.get("OPENAI_VECTOR_STORE_ID", "")
    if env_id:
        return env_id
    own_conn = None
    try:
        if conn is None:
            if not DB_PATH.exists():
                return ""
            own_conn = connect_db()
            conn = own_conn
        row = conn.execute(
            "SELECT vector_store_id FROM ai_index_state WHERE provider='openai' AND vector_store_id IS NOT NULL AND vector_store_id!='' ORDER BY last_synced_at DESC LIMIT 1"
        ).fetchone()
        return row["vector_store_id"] if row and row["vector_store_id"] else ""
    except Exception:
        return ""
    finally:
        if own_conn is not None:
            own_conn.close()


def openai_delete(path: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI is not configured")
    req = urllib.request.Request(
        f"{OPENAI_API_BASE}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def openai_upload_file(path: Path) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI is not configured")
    boundary = f"----studyhub{int(time.time() * 1000)}"
    file_bytes = path.read_bytes()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nassistants\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}\r\n\r\n"
        ).encode()
        + file_bytes
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        f"{OPENAI_API_BASE}/files",
        data=b"".join(parts),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_openai_vector_store(conn: sqlite3.Connection) -> str:
    existing = os.environ.get("OPENAI_VECTOR_STORE_ID", "")
    if existing:
        return existing
    row = conn.execute(
        "SELECT vector_store_id FROM ai_index_state WHERE provider='openai' AND vector_store_id IS NOT NULL AND vector_store_id!='' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row and row["vector_store_id"]:
        return row["vector_store_id"]
    created = openai_json_request("POST", "/vector_stores", {"name": "StudyHub Local"})
    return created["id"]


def sync_openai_vector_store(limit: int = 20) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return {"status": "not_configured", "message": "OPENAI_API_KEY is not configured server-side.", "synced": 0, "failed": 0}
    conn = connect_db()
    init_db(conn)
    vector_store_id = ensure_openai_vector_store(conn)
    unchanged = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM files f
        JOIN ai_index_state ais ON ais.file_id=f.id AND ais.provider='openai'
        WHERE f.active=1 AND f.is_official=1 AND f.suspicious='' AND f.ai_index_status='indexed'
          AND ais.sha256=f.sha256 AND ais.status IN ('indexed', 'completed', 'in_progress')
        """
    ).fetchone()["c"]
    rows = conn.execute(
        """
        SELECT f.*, ais.provider_file_id AS old_provider_file_id, ais.vector_store_id AS old_vector_store_id,
          ais.sha256 AS old_sha256
        FROM files f
        LEFT JOIN ai_index_state ais ON ais.file_id=f.id AND ais.provider='openai'
        WHERE f.active=1 AND f.is_official=1 AND f.suspicious='' AND f.ai_index_status='indexed'
          AND (ais.id IS NULL OR ais.sha256 != f.sha256 OR ais.status NOT IN ('indexed', 'completed', 'in_progress'))
        ORDER BY f.course_code, f.week_label, f.filename
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    synced = 0
    failed = 0
    stale_removed = 0
    errors = []
    for row in rows:
        try:
            if row["old_provider_file_id"] and row["old_vector_store_id"] and row["old_sha256"] and row["old_sha256"] != row["sha256"]:
                try:
                    openai_delete(f"/vector_stores/{row['old_vector_store_id']}/files/{row['old_provider_file_id']}")
                    openai_delete(f"/files/{row['old_provider_file_id']}")
                    stale_removed += 1
                except Exception:
                    errors.append({"file": row["filename"], "stale_remove_warning": "Could not remove stale OpenAI file"})
            uploaded = openai_upload_file(Path(row["original_path"]))
            attrs = file_metadata(row)
            attrs["source"] = "Canvas"
            attrs["week"] = row["week_number"] or 0
            attached = openai_json_request(
                "POST",
                f"/vector_stores/{vector_store_id}/files",
                {"file_id": uploaded["id"], "attributes": attrs},
            )
            conn.execute(
                """
                INSERT INTO ai_index_state(file_id, stable_id, sha256, provider, vector_store_id, provider_file_id, status, error, last_synced_at, metadata_json)
                VALUES (?, ?, ?, 'openai', ?, ?, ?, '', ?, ?)
                ON CONFLICT(file_id, provider) DO UPDATE SET
                  stable_id=excluded.stable_id, sha256=excluded.sha256, vector_store_id=excluded.vector_store_id,
                  provider_file_id=excluded.provider_file_id, status=excluded.status, error='',
                  last_synced_at=excluded.last_synced_at, metadata_json=excluded.metadata_json
                """,
                (
                    row["id"],
                    row["stable_id"],
                    row["sha256"],
                    vector_store_id,
                    uploaded["id"],
                    attached.get("status", "in_progress"),
                    now_iso(),
                    json.dumps(attrs, ensure_ascii=False),
                ),
            )
            synced += 1
        except Exception as exc:
            failed += 1
            safe_error = type(exc).__name__
            errors.append({"file": row["filename"], "error": safe_error})
            conn.execute(
                """
                INSERT INTO ai_index_state(file_id, stable_id, sha256, provider, vector_store_id, status, error, last_synced_at, metadata_json)
                VALUES (?, ?, ?, 'openai', ?, 'failed', ?, ?, ?)
                ON CONFLICT(file_id, provider) DO UPDATE SET status='failed', error=excluded.error, last_synced_at=excluded.last_synced_at
                """,
                (row["id"], row["stable_id"], row["sha256"], vector_store_id, safe_error, now_iso(), json.dumps(file_metadata(row), ensure_ascii=False)),
            )
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "vectorStore": "Configured" if vector_store_id else "Not configured",
        "candidates": len(rows),
        "synced": synced,
        "unchanged": unchanged,
        "staleRemoved": stale_removed,
        "failed": failed,
        "errors": errors[:10],
    }


def local_bridge_response(prompt: str, chunks: list[dict[str, Any]], questions: list[dict[str, Any]] | None = None) -> str:
    excerpts = []
    for question in (questions or [])[:2]:
        excerpts.append(
            "\n".join(
                [
                    "Teacher-provided question",
                    f"Course: {question['course_code']}",
                    f"Week: {question['week_label']}",
                    f"File: {question['filename']}",
                    f"Question: {question['question_number']}",
                    question["question_text"][:1200],
                ]
            )
        )
    for chunk in chunks[:3]:
        excerpt = re.sub(r"\s+", " ", chunk["text"]).strip()[:700]
        excerpts.append(f"{chunk['filename']} ({chunk.get('source_location') or 'source'}): {excerpt}")
    return (
        "OpenAI is not configured, so this is a local source-backed preview rather than a GPT answer.\n\n"
        + "\n\n".join(excerpts)
        + "\n\nSources\n"
        + format_sources(chunks)
    )


class StudyHubHandler(BaseHTTPRequestHandler):
    server_version = "StudyHubLocal/1.0"

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        try:
            self.validate_loopback_request_host()
        except Exception as exc:
            self.handle_exception(exc)
            return False
        return True

    def log_message(self, fmt: str, *args: Any) -> None:
        log_event("http", "request handled")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none';",
        )
        request_path = urllib.parse.urlparse(getattr(self, "path", "")).path
        if request_path.startswith("/api/") or request_path == "/mcp":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_no_content(self) -> None:
        self.send_response(204)
        self.end_headers()

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def read_limited_body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return b""
        if length > limit:
            raise PayloadTooLarge("Request body too large")
        return self.rfile.read(length)

    def parse_body_json(self, limit: int = MAX_JSON_BODY_SIZE) -> dict[str, Any]:
        body = self.read_limited_body(limit)
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def validate_loopback_request_host(self) -> None:
        if not request_host_is_loopback(self.headers.get("Host", "")):
            raise PermissionError("Non-loopback host denied")

    def validate_same_origin_mutation(self) -> None:
        self.validate_loopback_request_host()
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        if origin and not is_safe_loopback_origin(origin, host):
            raise PermissionError("Cross-origin request denied")
        sec_fetch_site = (self.headers.get("Sec-Fetch-Site", "") or "").lower()
        if sec_fetch_site == "cross-site":
            raise PermissionError("Cross-site request denied")
        token = self.headers.get("X-StudyHub-CSRF", "")
        if not secrets.compare_digest(token, CSRF_TOKEN):
            raise PermissionError("Invalid CSRF token")

    def handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, PayloadTooLarge):
            self.send_error_json(413, "Request body too large")
        elif isinstance(exc, PermissionError):
            self.send_error_json(403, str(exc) if str(exc) else "Forbidden")
        elif isinstance(exc, FileNotFoundError):
            self.send_error_json(404, "Not found")
        elif isinstance(exc, (ValueError, json.JSONDecodeError)):
            self.send_error_json(400, str(exc) if str(exc) else "Invalid request")
        else:
            log_event("error", type(exc).__name__)
            self.send_error_json(500, "Internal StudyHub error")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            self.validate_loopback_request_host()
            if path == "/mcp":
                self.send_json(
                    {
                        "name": "StudyHub Local Readonly MCP",
                        "transport": "streamable-http/json-rpc",
                        "endpoint": "/mcp",
                        "tools": [tool["name"] for tool in mcp_tool_descriptors()],
                        "readOnly": True,
                    }
                )
                return
            if path.startswith("/api/"):
                self.handle_api_get(path, qs)
                return
            if path.startswith("/preview/"):
                self.handle_preview(int(path.rsplit("/", 1)[-1]))
                return
            self.serve_static(path)
        except Exception as exc:
            self.handle_exception(exc)

    def do_HEAD(self) -> None:
        try:
            self.validate_loopback_request_host()
            self.send_response(204)
            self.end_headers()
        except Exception as exc:
            self.handle_exception(exc)

    def do_OPTIONS(self) -> None:
        try:
            self.validate_loopback_request_host()
            self.send_response(204)
            self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
            self.end_headers()
        except Exception as exc:
            self.handle_exception(exc)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.validate_same_origin_mutation()
            if parsed.path == "/mcp":
                self.handle_mcp_rpc()
            elif parsed.path == "/api/scan":
                stats = scan_library(DEFAULT_STUDY_ROOT)
                self.send_json({"ok": True, "stats": stats.__dict__})
            elif parsed.path == "/api/upload":
                self.handle_upload()
            elif parsed.path.startswith("/api/open/"):
                self.handle_open(int(parsed.path.rsplit("/", 1)[-1]))
            elif parsed.path.startswith("/api/star/"):
                self.handle_star(int(parsed.path.rsplit("/", 1)[-1]))
            elif parsed.path == "/api/notes":
                self.handle_note()
            elif parsed.path == "/api/ask":
                self.handle_ask()
            elif parsed.path == "/api/ai-sync":
                self.send_json(sync_openai_vector_store())
            else:
                self.send_error_json(404, "Not found")
        except Exception as exc:
            self.handle_exception(exc)

    def handle_mcp_rpc(self) -> None:
        body = self.parse_body_json(MAX_MCP_BODY_SIZE)
        if not isinstance(body, dict):
            self.send_json(jsonrpc_error(None, -32600, "Invalid Request"), 400)
            return
        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            self.send_json(jsonrpc_error(rpc_id, -32602, "Invalid params"), 400)
            return
        if method == "notifications/initialized":
            self.send_no_content()
            return
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "StudyHub Local Readonly MCP", "version": "1.0.0"},
                    "instructions": (
                        "Read-only access to the user's configured local study library. "
                        "Use only returned source-backed content; never invent teacher questions."
                    ),
                }
            elif method == "tools/list":
                result = {"tools": mcp_tool_descriptors()}
            elif method == "tools/call":
                name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    self.send_json(jsonrpc_error(rpc_id, -32602, "Tool arguments must be an object"), 400)
                    return
                result = mcp_call_tool(name, arguments)
            else:
                self.send_json(jsonrpc_error(rpc_id, -32601, "Method not found"), 404)
                return
            self.send_json(jsonrpc_success(rpc_id, result))
        except KeyError as exc:
            self.send_json(jsonrpc_error(rpc_id, -32602, "Invalid tool name"), 400)
        except PermissionError:
            self.send_json(jsonrpc_error(rpc_id, -32603, "DENY"), 403)
        except FileNotFoundError:
            self.send_json(jsonrpc_error(rpc_id, -32602, "NOT FOUND"), 404)
        except ValueError as exc:
            self.send_json(jsonrpc_error(rpc_id, -32602, str(exc)), 400)

    def handle_api_get(self, path: str, qs: dict[str, list[str]]) -> None:
        conn = connect_db()
        init_db(conn)
        try:
            if path == "/api/session":
                self.send_json(api_session())
            elif path == "/api/health":
                self.send_json(api_health(conn))
            elif path == "/api/courses":
                rows = conn.execute(
                    """
                    SELECT c.*, COUNT(DISTINCT f.id) AS file_count,
                      MAX(CASE WHEN w.has_materials=1 THEN w.week_number ELSE NULL END) AS latest_week
                    FROM courses c
                    LEFT JOIN files f ON f.course_id=c.id AND f.active=1
                    LEFT JOIN weeks w ON w.course_id=c.id
                    GROUP BY c.id
                    ORDER BY c.code
                    """
                ).fetchall()
                self.send_json([public_course(row) for row in rows])
            elif path == "/api/weeks":
                course_id = int(qs.get("course_id", ["0"])[0])
                rows = conn.execute(
                    "SELECT * FROM weeks WHERE course_id=? ORDER BY week_number",
                    (course_id,),
                ).fetchall()
                self.send_json([public_week(row) for row in rows])
            elif path == "/api/files":
                course_id = int(qs.get("course_id", ["0"])[0])
                week_label = qs.get("week", [""])[0]
                params: list[Any] = [course_id]
                sql = "SELECT f.*, s.id AS star_id FROM files f LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id WHERE f.course_id=? AND f.active=1"
                if week_label:
                    sql += " AND f.week_label=?"
                    params.append(week_label)
                sql += " ORDER BY f.week_label, f.section, f.category, f.filename"
                self.send_json([public_file(row) for row in conn.execute(sql, params).fetchall()])
            elif path == "/api/file":
                file_id = int(qs.get("id", ["0"])[0])
                row = get_file(conn, file_id)
                self.send_json(public_file(row, include_text=read_cached_text(row, 8000)))
            elif path == "/api/search":
                query = qs.get("q", [""])[0].strip()
                course_id = qs.get("course_id", [""])[0]
                week = qs.get("week", [""])[0]
                scope = qs.get("scope", [""])[0]
                params: list[Any] = []
                normalized_query = fts_query(query)
                if normalized_query:
                    sql = """
                    SELECT f.*, bm25(files_fts) AS rank
                    FROM files_fts JOIN files f ON files_fts.rowid=f.id
                    WHERE files_fts MATCH ? AND f.active=1
                    """
                    params.append(normalized_query)
                else:
                    sql = "SELECT f.*, 0 AS rank FROM files f WHERE f.active=1"
                if course_id:
                    sql += " AND f.course_id=?"
                    params.append(int(course_id))
                if week:
                    sql += " AND f.week_label=?"
                    params.append(week)
                if scope:
                    sql += " AND (f.section=? OR f.category=? OR f.exercise_type=?)"
                    params.extend([scope, scope, scope])
                sql += " ORDER BY rank, f.modified_at DESC LIMIT 80"
                self.send_json([public_file(row) for row in conn.execute(sql, params).fetchall()])
            elif path == "/api/recent":
                rows = conn.execute("SELECT * FROM files WHERE active=1 ORDER BY indexed_at DESC LIMIT 20").fetchall()
                self.send_json([public_file(row) for row in rows])
            elif path == "/api/starred":
                rows = conn.execute(
                    "SELECT f.* FROM stars s JOIN files f ON f.id=s.target_id WHERE s.target_type='file' AND f.active=1 ORDER BY s.created_at DESC"
                ).fetchall()
                self.send_json([public_file(row) for row in rows])
            elif path == "/api/context":
                file_id = int(qs.get("file_id", ["0"])[0])
                self.send_json(build_context_pack(conn, file_id))
            elif path == "/api/prepare-context":
                self.send_json(self.handle_prepare_context(conn, qs))
            elif path == "/api/questions":
                self.send_json(self.handle_questions(conn, qs))
            elif path == "/api/ai-status":
                self.send_json(self.handle_ai_status(conn))
            elif path == "/api/wrong-questions":
                rows = conn.execute("SELECT * FROM wrong_questions ORDER BY created_at DESC LIMIT 100").fetchall()
                self.send_json(rows_to_dicts(rows))
            else:
                self.send_error_json(404, "Not found")
        finally:
            conn.close()

    def handle_questions(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = """
        SELECT q.*, f.filename, f.rel_path, f.id AS source_file_id
        FROM questions q
        JOIN files f ON f.id=q.file_id
        WHERE f.active=1 AND q.official_source=1
        """
        course = qs.get("course", [""])[0]
        week = qs.get("week", [""])[0]
        exercise_type = qs.get("type", [""])[0]
        if course:
            sql += " AND q.course_code=?"
            params.append(course)
        if week:
            sql += " AND q.week_label=?"
            params.append(week if week.startswith("Week ") else f"Week {int(week):02d}")
        if exercise_type:
            sql += " AND lower(q.exercise_type)=lower(?)"
            params.append(exercise_type)
        sql += " ORDER BY q.course_code, q.week_label, q.exercise_type, q.question_number LIMIT 80"
        return rows_to_dicts(conn.execute(sql, params).fetchall())

    def handle_prepare_context(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> dict[str, Any]:
        course = qs.get("course", [""])[0]
        week = qs.get("week", [""])[0]
        file_id = int(qs.get("file_id", ["0"])[0] or 0)
        question_id = int(qs.get("question_id", ["0"])[0] or 0)
        prompt = qs.get("q", [""])[0]
        context: dict[str, Any] = {"course": course, "week": week, "fileId": file_id or None, "questionId": question_id or None}
        if file_id:
            file_context = build_context_pack(conn, file_id)
            context.update(file_context)
        if question_id:
            row = conn.execute(
                "SELECT q.*, f.filename, f.rel_path FROM questions q JOIN files f ON f.id=q.file_id WHERE q.id=?",
                (question_id,),
            ).fetchone()
            if row:
                context["question"] = dict(row)
                context["course"] = row["course_code"]
                context["week"] = row["week_label"]
        chunks = search_local_context(conn, prompt, context, limit=6)
        return {
            "course": context.get("course"),
            "week": context.get("week"),
            "file": context.get("selectedFile"),
            "question": context.get("question"),
            "sources": chunks,
            "sourceSummary": format_sources(chunks),
            "policy": "Use only indexed official course sources. Never invent practice questions.",
        }

    def handle_ai_status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM ai_index_state GROUP BY status ORDER BY status"
        ).fetchall()
        indexed_files = conn.execute("SELECT COUNT(*) AS c FROM files WHERE active=1 AND ai_index_status='indexed'").fetchone()["c"]
        vector_indexed = conn.execute("SELECT COUNT(*) AS c FROM ai_index_state WHERE provider='openai' AND status IN ('indexed', 'completed', 'in_progress')").fetchone()["c"]
        vector_row = conn.execute(
            "SELECT vector_store_id FROM ai_index_state WHERE provider='openai' AND vector_store_id IS NOT NULL AND vector_store_id!='' ORDER BY last_synced_at DESC LIMIT 1"
        ).fetchone()
        vector_store_id = os.environ.get("OPENAI_VECTOR_STORE_ID") or (vector_row["vector_store_id"] if vector_row else "")
        return {
            "openAI": "Configured" if bool(os.environ.get("OPENAI_API_KEY")) else "Not configured",
            "vectorStore": "Configured" if vector_store_id else "Not configured",
            "vectorStoreLabel": "Configured" if vector_store_id else "Not configured",
            "localIndex": rows_to_dicts(rows),
            "indexedFiles": indexed_files,
            "vectorIndexedFiles": vector_indexed,
            "lastAISync": conn.execute("SELECT MAX(last_synced_at) AS ts FROM ai_index_state").fetchone()["ts"],
        }

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not is_inside(target, STATIC_DIR) or not target.exists() or target.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_escaped_text_preview(self, path: Path, text: str) -> None:
        body = html.escape(text[:20000] or "No text preview available. Use Open Original.").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(path.name + '.txt')}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def handle_preview(self, file_id: int) -> None:
        conn = connect_db()
        try:
            row = get_file(conn, file_id)
            path = Path(row["original_path"]).resolve()
            ctype = row["mime_type"] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            ext = path.suffix.lower()
            if ext in ACTIVE_WEB_PREVIEW_EXTS:
                text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
                self.send_escaped_text_preview(path, text)
                return
            if ext in OOXML_PREVIEW_EXTS:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                text = read_cached_text(row, 20000)
                body = f"<pre>{html.escape(text[:20000] or 'No text preview available. Use Open Original.')}</pre>"
                self.wfile.write(body.encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(path.name)}")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        finally:
            conn.close()

    def handle_open(self, file_id: int) -> None:
        conn = connect_db()
        try:
            row = get_file(conn, file_id)
            path = Path(row["original_path"]).resolve()
            subprocess.Popen(["open", str(path)])
            self.send_json({"ok": True})
        finally:
            conn.close()

    def handle_star(self, file_id: int) -> None:
        conn = connect_db()
        init_db(conn)
        existing = conn.execute("SELECT id FROM stars WHERE target_type='file' AND target_id=?", (file_id,)).fetchone()
        if existing:
            conn.execute("DELETE FROM stars WHERE id=?", (existing["id"],))
            starred = False
        else:
            conn.execute(
                "INSERT INTO stars(target_type, target_id, created_at) VALUES ('file', ?, ?)",
                (file_id, now_iso()),
            )
            starred = True
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "starred": starred})

    def handle_note(self) -> None:
        body = self.parse_body_json()
        conn = connect_db()
        conn.execute(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                body.get("targetType", "file"),
                body.get("targetId"),
                body.get("courseId"),
                body.get("week"),
                body.get("body", ""),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
        conn.close()
        self.send_json({"ok": True})

    def handle_ask(self) -> None:
        body = self.parse_body_json()
        conn = connect_db()
        prompt = body.get("prompt", "").strip()
        context = normalize_ask_context(conn, body.get("context", {}) or {}, prompt)
        questions = teacher_questions_for_context(conn, context, prompt, limit=5)
        solutions = official_solutions_for_context(conn, context, limit=5)
        if wants_generated_question(prompt):
            response = teacher_question_response(questions)
            status = "teacher_question" if questions else "no_teacher_question"
            chunks = []
            context_json = json.dumps({"context": context, "questions": questions, "solutions": solutions}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO ai_interactions(context_json, prompt, response, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (context_json, prompt, response, status, now_iso()),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": status, "response": response, "sources": [], "questions": questions, "solutions": solutions})
            return
        chunks = search_local_context(conn, prompt, context, limit=8)
        status = "local"
        if not chunks and not questions:
            response = "I couldn't find this in the currently indexed official course materials."
            status = "no_source"
        elif os.environ.get("OPENAI_API_KEY"):
            try:
                response = openai_responses_request(prompt, chunks, context, questions=questions, solutions=solutions)
                status = "openai"
            except Exception as exc:
                response = (
                    "OpenAI request failed, so I am falling back to local indexed excerpts.\n\n"
                    f"{local_bridge_response(prompt, chunks, questions)}"
                )
                log_event("openai_error", type(exc).__name__)
                status = "openai_error_local_fallback"
        else:
            response = local_bridge_response(prompt, chunks, questions)
        context_json = json.dumps({"context": context, "sources": chunks[:8], "questions": questions, "solutions": solutions}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO ai_interactions(context_json, prompt, response, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (context_json, prompt, response, status, now_iso()),
        )
        conn.commit()
        conn.close()
        self.send_json({"status": status, "response": response, "sources": chunks[:8], "questions": questions, "solutions": solutions})

    def handle_upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        upload = MultipartUpload(ctype, self.read_limited_body(MAX_UPLOAD_REQUEST_SIZE))
        course_id = int(upload.fields.get("course_id", "0") or 0)
        week = normalize_week(upload.fields.get("week", ""))
        section = validate_path_component(upload.fields.get("section", ""))
        category = validate_path_component(upload.fields.get("category", ""))
        if not course_id or not week or not section or not category:
            raise ValueError("course_id, week, section, and category are required")
        if section not in ALLOWED_SECTIONS:
            raise PermissionError("Unknown upload section")
        conn = connect_db()
        try:
            course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
            if course is None:
                raise ValueError("unknown course")
            week_row = conn.execute("SELECT id FROM weeks WHERE course_id=? AND week_label=?", (course_id, week)).fetchone()
            if week_row is None:
                raise PermissionError("Unknown upload week")
            target_dir = safe_child_path(DEFAULT_STUDY_ROOT, course["folder_name"], week, section, category)
            saved = []
            for _field, filename, data in upload.files:
                if len(data) > MAX_UPLOAD_FILE_SIZE:
                    raise PayloadTooLarge("Uploaded file too large")
                safe_name = safe_filename(filename)
                digest = hashlib.sha256(data).hexdigest()
                target, duplicate = unique_path_for_upload(target_dir, safe_name, digest)
                if not duplicate:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    tmp_path: Path | None = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=target_dir, prefix=".upload-", delete=False) as tmp:
                            tmp.write(data)
                            tmp_path = Path(tmp.name)
                        tmp_path.replace(target)
                    finally:
                        if tmp_path and tmp_path.exists():
                            tmp_path.unlink(missing_ok=True)
                    log_event("upload", json.dumps({"file": safe_relative(target, DEFAULT_STUDY_ROOT)}, ensure_ascii=False))
                saved.append({"filename": target.name, "relative_path": safe_relative(target, DEFAULT_STUDY_ROOT), "duplicate": duplicate})
        finally:
            conn.close()
        stats = scan_library(DEFAULT_STUDY_ROOT)
        self.send_json({"ok": True, "saved": saved, "scan": stats.__dict__})


def find_free_port(start_port: int) -> int:
    bind_host = validate_loopback_bind_host(DEFAULT_HOST)
    port = start_port
    while port < start_port + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((bind_host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("No free localhost port found")


def serve(port: int, open_browser: bool = False, scan_first: bool = True) -> None:
    ensure_dirs()
    bind_host = validate_loopback_bind_host(DEFAULT_HOST)
    if scan_first:
        scan_library(DEFAULT_STUDY_ROOT)
    chosen_port = find_free_port(port)
    server = ThreadingHTTPServer((bind_host, chosen_port), StudyHubHandler)
    display_host = "localhost" if bind_host in {"127.0.0.1", "::1", "localhost"} else bind_host
    url = f"http://{display_host}:{chosen_port}"
    print(f"{APP_NAME} running at {url}", flush=True)
    print("Study library: configured local path", flush=True)
    print("SQLite database: local runtime database", flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()


def verify_library() -> int:
    conn = connect_db()
    init_db(conn)
    rows = conn.execute("SELECT id, original_path FROM files").fetchall()
    bad = []
    for row in rows:
        issue = is_suspicious_file(Path(row["original_path"]))
        if issue:
            bad.append((row["rel_path"], issue))
    print(f"Indexed files: {len(rows)}")
    print(f"Suspicious files: {len(bad)}")
    for path, issue in bad[:50]:
        print(f"- {path}: {issue}")
    conn.close()
    return 1 if bad else 0


def backup_metadata() -> Path:
    ensure_dirs()
    if not DB_PATH.exists():
        raise FileNotFoundError("Database does not exist yet.")
    backup = DATA_DIR / f"studyhub-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite.bak"
    shutil.copy2(DB_PATH, backup)
    print(backup)
    return backup


def normalize_week(value: str) -> str:
    if not value:
        return ""
    if value.lower().startswith("week"):
        m = re.search(r"\d+", value)
        return f"Week {int(m.group(0)):02d}" if m else value
    return f"Week {int(value):02d}" if value.isdigit() else value


def cli_status() -> int:
    conn = connect_db()
    init_db(conn)
    print(json.dumps(api_health(conn), ensure_ascii=False, indent=2))
    conn.close()
    return 0


def cli_search(args: argparse.Namespace) -> int:
    conn = connect_db()
    init_db(conn)
    params: list[Any] = []
    sql = "SELECT id, course_code, week_label, category, exercise_type, filename, rel_path, is_official FROM files WHERE active=1"
    if args.course:
        sql += " AND course_code LIKE ?"
        params.append(f"%{args.course}%")
    if args.week:
        sql += " AND week_label=?"
        params.append(normalize_week(args.week))
    if args.type:
        sql += " AND (lower(category)=lower(?) OR lower(exercise_type)=lower(?) OR lower(section)=lower(?))"
        params.extend([args.type, args.type, args.type])
    if args.query:
        sql += " AND (filename LIKE ? OR rel_path LIKE ?)"
        params.extend([f"%{args.query}%", f"%{args.query}%"])
    sql += " ORDER BY course_code, week_label, category, filename LIMIT 80"
    files = rows_to_dicts(conn.execute(sql, params).fetchall())
    qparams: list[Any] = []
    qsql = "SELECT id, course_code, week_label, exercise_type, question_number, source_location, question_text FROM questions WHERE official_source=1"
    if args.course:
        qsql += " AND course_code LIKE ?"
        qparams.append(f"%{args.course}%")
    if args.week:
        qsql += " AND week_label=?"
        qparams.append(normalize_week(args.week))
    if args.type:
        qsql += " AND lower(exercise_type)=lower(?)"
        qparams.append(args.type)
    if args.query:
        qsql += " AND question_text LIKE ?"
        qparams.append(f"%{args.query}%")
    qsql += " ORDER BY course_code, week_label, exercise_type, question_number LIMIT 80"
    questions = rows_to_dicts(conn.execute(qsql, qparams).fetchall())
    print(json.dumps({"matching_files": files, "matching_questions": questions}, ensure_ascii=False, indent=2))
    conn.close()
    return 0


def cli_context(args: argparse.Namespace) -> int:
    conn = connect_db()
    init_db(conn)
    qs = {
        "course": [args.course or ""],
        "week": [normalize_week(args.week or "")],
        "file_id": [str(args.file_id or 0)],
        "question_id": [str(args.question_id or 0)],
        "q": [args.query or ""],
    }
    handler = object.__new__(StudyHubHandler)
    print(json.dumps(handler.handle_prepare_context(conn, qs), ensure_ascii=False, indent=2))
    conn.close()
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "scan", "reindex", "verify", "backup", "health", "status", "search", "context", "ai-sync"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--no-scan", action="store_true", help="Start immediately using the existing local index.")
    parser.add_argument("--course", default="")
    parser.add_argument("--week", default="")
    parser.add_argument("--type", default="")
    parser.add_argument("--file-id", type=int, default=0)
    parser.add_argument("--question-id", type=int, default=0)
    parser.add_argument("query", nargs="?", default="")
    args = parser.parse_args(argv)
    if args.command in {"scan", "reindex"}:
        print(scan_library(DEFAULT_STUDY_ROOT))
        return 0
    if args.command == "verify":
        return verify_library()
    if args.command == "backup":
        backup_metadata()
        return 0
    if args.command in {"health", "status"}:
        return cli_status()
    if args.command == "search":
        return cli_search(args)
    if args.command == "context":
        return cli_context(args)
    if args.command == "ai-sync":
        print(json.dumps(sync_openai_vector_store(), ensure_ascii=False, indent=2))
        return 0
    serve(args.port, open_browser=args.open, scan_first=not args.no_scan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
