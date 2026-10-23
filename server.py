#!/usr/bin/env python3
"""StudyHub Local: private local-first study library server."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import mimetypes
import os
import ipaddress
import re
import shutil
import secrets
import signal
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

try:
    import certifi
except ImportError:
    certifi = None

APP_NAME = "StudyHub Local"
APP_ROOT = Path(__file__).resolve().parent
RUNTIME_PROFILE = os.environ.get("STUDYHUB_RUNTIME_PROFILE", "source").strip().lower() or "source"
if RUNTIME_PROFILE not in {"source", "production", "development", "demo-test"}:
    raise RuntimeError("Unknown StudyHub runtime profile")

DEMO_BLOCKED_CONFIG_KEYS = {
    "DEMO_MODE",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_VECTOR_STORE_ID",
    "STUDY_LIBRARY_PATH",
}
if RUNTIME_PROFILE == "demo-test":
    for blocked_key in DEMO_BLOCKED_CONFIG_KEYS:
        os.environ.pop(blocked_key, None)

ENV_LOCAL_PATH = Path(os.environ.get("STUDYHUB_CONFIG_PATH", APP_ROOT / ".env.local")).expanduser()
if not ENV_LOCAL_PATH.is_absolute():
    ENV_LOCAL_PATH = APP_ROOT / ENV_LOCAL_PATH
ENV_LOCAL_EXISTS = ENV_LOCAL_PATH.exists()


def load_local_env() -> None:
    if not ENV_LOCAL_PATH.exists():
        return
    for raw_line in ENV_LOCAL_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if RUNTIME_PROFILE == "demo-test" and key in DEMO_BLOCKED_CONFIG_KEYS:
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


load_local_env()
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8765"))


def configured_path(env_name: str, fallback: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    path = Path(raw).expanduser() if raw else fallback
    if not path.is_absolute():
        path = APP_ROOT / path
    return path


RUNTIME_DIR = configured_path("STUDYHUB_RUNTIME_DIR", APP_ROOT)
DATA_DIR = configured_path("STUDYHUB_DATA_DIR", RUNTIME_DIR / "data")
EXPORT_DIR = DATA_DIR / "exports"
RESTORE_INBOX_DIR = DATA_DIR / "restore-inbox"
CACHE_DIR = configured_path("STUDYHUB_CACHE_DIR", RUNTIME_DIR / "cache")
TEXT_CACHE_DIR = CACHE_DIR / "text"
TEXT_EXTRACTION_VERSION = "extract-v2-office-paragraphs"
LOG_DIR = configured_path("STUDYHUB_LOG_DIR", RUNTIME_DIR / "logs")
STATIC_DIR = configured_path("STUDYHUB_STATIC_DIR", APP_ROOT / "static")
KATEX_DIST_DIR = configured_path("STUDYHUB_KATEX_DIR", APP_ROOT / "node_modules" / "katex" / "dist")
DESKTOP_MODE = os.environ.get("STUDYHUB_DESKTOP", "").strip().lower() in {"1", "true", "yes", "on"}
PACKAGED_BACKEND = bool(getattr(sys, "frozen", False))


def read_local_env_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    if not ENV_LOCAL_PATH.exists():
        return entries
    for raw_line in ENV_LOCAL_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if RUNTIME_PROFILE == "demo-test" and key in DEMO_BLOCKED_CONFIG_KEYS:
            continue
        entries[key] = value.strip().strip('"').strip("'")
    return entries


def write_local_env_entries(entries: dict[str, str]) -> None:
    ensure_dirs()
    if RUNTIME_PROFILE == "demo-test":
        entries = {key: value for key, value in entries.items() if key not in DEMO_BLOCKED_CONFIG_KEYS}
    ENV_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered = [
        "STUDY_LIBRARY_PATH",
        "HOST",
        "PORT",
        "OPENAI_API_KEY",
        "OPENAI_VECTOR_STORE_ID",
        "OPENAI_MODEL",
        "OPENAI_API_BASE",
    ]
    lines = ["# Local private StudyHub configuration. Do not commit this file."]
    seen: set[str] = set()
    for key in ordered:
        if key in entries:
            lines.append(f'{key}="{entries[key]}"')
            seen.add(key)
    for key in sorted(k for k in entries if k not in seen):
        lines.append(f'{key}="{entries[key]}"')
    ENV_LOCAL_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_user_library_path(raw_path: str) -> Path:
    if RUNTIME_PROFILE == "demo-test":
        raise PermissionError("Demo/Test mode cannot connect an external study library.")
    value = (raw_path or "").strip()
    if not value:
        raise ValueError("Enter the folder where your study files live.")
    if "\x00" in value:
        raise ValueError("Choose a normal local folder path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use a full local path, or start with ~/ for your home folder.")
    if not path.exists():
        raise FileNotFoundError("That folder was not found. Create it or choose an existing study folder.")
    if not path.is_dir():
        raise ValueError("Choose a folder, not a single file.")
    return path.resolve()


def save_study_library_config(raw_path: str) -> dict[str, Any]:
    validate_user_library_path(raw_path)
    entries = read_local_env_entries()
    entries.pop("DEMO_MODE", None)
    entries["STUDY_LIBRARY_PATH"] = raw_path.strip()
    entries.setdefault("HOST", "127.0.0.1")
    write_local_env_entries(entries)
    return {
        "ok": True,
        "restartRequired": True,
        "message": "StudyHub will use this folder after you restart the local server.",
    }


DB_PATH = configured_path("DATABASE_PATH", DATA_DIR / "studyhub.sqlite")
DEFAULT_STUDY_ROOT = configured_path(
    "STUDY_LIBRARY_PATH",
    DATA_DIR / "managed-library",
)
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
CSRF_TOKEN = secrets.token_urlsafe(32)
MAX_JSON_BODY_SIZE = int(os.environ.get("MAX_JSON_BODY_SIZE", str(512 * 1024)))
MAX_MCP_BODY_SIZE = int(os.environ.get("MAX_MCP_BODY_SIZE", str(512 * 1024)))
MAX_UPLOAD_FILE_SIZE = int(os.environ.get("MAX_UPLOAD_FILE_SIZE", str(50 * 1024 * 1024)))
MAX_UPLOAD_REQUEST_SIZE = int(os.environ.get("MAX_UPLOAD_REQUEST_SIZE", str(60 * 1024 * 1024)))
BACKUP_FORMAT_VERSION = 1
BACKUP_FORMAT_NAME = "studyhub-backup"
BACKUP_MAX_ENTRIES = 32
BACKUP_MAX_ENTRY_BYTES = 10 * 1024 * 1024
BACKUP_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
BACKUP_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
BACKUP_MAX_RECORDS = 100_000
BACKUP_DATA_MEMBERS = (
    "data/terms.json",
    "data/courses.json",
    "data/learning_units.json",
    "data/material_overlays.json",
    "data/local_sources.json",
    "data/notes.json",
    "data/stars.json",
    "data/study_records.json",
    "data/ai_history.json",
    "data/connector_user_state.json",
    "data/whats_new_state.json",
)
BACKUP_MEMBERS = ("manifest.json", *BACKUP_DATA_MEMBERS)
BACKUP_STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
RESTORE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RESTORE_PLAN_VERSION = 1
BACKUP_REQUIRED_SCHEMA: dict[str, set[str]] = {
    "schema_migrations": {"version", "name", "applied_at"},
    "terms": {"id", "stable_id", "name", "archived", "sort_order", "created_at", "updated_at"},
    "courses": {
        "id", "stable_id", "source_kind", "metadata_locked", "term_id", "display_name", "name",
        "course_code", "code", "archived", "active", "sort_order", "created_at", "updated_at",
    },
    "weeks": {
        "id", "stable_id", "course_id", "origin", "week_label", "week_number", "kind", "archived",
        "created_at", "updated_at",
    },
    "files": {
        "id", "stable_id", "course_id", "week_id", "metadata_locked", "display_name", "filename",
        "material_type", "category", "exercise_type", "inbox", "active", "removed_at",
        "material_updated_at", "indexed_at", "import_mode", "original_path", "sha256", "hash",
        "file_size", "size", "source_missing",
    },
    "materials": {"stable_id", "legacy_file_id", "current_version_id"},
    "material_versions": {"id", "source_id"},
    "sources": {"id", "authority"},
    "notes": {"target_type", "target_id", "body", "created_at", "updated_at"},
    "stars": {"target_type", "target_id", "created_at"},
    "attempts": {"file_id", "answer_text", "result", "created_at"},
    "wrong_questions": {
        "course_id", "week_label", "source_file_id", "question_ref", "mistake", "concept", "attempts",
        "last_reviewed", "mastery", "created_at",
    },
    "bookmarks": {"target_type", "target_id", "label", "created_at"},
    "study_sessions": {"course_id", "week_label", "mode", "started_at", "ended_at"},
    "academic_change_events": {"id", "stable_id"},
    "academic_change_event_state": {"event_id", "seen_at", "updated_at"},
}
CREDENTIAL_TRANSPORT_FD_ENV = "STUDYHUB_CREDENTIAL_TRANSPORT_FD"
CREDENTIAL_TRANSPORT_PROTOCOL_ENV = "STUDYHUB_CREDENTIAL_PROTOCOL"
CREDENTIAL_TRANSPORT_KIND_ENV = "STUDYHUB_CREDENTIAL_TRANSPORT"
CREDENTIAL_PROTOCOL = "studyhub-credential/1"
CREDENTIAL_MAX_FRAME_BYTES = 8192
CHUNK_TARGET_CHARS = 3200
QUESTION_RE = re.compile(r"^\s*(?:Q(?:uestion)?\.?\s*)?(?P<num>\d{1,3}|[A-Z])[\).\:]\s+(?P<body>.{12,})", re.IGNORECASE)
SOLUTION_RE = re.compile(r"\b(solution|solutions|answer|answers|worked|key)\b", re.IGNORECASE)
SOLUTION_HEADING_RE = re.compile(
    r"^\s*(?:(?:Official|Teacher)\s+){0,2}(?:Solution|Solutions|Answer|Answers)\s*:?\s*$",
    re.IGNORECASE,
)
QUESTION_CATEGORY_RE = re.compile(r"\b(tutorial|workshop|lab|practice|revision|quiz)\b", re.IGNORECASE)
ASK_QUESTION_RE = re.compile(r"\bQ(?:uestion)?\.?\s*(?P<num>\d{1,3}|[A-Z])\b|题\s*(?P<cnum>\d{1,3})", re.IGNORECASE)
SOLUTION_INTENT_RE = re.compile(r"\b(solution|answer|solve|calculate|work(?:ed)?\s*out)\b|答案|解答|求解|计算", re.IGNORECASE)
QUESTION_INTENT_RE = re.compile(r"\b(question|tutorial|workshop|lab|quiz|practice)\b|题目|练习", re.IGNORECASE)
LOCAL_SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "current",
    "file",
    "for",
    "from",
    "explain",
    "generate",
    "give",
    "help",
    "important",
    "in",
    "is",
    "it",
    "key",
    "me",
    "of",
    "on",
    "or",
    "prepare",
    "selected",
    "summarize",
    "summary",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "why",
    "with",
}


class CredentialClientError(RuntimeError):
    pass


class CredentialClient:
    """Internal client for the private parent/child credential transport."""

    def __init__(self, sock: socket.socket, session: str):
        self._sock = sock
        self._session = session
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> "CredentialClient | None":
        fd_raw = os.environ.pop(CREDENTIAL_TRANSPORT_FD_ENV, "").strip()
        protocol = os.environ.pop(CREDENTIAL_TRANSPORT_PROTOCOL_ENV, "").strip()
        transport = os.environ.pop(CREDENTIAL_TRANSPORT_KIND_ENV, "").strip()
        if not fd_raw:
            return None
        if protocol != "1" or transport != "unix-fd":
            raise CredentialClientError("credential_transport_unavailable")
        try:
            fd = int(fd_raw)
        except ValueError as exc:
            raise CredentialClientError("credential_transport_unavailable") from exc
        try:
            sock = socket.socket(fileno=fd)
            sock.set_inheritable(False)
            greeting = cls._read_frame(sock)
            session = cls._parse_greeting(greeting)
            return cls(sock, session)
        except OSError as exc:
            raise CredentialClientError("credential_transport_unavailable") from exc

    @staticmethod
    def _read_exact(sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise CredentialClientError("credential_transport_closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @classmethod
    def _read_frame(cls, sock: socket.socket) -> str:
        length = int.from_bytes(cls._read_exact(sock, 4), "big")
        if length > CREDENTIAL_MAX_FRAME_BYTES:
            raise CredentialClientError("credential_response_oversized")
        try:
            return cls._read_exact(sock, length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialClientError("credential_response_malformed") from exc

    @staticmethod
    def _write_frame(sock: socket.socket, payload: str) -> None:
        data = payload.encode("utf-8")
        if len(data) > CREDENTIAL_MAX_FRAME_BYTES:
            raise CredentialClientError("credential_request_oversized")
        sock.sendall(len(data).to_bytes(4, "big") + data)

    @staticmethod
    def _parse_greeting(payload: str) -> str:
        parts = payload.split()
        if len(parts) != 2 or parts[0] != CREDENTIAL_PROTOCOL or not parts[1].startswith("session="):
            raise CredentialClientError("credential_response_malformed")
        session = parts[1].removeprefix("session=")
        if not re.fullmatch(r"[0-9a-f]{64}", session):
            raise CredentialClientError("credential_response_malformed")
        return session

    @staticmethod
    def _parse_response(payload: str) -> str:
        parts = payload.split()
        if len(parts) < 3 or parts[0] != CREDENTIAL_PROTOCOL:
            raise CredentialClientError("credential_response_malformed")
        if parts[1] == "err":
            code = next((part.removeprefix("code=") for part in parts[2:] if part.startswith("code=")), "operation_failed")
            raise CredentialClientError(code)
        if len(parts) != 3 or parts[1] != "ok" or not parts[2].startswith("credential_hex="):
            raise CredentialClientError("credential_response_malformed")
        encoded = parts[2].removeprefix("credential_hex=")
        try:
            return bytes.fromhex(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CredentialClientError("credential_response_malformed") from exc

    def with_canvas_default_credential(self, action: Callable[[str], Any]) -> Any:
        with self._lock:
            self._write_frame(self._sock, f"{CREDENTIAL_PROTOCOL} use canvas_default session={self._session}")
            credential = self._parse_response(self._read_frame(self._sock))
        return action(credential)


_CREDENTIAL_CLIENT: CredentialClient | None = None


CANVAS_RECORD_PREFIX = "studyhub-canvas-connection-v1"
CANVAS_ACCEPT_HEADER = "application/json+canvas-string-ids"
CANVAS_MAX_RESPONSE_BYTES = 1_000_000
CANVAS_MAX_PAGES = 10
CANVAS_MAX_ITEMS = 500
CANVAS_TIMEOUT_SECONDS = 10
CANVAS_ALLOWED_GET_PATHS = {"/api/v1/users/self", "/api/v1/courses"}
CANVAS_MAX_REMOTE_ID_LENGTH = 160
CANVAS_MAX_FILE_NAME_LENGTH = 512
CANVAS_MAX_CONTENT_TYPE_LENGTH = 255
CANVAS_MAX_TIMESTAMP_LENGTH = 128
CANVAS_MAX_FILE_SIZE = (1 << 63) - 1
CANVAS_MAX_ACQUISITION_BYTES = 256 * 1024 * 1024
CANVAS_MAX_APPLY_FILES = 25
CANVAS_MAX_DOWNLOAD_URL_LENGTH = 8192
CANVAS_MAX_DOWNLOAD_REDIRECTS = 5
CANVAS_SYNC_PLAN_VERSION = "canvas-sync-plan-v1"
CANVAS_REMOTE_REVISION_VERSION = "canvas-remote-revision-v1"
ACADEMIC_CHANGE_EVENT_VERSION = "academic-change-event-v1"
ACADEMIC_CHANGE_EVENT_KINDS = {
    "CANVAS_FILE_ADDED",
    "CANVAS_FILE_CONTENT_UPDATED",
    "CANVAS_FILE_METADATA_UPDATED",
}
WHATS_NEW_DEFAULT_LIMIT = 50
WHATS_NEW_MAX_LIMIT = 100
WHATS_NEW_MAX_MARK_SEEN = 100
_CANVAS_SYNC_APPLY_LOCK = threading.Lock()


class CanvasConnectionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CanvasConnectionRecord:
    origin: str
    access_token: str


@dataclass(frozen=True)
class CanvasHTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class CanvasIdentity:
    remote_user_id: str
    display_name: str


@dataclass(frozen=True)
class CanvasCourse:
    remote_course_id: str
    name: str
    course_code: str
    workflow_state: str
    start_at: str | None
    end_at: str | None
    term_id: str | None
    term_name: str | None


@dataclass(frozen=True)
class CanvasRemoteFile:
    remote_file_id: str
    remote_course_id: str
    display_name: str
    filename: str
    content_type: str
    byte_size: int | None
    remote_created_at: str | None
    remote_updated_at: str | None
    remote_modified_at: str | None
    locked: bool
    hidden: bool
    hidden_for_user: bool


@dataclass(frozen=True)
class CanvasFileAcquisitionMetadata:
    remote_file: CanvasRemoteFile
    download_url: str


@dataclass(frozen=True)
class CanvasAcquiredFile:
    remote_file: CanvasRemoteFile
    remote_file_stable_id: str
    retained_path: Path
    sha256: str
    byte_size: int
    extension: str
    created_retained_file: bool


class CanvasConnectorError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CanvasCrossOriginRedirect(urllib.error.HTTPError):
    pass


def parse_canvas_connection_record(raw: str) -> CanvasConnectionRecord:
    lines = raw.splitlines()
    if len(lines) != 3 or lines[0] != CANVAS_RECORD_PREFIX:
        raise CanvasConnectionError("malformed_canvas_connection")
    origin_hex = lines[1].removeprefix("origin_hex=")
    token_hex = lines[2].removeprefix("token_hex=")
    if not lines[1].startswith("origin_hex=") or not lines[2].startswith("token_hex="):
        raise CanvasConnectionError("malformed_canvas_connection")
    try:
        origin = bytes.fromhex(origin_hex).decode("utf-8")
        token = bytes.fromhex(token_hex).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CanvasConnectionError("malformed_canvas_connection") from exc
    normalized_origin = normalize_canvas_origin(origin)
    if not token.strip() or any(ch.isspace() or ord(ch) < 32 for ch in token) or len(token) > 4096:
        raise CanvasConnectionError("malformed_canvas_connection")
    return CanvasConnectionRecord(normalized_origin, token)


def normalize_canvas_origin(raw: str) -> str:
    if "access_token" in raw.lower():
        raise CanvasConnectionError("invalid_canvas_origin")
    parsed = urllib.parse.urlparse(raw.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise CanvasConnectionError("invalid_canvas_origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CanvasConnectionError("invalid_canvas_origin")
    if parsed.path not in {"", "/"}:
        raise CanvasConnectionError("invalid_canvas_origin")
    try:
        ipaddress.ip_address(parsed.hostname)
        raise CanvasConnectionError("invalid_canvas_origin")
    except ValueError:
        pass
    host = parsed.hostname.lower()
    return f"https://{host}:{parsed.port}" if parsed.port else f"https://{host}"


def same_origin(left: str, right: str) -> bool:
    a = urllib.parse.urlparse(left)
    b = urllib.parse.urlparse(right)
    return (
        a.scheme == b.scheme
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and (a.port or (443 if a.scheme == "https" else 80))
        == (b.port or (443 if b.scheme == "https" else 80))
    )


class NoAutomaticRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise CanvasCrossOriginRedirect(req.full_url, code, "canvas_redirect", headers, fp)


def default_canvas_http_get(url: str, headers: dict[str, str], timeout: int = CANVAS_TIMEOUT_SECONDS) -> CanvasHTTPResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoAutomaticRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(CANVAS_MAX_RESPONSE_BYTES + 1)
            return CanvasHTTPResponse(response.status, dict(response.headers.items()), body)
    except urllib.error.HTTPError as exc:
        body = exc.read(CANVAS_MAX_RESPONSE_BYTES + 1)
        return CanvasHTTPResponse(exc.code, dict(exc.headers.items()), body)
    except TimeoutError as exc:
        raise CanvasConnectorError("transport_failure") from exc
    except urllib.error.URLError as exc:
        raise CanvasConnectorError("transport_failure") from exc


def default_canvas_download_open(url: str, timeout: int = CANVAS_TIMEOUT_SECONDS):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "StudyHub Local Canvas acquisition"},
        method="GET",
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoAutomaticRedirect)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise CanvasConnectorError("canvas_download_failed") from exc


class CanvasConnector:
    def __init__(
        self,
        record: CanvasConnectionRecord,
        http_get: Callable[[str, dict[str, str], int], CanvasHTTPResponse] = default_canvas_http_get,
        max_pages: int = CANVAS_MAX_PAGES,
        max_items: int = CANVAS_MAX_ITEMS,
    ):
        self.record = record
        self.http_get = http_get
        self.max_pages = max_pages
        self.max_items = max_items

    def identity(self) -> CanvasIdentity:
        payload = self._get_json("/api/v1/users/self")
        if not isinstance(payload, dict):
            raise CanvasConnectorError("malformed_canvas_response")
        remote_id = payload.get("id")
        display_name = payload.get("name") or payload.get("short_name") or ""
        if remote_id is None or not isinstance(display_name, str):
            raise CanvasConnectorError("malformed_canvas_response")
        return CanvasIdentity(str(remote_id), display_name)

    def courses(self, enrollment_state: str = "active") -> list[CanvasCourse]:
        params = {"enrollment_state": enrollment_state, "per_page": "50"}
        payloads = self._get_paginated_json("/api/v1/courses", params=params)
        courses: list[CanvasCourse] = []
        for item in payloads:
            if not isinstance(item, dict):
                raise CanvasConnectorError("malformed_canvas_response")
            remote_id = item.get("id")
            name = item.get("name") or ""
            if remote_id is None or not isinstance(name, str):
                raise CanvasConnectorError("malformed_canvas_response")
            term = item.get("term") if isinstance(item.get("term"), dict) else {}
            courses.append(
                CanvasCourse(
                    remote_course_id=str(remote_id),
                    name=name,
                    course_code=str(item.get("course_code") or ""),
                    workflow_state=str(item.get("workflow_state") or ""),
                    start_at=item.get("start_at") if isinstance(item.get("start_at"), str) else None,
                    end_at=item.get("end_at") if isinstance(item.get("end_at"), str) else None,
                    term_id=str(term.get("id")) if term.get("id") is not None else None,
                    term_name=term.get("name") if isinstance(term.get("name"), str) else None,
                )
            )
        return courses

    def list_course_files(self, remote_course_id: str) -> list[CanvasRemoteFile]:
        course_id = normalize_canvas_opaque_id(remote_course_id, "remote course id")
        encoded_course_id = urllib.parse.quote(course_id, safe="")
        path = f"/api/v1/courses/{encoded_course_id}/files"
        payloads = self._get_paginated_json(path, params={"per_page": "50"})
        files: list[CanvasRemoteFile] = []
        seen_remote_file_ids: set[str] = set()
        for item in payloads:
            remote_file = normalize_canvas_file_object(item, course_id)
            if remote_file.remote_file_id in seen_remote_file_ids:
                raise CanvasConnectorError("duplicate_canvas_file_id")
            seen_remote_file_ids.add(remote_file.remote_file_id)
            files.append(remote_file)
        return files

    def get_course_file(self, remote_course_id: str, remote_file_id: str) -> CanvasFileAcquisitionMetadata:
        course_id = normalize_canvas_opaque_id(remote_course_id, "remote course id")
        file_id = normalize_canvas_opaque_id(remote_file_id, "remote file id")
        encoded_course_id = urllib.parse.quote(course_id, safe="")
        encoded_file_id = urllib.parse.quote(file_id, safe="")
        payload = self._get_json(f"/api/v1/courses/{encoded_course_id}/files/{encoded_file_id}")
        remote_file = normalize_canvas_file_object(payload, course_id)
        if remote_file.remote_file_id != file_id:
            raise CanvasConnectorError("canvas_file_identity_mismatch")
        if not isinstance(payload, dict):
            raise CanvasConnectorError("malformed_canvas_response")
        download_url = normalize_canvas_text(
            payload.get("url"), maximum=CANVAS_MAX_DOWNLOAD_URL_LENGTH, required=True
        )
        validate_canvas_download_url(download_url)
        return CanvasFileAcquisitionMetadata(remote_file, download_url)

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = self._build_url(path, params or {})
        response = self._request_url(url)
        return self._decode_json(response)

    def _get_paginated_json(self, path: str, params: dict[str, str] | None = None) -> list[Any]:
        url = self._build_url(path, params or {})
        seen: set[str] = set()
        items: list[Any] = []
        for _page in range(self.max_pages):
            if url in seen:
                raise CanvasConnectorError("pagination_loop")
            seen.add(url)
            response = self._request_url(url)
            payload = self._decode_json(response)
            if not isinstance(payload, list):
                raise CanvasConnectorError("malformed_canvas_response")
            items.extend(payload)
            if len(items) > self.max_items:
                raise CanvasConnectorError("pagination_limit_exceeded")
            next_url = next_canvas_link(response.headers, "next")
            if not next_url:
                return items
            if not same_origin(self.record.origin, next_url):
                raise CanvasConnectorError("cross_origin_pagination")
            url = next_url
        raise CanvasConnectorError("pagination_limit_exceeded")

    def _build_url(self, path: str, params: dict[str, str]) -> str:
        if (
            path not in CANVAS_ALLOWED_GET_PATHS
            and not re.fullmatch(r"/api/v1/courses/[^/]+/files", path)
            and not re.fullmatch(r"/api/v1/courses/[^/]+/files/[^/]+", path)
        ):
            raise CanvasConnectorError("unsupported_canvas_endpoint")
        query = urllib.parse.urlencode(params)
        return f"{self.record.origin}{path}" + (f"?{query}" if query else "")

    def _request_url(self, url: str) -> CanvasHTTPResponse:
        if not same_origin(self.record.origin, url):
            raise CanvasConnectorError("origin_mismatch")
        headers = {
            "Accept": CANVAS_ACCEPT_HEADER,
            "Authorization": f"Bearer {self.record.access_token}",
            "User-Agent": "StudyHub Local Canvas discovery",
        }
        try:
            response = self.http_get(url, headers, CANVAS_TIMEOUT_SECONDS)
        except CanvasConnectorError:
            raise
        except Exception as exc:
            raise CanvasConnectorError("transport_failure") from exc
        if len(response.body) > CANVAS_MAX_RESPONSE_BYTES:
            raise CanvasConnectorError("response_too_large")
        return self._classify_response(response)

    def _classify_response(self, response: CanvasHTTPResponse) -> CanvasHTTPResponse:
        if 200 <= response.status < 300:
            return response
        if response.status == 301 or response.status == 302 or response.status == 303 or response.status == 307 or response.status == 308:
            location = header_value(response.headers, "location")
            if location:
                next_url = urllib.parse.urljoin(self.record.origin, location)
                if not same_origin(self.record.origin, next_url):
                    raise CanvasConnectorError("cross_origin_redirect")
            raise CanvasConnectorError("canvas_unavailable")
        if response.status == 401:
            raise CanvasConnectorError("authentication_failed")
        if response.status == 403:
            raise CanvasConnectorError("permission_denied")
        if response.status == 429:
            raise CanvasConnectorError("rate_limited")
        if response.status >= 500:
            raise CanvasConnectorError("canvas_unavailable")
        raise CanvasConnectorError("canvas_api_error")

    def _decode_json(self, response: CanvasHTTPResponse) -> Any:
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CanvasConnectorError("malformed_canvas_response") from exc


def header_value(headers: dict[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def next_canvas_link(headers: dict[str, str], relation: str) -> str | None:
    link = header_value(headers, "link")
    if not link:
        return None
    for part in link.split(","):
        match = re.search(r"<([^>]+)>\s*;\s*rel=\"?([^\";]+)\"?", part.strip(), re.IGNORECASE)
        if match and match.group(2).lower() == relation.lower():
            return match.group(1)
    return None


def validate_canvas_download_url(raw_url: str) -> str:
    if not isinstance(raw_url, str) or not raw_url or len(raw_url) > CANVAS_MAX_DOWNLOAD_URL_LENGTH:
        raise CanvasConnectorError("unsafe_canvas_download_url")
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CanvasConnectorError("unsafe_canvas_download_url")
    host = parsed.hostname.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise CanvasConnectorError("unsafe_canvas_download_url")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise CanvasConnectorError("unsafe_canvas_download_url")
    return raw_url


def stream_canvas_download(
    download_url: str,
    staging_path: Path,
    *,
    declared_size: int | None,
    open_url: Callable[[str, int], Any] = default_canvas_download_open,
    max_bytes: int = CANVAS_MAX_ACQUISITION_BYTES,
    max_redirects: int = CANVAS_MAX_DOWNLOAD_REDIRECTS,
) -> tuple[str, int]:
    if declared_size is not None and declared_size > max_bytes:
        raise CanvasConnectorError("canvas_file_too_large")
    current_url = validate_canvas_download_url(download_url)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging_path.touch(mode=0o600, exist_ok=False)
        os.chmod(staging_path, 0o600)
        redirects = 0
        while True:
            response = open_url(current_url, CANVAS_TIMEOUT_SECONDS)
            try:
                response_status = getattr(response, "status", None)
                status = int(response_status if response_status is not None else response.getcode())
                headers = dict(response.headers.items()) if hasattr(response.headers, "items") else dict(response.headers)
                if status in {301, 302, 303, 307, 308}:
                    location = header_value(headers, "location")
                    if not location or redirects >= max_redirects:
                        raise CanvasConnectorError("canvas_download_redirect_limit")
                    current_url = validate_canvas_download_url(urllib.parse.urljoin(current_url, location))
                    redirects += 1
                    continue
                if status < 200 or status >= 300:
                    raise CanvasConnectorError("canvas_download_failed")
                digest = hashlib.sha256()
                byte_count = 0
                with staging_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if byte_count > max_bytes:
                            raise CanvasConnectorError("canvas_file_too_large")
                        digest.update(chunk)
                        output.write(chunk)
                if declared_size is not None and byte_count != declared_size:
                    raise CanvasConnectorError("canvas_file_size_mismatch")
                return digest.hexdigest(), byte_count
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise


def normalize_canvas_opaque_id(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CanvasConnectorError("malformed_canvas_response")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > CANVAS_MAX_REMOTE_ID_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise CanvasConnectorError("malformed_canvas_response")
    return normalized


def normalize_canvas_text(value: Any, *, maximum: int, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise CanvasConnectorError("malformed_canvas_response")
    if len(value) > maximum or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise CanvasConnectorError("malformed_canvas_response")
    if required and not value.strip():
        raise CanvasConnectorError("malformed_canvas_response")
    return value


def normalize_canvas_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    text = normalize_canvas_text(value, maximum=CANVAS_MAX_TIMESTAMP_LENGTH)
    return text or None


def normalize_canvas_boolean(item: dict[str, Any], key: str) -> bool:
    value = item.get(key, False)
    if not isinstance(value, bool):
        raise CanvasConnectorError("malformed_canvas_response")
    return value


def normalize_canvas_file_object(item: Any, remote_course_id: str) -> CanvasRemoteFile:
    if not isinstance(item, dict):
        raise CanvasConnectorError("malformed_canvas_response")
    remote_file_id = normalize_canvas_opaque_id(item.get("id"), "remote file id")
    display_name = normalize_canvas_text(item.get("display_name", ""), maximum=CANVAS_MAX_FILE_NAME_LENGTH)
    filename = normalize_canvas_text(item.get("filename", ""), maximum=CANVAS_MAX_FILE_NAME_LENGTH, required=True)
    content_type = normalize_canvas_text(
        item.get("content-type", item.get("content_type", "")),
        maximum=CANVAS_MAX_CONTENT_TYPE_LENGTH,
    )
    size = item.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > CANVAS_MAX_FILE_SIZE):
        raise CanvasConnectorError("malformed_canvas_response")
    return CanvasRemoteFile(
        remote_file_id=remote_file_id,
        remote_course_id=normalize_canvas_opaque_id(remote_course_id, "remote course id"),
        display_name=display_name,
        filename=filename,
        content_type=content_type,
        byte_size=size,
        remote_created_at=normalize_canvas_timestamp(item.get("created_at")),
        remote_updated_at=normalize_canvas_timestamp(item.get("updated_at")),
        remote_modified_at=normalize_canvas_timestamp(item.get("modified_at")),
        locked=normalize_canvas_boolean(item, "locked"),
        hidden=normalize_canvas_boolean(item, "hidden"),
        hidden_for_user=normalize_canvas_boolean(item, "hidden_for_user"),
    )


def canvas_connection_record_from_private_transport() -> CanvasConnectionRecord:
    client = initialize_credential_client()
    if client is None:
        raise CanvasConnectorError("connection_not_configured")
    try:
        return client.with_canvas_default_credential(parse_canvas_connection_record)
    except CredentialClientError as exc:
        code = str(exc)
        if code == "credential_missing":
            raise CanvasConnectorError("credential_missing") from exc
        if code == "credential_backend_unavailable":
            raise CanvasConnectorError("credential_backend_unavailable") from exc
        if code == "credential_access_denied":
            raise CanvasConnectorError("credential_access_denied") from exc
        raise CanvasConnectorError("credential_operation_failed") from exc
    except CanvasConnectionError as exc:
        raise CanvasConnectorError(exc.code) from exc


def with_canvas_connector(action: Callable[[CanvasConnector], Any]) -> Any:
    record = canvas_connection_record_from_private_transport()
    return action(CanvasConnector(record))


def public_canvas_identity(identity: CanvasIdentity) -> dict[str, str]:
    return {
        "remote_user_id": identity.remote_user_id,
        "display_name": identity.display_name,
    }


def public_canvas_course(course: CanvasCourse) -> dict[str, Any]:
    return {
        "remote_course_id": course.remote_course_id,
        "name": course.name,
        "course_code": course.course_code,
        "workflow_state": course.workflow_state,
        "start_at": course.start_at,
        "end_at": course.end_at,
        "term": {"id": course.term_id, "name": course.term_name}
        if course.term_id or course.term_name
        else None,
    }


def canvas_authority_id(normalized_origin: str) -> str:
    origin = normalize_canvas_origin(normalized_origin)
    return hashlib.sha256(f"canvas\0{origin}".encode("utf-8")).hexdigest()


def canvas_course_offering_stable_id(authority_id: str, remote_course_id: str) -> str:
    return deterministic_stable_id(
        "canvas_course_offering",
        f"v1\0canvas\0{authority_id}\0{remote_course_id}",
    )


def canvas_course_map(courses: list[CanvasCourse]) -> dict[str, CanvasCourse]:
    return {str(course.remote_course_id): course for course in courses}


def initialize_credential_client() -> CredentialClient | None:
    global _CREDENTIAL_CLIENT
    if _CREDENTIAL_CLIENT is None:
        _CREDENTIAL_CLIENT = CredentialClient.from_environment()
    return _CREDENTIAL_CLIENT

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
MATERIAL_TYPES = (
    "lecture",
    "tutorial",
    "workshop",
    "lab",
    "quiz",
    "assignment",
    "reading",
    "exam",
    "other",
)
MATERIAL_TYPE_LABELS = {item: item.title() for item in MATERIAL_TYPES}
EXERCISE_MATERIAL_TYPES = {"tutorial", "workshop", "lab", "quiz", "assignment", "exam"}
SYSTEM_INBOX_FOLDER = "__studyhub_unclassified__"
TEXT_EXTRACTABLE_EXTS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".csv",
    ".tsv",
    ".txt",
    ".md",
    ".py",
    ".r",
    ".html",
    ".htm",
    ".xhtml",
    ".xml",
    ".svg",
    ".ipynb",
}
ACTIVE_WEB_PREVIEW_EXTS = {".html", ".htm", ".xhtml", ".xml", ".svg"}
OFFICE_PDF_PREVIEW_EXTS = {".doc", ".docx", ".ppt", ".pptx"}
SPREADSHEET_PREVIEW_EXTS = {".xls", ".xlsx"}
TEXT_PREVIEW_EXTS = {".csv", ".tsv", ".txt", ".md", ".py", ".r", ".ipynb"}
IMAGE_PREVIEW_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
OFFICE_PREVIEW_TIMEOUT_SECONDS = int(os.environ.get("STUDYHUB_OFFICE_PREVIEW_TIMEOUT", "60"))
_OFFICE_PREVIEW_LOCKS: dict[str, threading.Lock] = {}
_OFFICE_PREVIEW_LOCKS_GUARD = threading.Lock()
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
    for path in (DATA_DIR, DATA_DIR / "managed-library", CACHE_DIR, TEXT_CACHE_DIR, preview_cache_dir(), LOG_DIR):
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


def preview_cache_dir() -> Path:
    return CACHE_DIR / "previews"


def canvas_remote_content_root() -> Path:
    return DATA_DIR / "remote-content" / "canvas"


def canvas_acquisition_staging_root() -> Path:
    return CACHE_DIR / "canvas-acquisition"


def text_cache_filename(digest: str) -> str:
    return f"{digest}-{TEXT_EXTRACTION_VERSION}.txt"


def expected_text_cache_path(digest: str) -> Path:
    return TEXT_CACHE_DIR / text_cache_filename(digest)


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
    payload = {
        key: row[key]
        for key in (
            "id",
            "stable_id",
            "code",
            "course_code",
            "name",
            "display_name",
            "folder_name",
            "term_id",
            "term_name",
            "archived",
            "source_kind",
            "sort_order",
            "created_at",
            "updated_at",
            "file_count",
            "latest_week",
        )
        if key in row.keys()
    }
    payload["code"] = payload.get("course_code") or payload.get("code") or ""
    payload["name"] = payload.get("display_name") or payload.get("name") or payload["code"]
    payload["display_code"] = display_course_code(payload["code"])
    return payload


def public_week(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "stable_id",
            "course_id",
            "week_label",
            "week_number",
            "kind",
            "origin",
            "archived",
            "has_materials",
            "file_count",
        )
        if key in row.keys()
    }


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
        "display_name",
        "material_type",
        "import_mode",
        "inbox",
        "metadata_locked",
        "source_missing",
        "removed_at",
        "material_created_at",
        "material_updated_at",
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
    if "course_code" in data:
        data["display_course_code"] = display_course_code(data["course_code"])
    if include_text is not None:
        data["extractedText"] = include_text
    return data


def clean_metadata_text(value: Any, field: str, *, required: bool = True, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit or any(ord(char) < 32 for char in text):
        raise ValueError(f"{field} is not valid")
    return text


def new_stable_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def deterministic_stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"


def normalize_material_type(value: Any) -> str:
    raw = clean_metadata_text(value or "other", "Material type", required=False, limit=40) or "other"
    aliases = {
        "slides": "lecture",
        "course materials": "lecture",
        "practice": "quiz",
        "revision": "other",
        "readings": "reading",
    }
    lowered = raw.lower()
    if lowered in MATERIAL_TYPES:
        return lowered
    return aliases.get(lowered, "other")


def material_type_label(material_type: str) -> str:
    return MATERIAL_TYPE_LABELS.get(normalize_material_type(material_type), "Other")


def section_for_material_type(material_type: str) -> str:
    return "02 Exercises" if material_type in EXERCISE_MATERIAL_TYPES else "01 Course Materials"


def material_path_allowed(row: sqlite3.Row | dict[str, Any], path: Path) -> bool:
    resolved = path.expanduser().resolve()
    keys = row.keys()
    import_mode = str(row["import_mode"] or "") if "import_mode" in keys else ""
    stored = Path(str(row["original_path"])).expanduser().resolve()
    if import_mode == "canvas_remote":
        return resolved == stored and is_inside(resolved, canvas_remote_content_root())
    if is_inside(resolved, DEFAULT_STUDY_ROOT):
        return True
    if import_mode != "reference":
        return False
    return resolved == stored


def public_solution(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "id",
        "file_id",
        "course_code",
        "week_label",
        "exercise_type",
        "solution_label",
        "source_location",
        "official_source",
        "created_at",
        "filename",
        "rel_path",
    )
    data = {key: row[key] for key in allowed if key in row.keys()}
    if "course_code" in data:
        data["display_course_code"] = display_course_code(data["course_code"])
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


def extract_learning_unit(parts: list[str]) -> tuple[str, int | None, str]:
    week_label, week_number = extract_week(parts)
    if week_label:
        return week_label, week_number, "week"
    for part in parts:
        match = re.match(r"^\s*Module[\s_-]*(?P<num>\d{1,3})?\b.*$", part, re.IGNORECASE)
        if match:
            number = int(match.group("num")) if match.group("num") else None
            return part, number, "module"
    return "Unclassified", None, "module"


def parse_course_dir(path: Path) -> tuple[str, str]:
    m = COURSE_RE.match(path.name)
    if m:
        return m.group("code"), m.group("name")
    return path.name, path.name


def display_course_code(code: str) -> str:
    codes = re.findall(r"\b([A-Z]{3,5})(\d{4})\b", code or "")
    if len(codes) <= 1:
        return code
    prefix = codes[0][0]
    if all(course_prefix == prefix for course_prefix, _number in codes):
        return f"{prefix}" + "/".join(number for _course_prefix, number in codes)
    return " / ".join(f"{course_prefix}{number}" for course_prefix, number in codes)


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


def configured_tool(name: str, env_name: str) -> str:
    raw = os.environ.get(env_name, "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            candidate = candidate / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    poppler_bin = os.environ.get("STUDYHUB_POPPLER_BIN", "").strip()
    if poppler_bin:
        candidate = Path(poppler_bin).expanduser() / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path.home() / "scoop" / "apps" / "poppler" / "current" / "Library" / "bin" / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def executable_file(path: Path) -> str:
    candidate = path.expanduser()
    return str(candidate) if candidate.exists() and os.access(candidate, os.X_OK) else ""


def office_converter_path() -> str:
    for env_name in ("STUDYHUB_OFFICE_CONVERTER", "STUDYHUB_SOFFICE", "STUDYHUB_LIBREOFFICE"):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        direct = executable_file(Path(raw))
        if direct:
            return direct
        found = shutil.which(raw)
        if found:
            return found

    for name in ("soffice", "libreoffice", "soffice.exe", "libreoffice.exe"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path("/usr/bin/libreoffice"),
        Path("/usr/local/bin/libreoffice"),
        Path("/opt/homebrew/bin/libreoffice"),
        Path("/usr/bin/soffice"),
        Path("/usr/local/bin/soffice"),
        Path("/opt/homebrew/bin/soffice"),
    ]
    for root_env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(root_env, "").strip()
        if root:
            candidates.append(Path(root) / "LibreOffice" / "program" / "soffice.exe")
    for candidate in candidates:
        direct = executable_file(candidate)
        if direct:
            return direct
    return ""


_OFFICE_CONVERTER_VERSION_CACHE: dict[str, str] = {}


def office_converter_version(converter: str) -> str:
    if not converter:
        return "missing"
    if converter in _OFFICE_CONVERTER_VERSION_CACHE:
        return _OFFICE_CONVERTER_VERSION_CACHE[converter]
    try:
        res = subprocess.run([converter, "--version"], text=True, capture_output=True, timeout=10)
        version = (res.stdout or res.stderr or "").strip() or Path(converter).name
    except Exception:
        version = Path(converter).name
    _OFFICE_CONVERTER_VERSION_CACHE[converter] = version[:200]
    return _OFFICE_CONVERTER_VERSION_CACHE[converter]


def office_preview_dependency_error() -> str:
    if office_converter_path():
        return ""
    return "Office visual preview requires LibreOffice. Install LibreOffice to preview PowerPoint and Word files as slides/pages."


@dataclass(frozen=True)
class OfficePreviewResult:
    status: str
    path: Path | None = None
    cached: bool = False
    message: str = ""


def preview_lock_for(key: str) -> threading.Lock:
    with _OFFICE_PREVIEW_LOCKS_GUARD:
        lock = _OFFICE_PREVIEW_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OFFICE_PREVIEW_LOCKS[key] = lock
        return lock


def office_preview_cache_key(row: sqlite3.Row | dict[str, Any], source_path: Path, converter: str) -> str:
    digest = row["sha256"] or row["hash"] or sha256_file(source_path)
    payload = f"{row['id']}|{source_path.suffix.lower()}|{digest}|{office_converter_version(converter)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def remove_stale_preview_derivatives(file_id: int, keep_key: str) -> None:
    root = preview_cache_dir()
    if not root.exists():
        return
    keep_pdf = f"file-{file_id}-{keep_key}.pdf"
    keep_meta = f"file-{file_id}-{keep_key}.json"
    for path in root.glob(f"file-{file_id}-*"):
        if path.name not in {keep_pdf, keep_meta}:
            try:
                path.unlink()
            except OSError:
                pass


def convert_office_document_to_pdf(row: sqlite3.Row | dict[str, Any], source_path: Path) -> OfficePreviewResult:
    ext = source_path.suffix.lower()
    if ext not in OFFICE_PDF_PREVIEW_EXTS:
        return OfficePreviewResult("unsupported", message="This file type does not use Office-to-PDF visual preview.")
    if not material_path_allowed(row, source_path):
        raise PermissionError("Preview source is outside the StudyHub material allowlist.")
    if not source_path.exists():
        raise FileNotFoundError("Original file is missing. Scan Library to refresh your file list.")

    converter = office_converter_path()
    if not converter:
        return OfficePreviewResult("missing_converter", message=office_preview_dependency_error())

    ensure_dirs()
    key = office_preview_cache_key(row, source_path, converter)
    cache_root = preview_cache_dir()
    pdf_path = cache_root / f"file-{row['id']}-{key}.pdf"
    meta_path = cache_root / f"file-{row['id']}-{key}.json"
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        remove_stale_preview_derivatives(int(row["id"]), key)
        return OfficePreviewResult("converted", pdf_path, cached=True)

    lock = preview_lock_for(f"file-{row['id']}-{key}")
    with lock:
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            remove_stale_preview_derivatives(int(row["id"]), key)
            return OfficePreviewResult("converted", pdf_path, cached=True)
        with tempfile.TemporaryDirectory(prefix=f"office-preview-{row['id']}-", dir=cache_root) as tmp_name:
            tmp = Path(tmp_name)
            out_dir = tmp / "out"
            profile_dir = tmp / "profile"
            out_dir.mkdir()
            profile_dir.mkdir()
            cmd = [
                converter,
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(source_path),
            ]
            try:
                res = subprocess.run(cmd, text=True, capture_output=True, timeout=OFFICE_PREVIEW_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return OfficePreviewResult("timeout", message="StudyHub timed out while creating the Office visual preview.")
            if res.returncode != 0:
                return OfficePreviewResult("conversion_failed", message="StudyHub could not create an Office visual preview.")

            expected = out_dir / f"{source_path.stem}.pdf"
            outputs = [expected] if expected.exists() else sorted(out_dir.glob("*.pdf"))
            converted = next((item for item in outputs if item.exists() and item.stat().st_size > 0), None)
            if converted is None:
                return OfficePreviewResult("conversion_failed", message="StudyHub could not create an Office visual preview.")
            tmp_pdf = cache_root / f".file-{row['id']}-{key}.{threading.get_ident()}.tmp.pdf"
            shutil.copyfile(converted, tmp_pdf)
            os.replace(tmp_pdf, pdf_path)
            meta_path.write_text(
                json.dumps(
                    {
                        "file_id": row["id"],
                        "source_sha256": row["sha256"] or row["hash"] or "",
                        "source_extension": ext,
                        "converter": Path(converter).name,
                        "converter_version": office_converter_version(converter),
                        "created_at": now_iso(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            remove_stale_preview_derivatives(int(row["id"]), key)
            return OfficePreviewResult("converted", pdf_path, cached=False)


def pdf_extraction_dependency_error() -> str:
    if configured_tool("pdftotext", "STUDYHUB_PDFTOTEXT"):
        return ""
    return "PDF text extraction unavailable: pdftotext was not found. Install Poppler and rescan the library."


def extraction_error_for(path: Path, text: str) -> str:
    if text:
        return ""
    if path.suffix.lower() == ".pdf":
        dependency_error = pdf_extraction_dependency_error()
        if dependency_error:
            return dependency_error
    if path.suffix.lower() in TEXT_EXTRACTABLE_EXTS:
        return "No extracted text"
    return ""


def extract_text_from_pdf(path: Path) -> str:
    pdftotext = configured_tool("pdftotext", "STUDYHUB_PDFTOTEXT")
    if pdftotext:
        return run_text_command([pdftotext, "-layout", str(path), "-"], timeout=30)[:200_000]
    return ""


def pdf_page_count(path: Path) -> int:
    pdfinfo = configured_tool("pdfinfo", "STUDYHUB_PDFINFO")
    if not pdfinfo:
        return 0
    out = run_text_command([pdfinfo, str(path)], timeout=10)
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else 0


def extract_pdf_page_text(path: Path, page: int) -> str:
    pdftotext = configured_tool("pdftotext", "STUDYHUB_PDFTOTEXT")
    if not pdftotext:
        return ""
    return run_text_command([pdftotext, "-layout", "-f", str(page), "-l", str(page), str(path), "-"], timeout=15)


PML_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def collapse_inline_whitespace(text: str) -> str:
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", text).strip()


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


def pptx_slide_names(path: Path) -> list[str]:
    if not zipfile.is_zipfile(path):
        return []
    try:
        with zipfile.ZipFile(path) as zf:
            presentation = ElementTree.fromstring(zf.read("ppt/presentation.xml"))
            rels = ElementTree.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
            targets = {
                rel.attrib.get("Id"): rel.attrib.get("Target", "")
                for rel in rels.findall(f"{PKG_REL_NS}Relationship")
                if "slide" in rel.attrib.get("Type", "")
            }
            names: list[str] = []
            for slide_id in presentation.findall(f".//{PML_NS}sldId"):
                rid = slide_id.attrib.get(f"{R_NS}id")
                target = targets.get(rid, "")
                if not target:
                    continue
                normalized = target.lstrip("/")
                if not normalized.startswith("ppt/"):
                    normalized = f"ppt/{normalized}"
                if normalized.startswith("ppt/slides/") and normalized.endswith(".xml") and normalized not in names:
                    names.append(normalized)
            if names:
                return names
    except Exception:
        pass

    def slide_sort_key(name: str) -> tuple[int, str]:
        match = re.search(r"slide(\d+)\.xml$", name)
        return (int(match.group(1)) if match else 10_000_000, name)

    with zipfile.ZipFile(path) as zf:
        return sorted((name for name in zf.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")), key=slide_sort_key)


def pptx_paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for child in paragraph:
        if child.tag == f"{A_NS}r":
            text = "".join(node.text or "" for node in child.findall(f"{A_NS}t"))
            if text:
                pieces.append(text)
        elif child.tag == f"{A_NS}fld":
            text = "".join(node.text or "" for node in child.findall(f"{A_NS}t"))
            if text:
                pieces.append(text)
        elif child.tag == f"{A_NS}br":
            pieces.append("\n")
    text = collapse_inline_whitespace("".join(pieces))
    if not text:
        return ""
    ppr = paragraph.find(f"{A_NS}pPr")
    bullet = ppr is not None and (
        ppr.find(f"{A_NS}buChar") is not None
        or ppr.find(f"{A_NS}buAutoNum") is not None
        or ppr.find(f"{A_NS}buBlip") is not None
    )
    if bullet and not text.startswith(("•", "-", "*")):
        text = f"• {text}"
    return text


def pptx_slide_text_items(path: Path) -> list[tuple[str, str]]:
    if not zipfile.is_zipfile(path):
        return []
    items: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        for name in pptx_slide_names(path):
            try:
                root = ElementTree.fromstring(zf.read(name))
            except Exception:
                continue
            paragraphs = [pptx_paragraph_text(paragraph) for paragraph in root.iter(f"{A_NS}p")]
            text = "\n".join(paragraph for paragraph in paragraphs if paragraph)
            if text:
                items.append((name, text))
    return items


def docx_paragraph_items(path: Path) -> list[tuple[str, str]]:
    if not zipfile.is_zipfile(path):
        return []
    items: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            root = ElementTree.fromstring(zf.read("word/document.xml"))
            for idx, paragraph in enumerate(root.iter(f"{W_NS}p"), start=1):
                pieces: list[str] = []
                for child in paragraph.iter():
                    if child.tag == f"{W_NS}t" and child.text:
                        pieces.append(child.text)
                    elif child.tag == f"{W_NS}tab":
                        pieces.append("\t")
                    elif child.tag == f"{W_NS}br":
                        pieces.append("\n")
                text = collapse_inline_whitespace("".join(pieces))
                if text:
                    items.append((f"paragraph-{idx}", text))
    except Exception:
        return []
    return items


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
            items = docx_paragraph_items(path)
            return "\n".join(text for _name, text in items)[:200_000] if items else xml_text_from_zip(path, ("word/",))
        if ext == ".pptx":
            slide_items = pptx_slide_text_items(path)
            if slide_items:
                return "\n\n".join(f"Slide {idx}\n\n{text}" for idx, (_name, text) in enumerate(slide_items, start=1))[:200_000]
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
        slide_items = pptx_slide_text_items(path) or xml_text_items_from_zip(path, "ppt/slides/")
        for idx, (_name, text) in enumerate(slide_items, start=1):
            chunks = chunk_plain_text(text, f"Slide {idx}", slide_start=idx)
            out.extend(chunks)
        for i, chunk in enumerate(out):
            chunk["chunk_index"] = i
        return out
    if ext == ".docx":
        items = docx_paragraph_items(path) or xml_text_items_from_zip(path, "word/")
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
                if QUESTION_RE.match(stripped) or re.match(r"^(Source note|Note)\b", stripped, re.IGNORECASE) or SOLUTION_HEADING_RE.match(stripped):
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


def detect_solution_headings_from_chunks(file_row: sqlite3.Row | dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    solutions: list[dict[str, Any]] = []
    for chunk in chunks:
        lines = chunk["text"].splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not SOLUTION_HEADING_RE.match(stripped):
                continue
            following = [candidate.strip() for candidate in lines[idx + 1 : idx + 4] if candidate.strip()]
            if not following:
                continue
            solutions.append(
                {
                    "file_id": file_row["id"],
                    "course_code": file_row["course_code"],
                    "week_label": file_row["week_label"],
                    "exercise_type": file_row["exercise_type"] or file_row["category"],
                    "solution_label": stripped,
                    "source_location": chunk["source_location"] or file_row["filename"],
                    "official_source": 1 if file_row["is_official"] else 0,
                }
            )
    return solutions


def connect_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def validate_backup_db_schema(conn: sqlite3.Connection) -> None:
    table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    available_tables = {str(row["name"]) for row in table_rows}
    if not set(BACKUP_REQUIRED_SCHEMA).issubset(available_tables):
        raise ValueError("Backup requires an initialized, compatible StudyHub database")
    for table, required_columns in BACKUP_REQUIRED_SCHEMA.items():
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not required_columns.issubset(columns):
            raise ValueError("Backup requires an initialized, compatible StudyHub database")


def connect_backup_db_readonly() -> sqlite3.Connection:
    database_path = DB_PATH.resolve()
    if not database_path.is_file():
        raise FileNotFoundError("StudyHub database is not initialized")
    conn = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        validate_backup_db_schema(conn)
        return conn
    except Exception:
        conn.close()
        raise


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def apply_schema_migration(
    conn: sqlite3.Connection,
    version: int,
    name: str,
    migration: Callable[[sqlite3.Connection], None],
) -> bool:
    """Apply one ordered schema migration and record it only after success."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
        return False
    savepoint = f"schema_migration_{version}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        migration(conn)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, now_iso()),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return True


def phase1_domain_foundation_migration(conn: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS blobs (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          sha256 TEXT NOT NULL UNIQUE,
          byte_size INTEGER NOT NULL DEFAULT 0,
          mime_type TEXT DEFAULT '',
          extension TEXT DEFAULT '',
          local_locator TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sources (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          provider_kind TEXT NOT NULL,
          authority TEXT NOT NULL,
          source_label TEXT DEFAULT '',
          source_type TEXT DEFAULT '',
          import_mode TEXT DEFAULT '',
          local_locator TEXT DEFAULT '',
          connector_id TEXT DEFAULT '',
          account_id TEXT DEFAULT '',
          remote_id TEXT DEFAULT '',
          remote_url TEXT DEFAULT '',
          remote_updated_at TEXT DEFAULT '',
          fetched_at TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS materials (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          legacy_file_id INTEGER UNIQUE REFERENCES files(id) ON DELETE CASCADE,
          course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
          week_id INTEGER REFERENCES weeks(id) ON DELETE SET NULL,
          course_code TEXT DEFAULT '',
          course_name TEXT DEFAULT '',
          week_label TEXT DEFAULT '',
          week_number INTEGER,
          material_type TEXT DEFAULT '',
          display_name TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          removed_at TEXT,
          current_version_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS material_versions (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
          source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
          blob_id INTEGER REFERENCES blobs(id) ON DELETE SET NULL,
          legacy_file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
          legacy_file_version_id INTEGER REFERENCES file_versions(id) ON DELETE SET NULL,
          sha256 TEXT NOT NULL,
          byte_size INTEGER NOT NULL DEFAULT 0,
          modified_at TEXT DEFAULT '',
          indexed_at TEXT DEFAULT '',
          text_cache_path TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          source_missing INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(material_id, sha256)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_materials_course_week ON materials(course_id, week_id, active)",
        "CREATE INDEX IF NOT EXISTS idx_material_versions_material_active ON material_versions(material_id, active)",
        "CREATE INDEX IF NOT EXISTS idx_material_versions_legacy ON material_versions(legacy_file_id, legacy_file_version_id)",
        "CREATE INDEX IF NOT EXISTS idx_sources_provider ON sources(provider_kind, authority)",
    )
    for statement in statements:
        conn.execute(statement)


def phase2_canvas_course_selection_migration(conn: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS course_offerings (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          provider TEXT NOT NULL,
          authority_id TEXT NOT NULL,
          remote_course_id TEXT NOT NULL,
          remote_term_id TEXT,
          remote_term_name TEXT,
          remote_name TEXT NOT NULL,
          remote_course_code TEXT,
          workflow_state TEXT,
          start_at TEXT,
          end_at TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(provider, authority_id, remote_course_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS course_offering_selections (
          id INTEGER PRIMARY KEY,
          offering_id INTEGER NOT NULL UNIQUE REFERENCES course_offerings(id) ON DELETE CASCADE,
          selected INTEGER NOT NULL DEFAULT 0,
          selected_at TEXT,
          updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_course_offerings_provider_authority ON course_offerings(provider, authority_id)",
        "CREATE INDEX IF NOT EXISTS idx_course_offering_selections_selected ON course_offering_selections(selected, updated_at)",
    )
    for statement in statements:
        conn.execute(statement)


def phase2_canvas_file_sync_baseline_migration(conn: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS canvas_file_sync_baselines (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          course_offering_id INTEGER NOT NULL REFERENCES course_offerings(id) ON DELETE CASCADE,
          provider TEXT NOT NULL,
          authority_id TEXT NOT NULL,
          remote_course_id TEXT NOT NULL,
          remote_file_id TEXT NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',
          filename TEXT NOT NULL,
          content_type TEXT NOT NULL DEFAULT '',
          byte_size INTEGER,
          remote_created_at TEXT,
          remote_updated_at TEXT,
          remote_modified_at TEXT,
          locked INTEGER NOT NULL DEFAULT 0,
          hidden INTEGER NOT NULL DEFAULT 0,
          hidden_for_user INTEGER NOT NULL DEFAULT 0,
          baseline_fingerprint TEXT NOT NULL,
          applied_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(provider, authority_id, course_offering_id, remote_file_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_canvas_file_baselines_offering ON canvas_file_sync_baselines(course_offering_id, remote_file_id)",
    )
    for statement in statements:
        conn.execute(statement)


def phase2_canvas_file_acquisition_migration(conn: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS course_offering_local_bindings (
          id INTEGER PRIMARY KEY,
          course_offering_id INTEGER NOT NULL UNIQUE REFERENCES course_offerings(id) ON DELETE CASCADE,
          legacy_course_id INTEGER NOT NULL UNIQUE REFERENCES courses(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_course_offering_local_bindings_course ON course_offering_local_bindings(legacy_course_id)",
    )
    for statement in statements:
        conn.execute(statement)


def phase2_academic_change_journal_migration(conn: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS academic_change_events (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          event_kind TEXT NOT NULL,
          provider TEXT NOT NULL,
          authority_id TEXT NOT NULL,
          course_offering_id INTEGER NOT NULL REFERENCES course_offerings(id),
          course_offering_stable_id TEXT NOT NULL,
          material_stable_id TEXT NOT NULL,
          remote_item_stable_id TEXT NOT NULL,
          predecessor_event_stable_id TEXT,
          resulting_material_version_stable_id TEXT,
          resulting_sha256 TEXT,
          course_label TEXT NOT NULL DEFAULT '',
          material_label TEXT NOT NULL DEFAULT '',
          changed_fields_json TEXT NOT NULL DEFAULT '[]',
          applied_at TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS academic_change_event_state (
          event_id INTEGER PRIMARY KEY REFERENCES academic_change_events(id) ON DELETE CASCADE,
          seen_at TEXT,
          updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_academic_change_events_offering_time ON academic_change_events(course_offering_id, applied_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_academic_change_events_material ON academic_change_events(material_stable_id, applied_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_academic_change_events_remote_chain ON academic_change_events(authority_id, course_offering_stable_id, remote_item_stable_id, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_academic_change_event_state_seen ON academic_change_event_state(seen_at, event_id)",
    )
    for statement in statements:
        conn.execute(statement)


SCHEMA_MIGRATIONS: tuple[tuple[int, str, Callable[[sqlite3.Connection], None]], ...] = (
    (1, "phase1_domain_foundation", phase1_domain_foundation_migration),
    (2, "phase2_canvas_course_selection", phase2_canvas_course_selection_migration),
    (3, "phase2_canvas_file_sync_baseline", phase2_canvas_file_sync_baseline_migration),
    (4, "phase2_canvas_file_acquisition", phase2_canvas_file_acquisition_migration),
    (5, "phase2_academic_change_journal", phase2_academic_change_journal_migration),
)


def run_schema_migrations(conn: sqlite3.Connection) -> None:
    for version, name, migration in SCHEMA_MIGRATIONS:
        apply_schema_migration(conn, version, name, migration)


def row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def domain_source_stable_id(file_row: sqlite3.Row | dict[str, Any]) -> str:
    material_seed = str(row_value(file_row, "stable_id") or row_value(file_row, "sha256") or row_value(file_row, "hash") or row_value(file_row, "id"))
    import_mode = str(row_value(file_row, "import_mode", "") or "")
    locator = str(row_value(file_row, "original_path", "") or "")
    seed = f"{material_seed}|{import_mode}|{locator}"
    return deterministic_stable_id("source", seed)


def canvas_source_stable_id(authority_id: str, offering_stable_id: str, remote_file_id: str) -> str:
    return deterministic_stable_id(
        "source",
        f"v1\0canvas\0{authority_id}\0{offering_stable_id}\0{remote_file_id}",
    )


def domain_material_version_stable_id(material_stable_id: str, sha256: str) -> str:
    return deterministic_stable_id("matver", f"{material_stable_id}|{sha256}")


def upsert_domain_projection_for_file(
    conn: sqlite3.Connection,
    file_row: sqlite3.Row,
    provenance: IngestionSourceProvenance | None = None,
) -> None:
    now = now_iso()
    material_stable_id = str(file_row["stable_id"] or file_row["sha256"] or file_row["hash"] or f"legacy-file-{file_row['id']}")
    if provenance is None and str(file_row["import_mode"] or "") == "canvas_remote":
        existing_source = conn.execute(
            """
            SELECT s.* FROM sources s
            JOIN material_versions mv ON mv.source_id=s.id
            JOIN materials m ON m.id=mv.material_id
            WHERE m.stable_id=? AND s.provider_kind='canvas'
            ORDER BY mv.id DESC LIMIT 1
            """,
            (material_stable_id,),
        ).fetchone()
        if existing_source:
            provenance = IngestionSourceProvenance(
                stable_source_id=str(existing_source["stable_id"]),
                provider_kind="canvas",
                authority="REMOTE_AUTHORITATIVE",
                connector_id=str(existing_source["connector_id"] or ""),
                remote_id=str(existing_source["remote_id"] or ""),
                remote_updated_at=str(existing_source["remote_updated_at"] or ""),
                fetched_at=str(existing_source["fetched_at"] or ""),
                source_label=str(existing_source["source_label"] or "Canvas"),
                source_type=str(existing_source["source_type"] or "Canvas"),
                import_mode="canvas_remote",
            )
    source_stable_id = provenance.stable_source_id if provenance else domain_source_stable_id(file_row)
    source_label = str(file_row["source_label"] or "")
    source_type = str(file_row["source_type"] or file_row["source"] or "")
    import_mode = str(file_row["import_mode"] or "scanned")
    original_path = str(file_row["original_path"] or "")
    authority = provenance.authority if provenance else "USER_OWNED"
    provider_kind = provenance.provider_kind if provenance else "local"
    if provenance:
        source_label = provenance.source_label
        source_type = provenance.source_type
        import_mode = provenance.import_mode
    conn.execute(
        """
        INSERT INTO sources(stable_id, provider_kind, authority, source_label, source_type, import_mode,
          local_locator, connector_id, remote_id, remote_url, remote_updated_at, fetched_at,
          created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
        ON CONFLICT(stable_id) DO UPDATE SET
          provider_kind=excluded.provider_kind,
          authority=excluded.authority,
          source_label=excluded.source_label,
          source_type=excluded.source_type,
          import_mode=excluded.import_mode,
          local_locator=excluded.local_locator,
          connector_id=excluded.connector_id,
          remote_id=excluded.remote_id,
          remote_url='',
          remote_updated_at=excluded.remote_updated_at,
          fetched_at=excluded.fetched_at,
          updated_at=excluded.updated_at
        """,
        (
            source_stable_id,
            provider_kind,
            authority,
            source_label,
            source_type,
            import_mode,
            original_path,
            provenance.connector_id if provenance else "",
            provenance.remote_id if provenance else "",
            provenance.remote_updated_at if provenance else "",
            provenance.fetched_at if provenance else "",
            now,
            now,
        ),
    )
    source_id = conn.execute("SELECT id FROM sources WHERE stable_id=?", (source_stable_id,)).fetchone()["id"]
    material_created = str(file_row["material_created_at"] or file_row["indexed_at"] or now)
    material_updated = str(file_row["material_updated_at"] or file_row["indexed_at"] or now)
    legacy_material = conn.execute("SELECT id, stable_id FROM materials WHERE legacy_file_id=?", (file_row["id"],)).fetchone()
    if legacy_material and legacy_material["stable_id"] != material_stable_id:
        conn.execute(
            "UPDATE materials SET stable_id=?, updated_at=? WHERE id=?",
            (material_stable_id, now, legacy_material["id"]),
        )
    conn.execute(
        """
        INSERT INTO materials(stable_id, legacy_file_id, course_id, week_id, course_code, course_name,
          week_label, week_number, material_type, display_name, active, removed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stable_id) DO UPDATE SET
          legacy_file_id=excluded.legacy_file_id,
          course_id=excluded.course_id,
          week_id=excluded.week_id,
          course_code=excluded.course_code,
          course_name=excluded.course_name,
          week_label=excluded.week_label,
          week_number=excluded.week_number,
          material_type=excluded.material_type,
          display_name=excluded.display_name,
          active=excluded.active,
          removed_at=excluded.removed_at,
          updated_at=excluded.updated_at
        """,
        (
            material_stable_id,
            int(file_row["id"]),
            file_row["course_id"],
            file_row["week_id"],
            file_row["course_code"] or "",
            file_row["course_name"] or "",
            file_row["week_label"] or "",
            file_row["week_number"],
            file_row["material_type"] or "",
            file_row["display_name"] or file_row["filename"] or "",
            int(file_row["active"] or 0),
            file_row["removed_at"],
            material_created,
            material_updated,
        ),
    )
    material_id = conn.execute("SELECT id FROM materials WHERE stable_id=?", (material_stable_id,)).fetchone()["id"]
    version_rows = conn.execute("SELECT * FROM file_versions WHERE file_id=? ORDER BY id", (file_row["id"],)).fetchall()
    if not version_rows:
        version_rows = [
            {
                "id": None,
                "sha256": file_row["sha256"] or file_row["hash"] or "",
                "file_size": file_row["file_size"] or file_row["size"] or 0,
                "modified_at": file_row["modified_at"] or "",
                "indexed_at": file_row["indexed_at"] or now,
                "text_cache_path": file_row["text_cache_path"] or "",
                "active": file_row["active"] or 0,
            }
        ]
    active_version_id: int | None = None
    for version_row in version_rows:
        sha = str(row_value(version_row, "sha256", "") or "")
        if not sha:
            continue
        byte_size = int(row_value(version_row, "file_size", file_row["file_size"] or file_row["size"] or 0) or 0)
        blob_stable_id = deterministic_stable_id("blob", sha)
        conn.execute(
            """
            INSERT INTO blobs(stable_id, sha256, byte_size, mime_type, extension, local_locator, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
              byte_size=excluded.byte_size,
              mime_type=excluded.mime_type,
              extension=excluded.extension,
              local_locator=CASE WHEN blobs.local_locator='' THEN excluded.local_locator ELSE blobs.local_locator END,
              updated_at=excluded.updated_at
            """,
            (
                blob_stable_id,
                sha,
                byte_size,
                file_row["mime_type"] or "",
                file_row["file_extension"] or file_row["extension"] or "",
                original_path if provenance and sha == str(file_row["sha256"] or file_row["hash"] or "") else "",
                now,
                now,
            ),
        )
        blob_id = conn.execute("SELECT id FROM blobs WHERE sha256=?", (sha,)).fetchone()["id"]
        version_stable_id = domain_material_version_stable_id(material_stable_id, sha)
        version_active = int(row_value(version_row, "active", file_row["active"] or 0) or 0)
        existing_domain_version = conn.execute(
            "SELECT source_id, blob_id FROM material_versions WHERE stable_id=?",
            (version_stable_id,),
        ).fetchone()
        if existing_domain_version and not version_active:
            source_id_for_version = existing_domain_version["source_id"]
            blob_id_for_version = existing_domain_version["blob_id"] or blob_id
        elif version_active:
            source_id_for_version = source_id
            blob_id_for_version = blob_id
        else:
            source_id_for_version = None
            blob_id_for_version = blob_id
        conn.execute(
            """
            UPDATE material_versions SET stable_id=?, updated_at=?
            WHERE material_id=? AND sha256=? AND stable_id!=?
            """,
            (version_stable_id, now, material_id, sha, version_stable_id),
        )
        source_missing = int(file_row["source_missing"] or 0)
        legacy_version_id = row_value(version_row, "id")
        conn.execute(
            """
            INSERT INTO material_versions(stable_id, material_id, source_id, blob_id, legacy_file_id,
              legacy_file_version_id, sha256, byte_size, modified_at, indexed_at, text_cache_path,
              active, source_missing, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stable_id) DO UPDATE SET
              material_id=excluded.material_id,
              source_id=excluded.source_id,
              blob_id=excluded.blob_id,
              legacy_file_id=excluded.legacy_file_id,
              legacy_file_version_id=excluded.legacy_file_version_id,
              byte_size=excluded.byte_size,
              modified_at=excluded.modified_at,
              indexed_at=excluded.indexed_at,
              text_cache_path=excluded.text_cache_path,
              active=excluded.active,
              source_missing=excluded.source_missing,
              updated_at=excluded.updated_at
            """,
            (
                version_stable_id,
                material_id,
                source_id_for_version,
                blob_id_for_version,
                int(file_row["id"]),
                legacy_version_id,
                sha,
                byte_size,
                str(row_value(version_row, "modified_at", file_row["modified_at"] or "") or ""),
                str(row_value(version_row, "indexed_at", file_row["indexed_at"] or now) or now),
                str(row_value(version_row, "text_cache_path", file_row["text_cache_path"] or "") or ""),
                version_active,
                source_missing,
                now,
                now,
            ),
        )
        domain_version_id = conn.execute("SELECT id FROM material_versions WHERE stable_id=?", (version_stable_id,)).fetchone()["id"]
        if version_active:
            active_version_id = int(domain_version_id)
    conn.execute(
        "UPDATE materials SET current_version_id=?, updated_at=? WHERE id=?",
        (active_version_id, material_updated, material_id),
    )


def reconcile_domain_projection(conn: sqlite3.Connection) -> None:
    run_schema_migrations(conn)
    rows = conn.execute("SELECT * FROM files ORDER BY id").fetchall()
    for row in rows:
        upsert_domain_projection_for_file(conn, row)
    conn.execute("DELETE FROM material_versions WHERE legacy_file_id NOT IN (SELECT id FROM files)")
    conn.execute("DELETE FROM materials WHERE legacy_file_id NOT IN (SELECT id FROM files)")


def upsert_canvas_course_offering(
    conn: sqlite3.Connection,
    authority_id: str,
    course: CanvasCourse,
) -> sqlite3.Row:
    now = now_iso()
    stable_id = canvas_course_offering_stable_id(authority_id, str(course.remote_course_id))
    conn.execute(
        """
        INSERT INTO course_offerings(
          stable_id, provider, authority_id, remote_course_id, remote_term_id,
          remote_term_name, remote_name, remote_course_code, workflow_state,
          start_at, end_at, first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (?, 'canvas', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, authority_id, remote_course_id) DO UPDATE SET
          remote_term_id=excluded.remote_term_id,
          remote_term_name=excluded.remote_term_name,
          remote_name=excluded.remote_name,
          remote_course_code=excluded.remote_course_code,
          workflow_state=excluded.workflow_state,
          start_at=excluded.start_at,
          end_at=excluded.end_at,
          last_seen_at=excluded.last_seen_at,
          updated_at=excluded.updated_at
        """,
        (
            stable_id,
            authority_id,
            str(course.remote_course_id),
            course.term_id,
            course.term_name,
            course.name,
            course.course_code,
            course.workflow_state,
            course.start_at,
            course.end_at,
            now,
            now,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM course_offerings WHERE provider='canvas' AND authority_id=? AND remote_course_id=?",
        (authority_id, str(course.remote_course_id)),
    ).fetchone()
    if row is None:
        raise RuntimeError("course_offering_upsert_failed")
    return row


def public_course_offering(row: sqlite3.Row | dict[str, Any], selected: bool | None = None) -> dict[str, Any]:
    data = dict(row)
    payload = {
        "id": data["stable_id"],
        "stable_id": data["stable_id"],
        "provider": data["provider"],
        "remote_course_id": data["remote_course_id"],
        "remote_name": data["remote_name"],
        "remote_course_code": data["remote_course_code"] or "",
        "workflow_state": data["workflow_state"] or "",
        "start_at": data["start_at"],
        "end_at": data["end_at"],
        "term": {"id": data["remote_term_id"], "name": data["remote_term_name"]}
        if data["remote_term_id"] or data["remote_term_name"]
        else None,
        "first_seen_at": data["first_seen_at"],
        "last_seen_at": data["last_seen_at"],
        "updated_at": data["updated_at"],
    }
    if selected is not None:
        payload["selected"] = selected
    elif "selected" in data:
        payload["selected"] = bool(data["selected"])
    return payload


def canvas_selection_revision(conn: sqlite3.Connection, authority_id: str) -> str:
    rows = conn.execute(
        """
        SELECT co.stable_id, co.remote_course_id, COALESCE(sel.selected, 0) AS selected,
          COALESCE(sel.updated_at, '') AS selection_updated_at
        FROM course_offerings co
        LEFT JOIN course_offering_selections sel ON sel.offering_id=co.id
        WHERE co.provider='canvas' AND co.authority_id=?
        ORDER BY co.remote_course_id
        """,
        (authority_id,),
    ).fetchall()
    payload = [
        {
            "id": row["stable_id"],
            "remote_course_id": row["remote_course_id"],
            "selected": int(row["selected"]),
            "updated_at": row["selection_updated_at"],
        }
        for row in rows
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize_requested_remote_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("remote_course_ids must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("remote_course_ids must contain only strings")
        remote_id = value.strip()
        if not remote_id or len(remote_id) > 160 or any(ord(char) < 32 for char in remote_id):
            raise ValueError("Invalid remote course id")
        if remote_id not in seen:
            out.append(remote_id)
            seen.add(remote_id)
    return out


def current_selected_canvas_remote_ids(conn: sqlite3.Connection, authority_id: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT co.remote_course_id
        FROM course_offerings co
        JOIN course_offering_selections sel ON sel.offering_id=co.id
        WHERE co.provider='canvas' AND co.authority_id=? AND sel.selected=1
        """,
        (authority_id,),
    ).fetchall()
    return {str(row["remote_course_id"]) for row in rows}


def canvas_course_selection_plan(
    conn: sqlite3.Connection,
    authority_id: str,
    discovered_courses: list[CanvasCourse],
    requested_remote_ids: list[str],
) -> dict[str, Any]:
    discovered = canvas_course_map(discovered_courses)
    requested = set(requested_remote_ids)
    discovered_ids = set(discovered.keys())
    selected = current_selected_canvas_remote_ids(conn, authority_id)
    unknown = sorted(requested - discovered_ids)
    select_ids = sorted((requested & discovered_ids) - selected)
    unchanged_ids = sorted(requested & selected)
    deselect_ids = sorted((selected & discovered_ids) - requested)
    metadata_refresh: list[dict[str, Any]] = []
    for remote_id, course in discovered.items():
        row = conn.execute(
            "SELECT * FROM course_offerings WHERE provider='canvas' AND authority_id=? AND remote_course_id=?",
            (authority_id, remote_id),
        ).fetchone()
        if row and (
            row["remote_name"] != course.name
            or (row["remote_course_code"] or "") != course.course_code
            or (row["workflow_state"] or "") != course.workflow_state
            or row["remote_term_id"] != course.term_id
            or row["remote_term_name"] != course.term_name
            or row["start_at"] != course.start_at
            or row["end_at"] != course.end_at
        ):
            metadata_refresh.append({"remote_course_id": remote_id, "offering": public_canvas_course(course)})
    revision = canvas_selection_revision(conn, authority_id)
    return {
        "selection_revision": revision,
        "select": [{"remote_course_id": remote_id, "offering": public_canvas_course(discovered[remote_id])} for remote_id in select_ids],
        "deselect": [{"remote_course_id": remote_id} for remote_id in deselect_ids],
        "unchanged": [{"remote_course_id": remote_id, "offering": public_canvas_course(discovered[remote_id])} for remote_id in unchanged_ids],
        "metadata_refresh": metadata_refresh,
        "unknown_remote_course_ids": unknown,
        "remembered_missing_from_discovery": sorted(selected - discovered_ids),
    }


def apply_canvas_course_selection(
    conn: sqlite3.Connection,
    authority_id: str,
    discovered_courses: list[CanvasCourse],
    requested_remote_ids: list[str],
    expected_revision: str,
    *,
    fail_after_offerings: bool = False,
    fail_after_savepoint_release: bool = False,
) -> dict[str, Any]:
    if not conn.in_transaction:
        conn.execute("BEGIN")
    before = canvas_selection_revision(conn, authority_id)
    if not expected_revision or expected_revision != before:
        raise ValueError("stale_canvas_course_selection_preview")
    plan = canvas_course_selection_plan(conn, authority_id, discovered_courses, requested_remote_ids)
    unknown = plan["unknown_remote_course_ids"]
    if unknown:
        raise ValueError("unknown_canvas_course_id")
    discovered = canvas_course_map(discovered_courses)
    requested = set(requested_remote_ids)
    discovered_ids = set(discovered.keys())
    selected_before = current_selected_canvas_remote_ids(conn, authority_id)
    select_ids = (requested & discovered_ids) - selected_before
    unchanged_ids = requested & selected_before
    deselect_ids = (selected_before & discovered_ids) - requested
    savepoint = "canvas_course_selection_apply"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        offerings: dict[str, sqlite3.Row] = {}
        for course in discovered_courses:
            offerings[str(course.remote_course_id)] = upsert_canvas_course_offering(conn, authority_id, course)
        if fail_after_offerings:
            raise RuntimeError("synthetic_apply_failure")
        now = now_iso()
        for remote_id in sorted(select_ids | unchanged_ids):
            row = offerings[remote_id]
            conn.execute(
                """
                INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(offering_id) DO UPDATE SET
                  selected=1,
                  selected_at=COALESCE(course_offering_selections.selected_at, excluded.selected_at),
                  updated_at=excluded.updated_at
                """,
                (row["id"], now, now),
            )
        for remote_id in sorted(deselect_ids):
            row = offerings[remote_id]
            conn.execute(
                """
                INSERT INTO course_offering_selections(offering_id, selected, selected_at, updated_at)
                VALUES (?, 0, NULL, ?)
                ON CONFLICT(offering_id) DO UPDATE SET selected=0, updated_at=excluded.updated_at
                """,
                (row["id"], now),
            )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    if fail_after_savepoint_release:
        raise RuntimeError("synthetic_post_savepoint_failure")
    after_revision = canvas_selection_revision(conn, authority_id)
    rows = remembered_canvas_course_selections(conn, authority_id=authority_id)
    return {
        "selection_revision": after_revision,
        "selected": rows,
        "plan": plan,
    }


def remembered_canvas_course_selections(conn: sqlite3.Connection, authority_id: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE co.provider='canvas'"
    if authority_id:
        where += " AND co.authority_id=?"
        params.append(authority_id)
    rows = conn.execute(
        f"""
        SELECT co.*, COALESCE(sel.selected, 0) AS selected, sel.selected_at AS selected_at,
          sel.updated_at AS selection_updated_at
        FROM course_offerings co
        JOIN course_offering_selections sel ON sel.offering_id=co.id
        {where} AND sel.selected=1
        ORDER BY co.remote_course_code, co.remote_name, co.remote_course_id
        """,
        params,
    ).fetchall()
    return [
        public_course_offering(row, selected=bool(row["selected"]))
        | {"selected_at": row["selected_at"], "selection_updated_at": row["selection_updated_at"], "fresh": False}
        for row in rows
    ]


def canvas_remote_file_stable_id(authority_id: str, offering_stable_id: str, remote_file_id: str) -> str:
    return deterministic_stable_id(
        "canvas_remote_file",
        f"v1\0canvas\0{authority_id}\0{offering_stable_id}\0{remote_file_id}",
    )


def canvas_remote_file_payload(remote_file: CanvasRemoteFile) -> dict[str, Any]:
    unavailable = remote_file.locked or remote_file.hidden or remote_file.hidden_for_user
    return {
        "remote_file_id": remote_file.remote_file_id,
        "remote_course_id": remote_file.remote_course_id,
        "display_name": remote_file.display_name,
        "filename": remote_file.filename,
        "content_type": remote_file.content_type,
        "byte_size": remote_file.byte_size,
        "remote_created_at": remote_file.remote_created_at,
        "remote_updated_at": remote_file.remote_updated_at,
        "remote_modified_at": remote_file.remote_modified_at,
        "locked": remote_file.locked,
        "hidden": remote_file.hidden,
        "hidden_for_user": remote_file.hidden_for_user,
        "currently_unavailable": unavailable,
    }


def canvas_remote_revision_fingerprint(remote_file: CanvasRemoteFile) -> str:
    canonical = {
        "version": CANVAS_REMOTE_REVISION_VERSION,
        "file": canvas_remote_file_payload(remote_file),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def canvas_baseline_file(row: sqlite3.Row | dict[str, Any]) -> CanvasRemoteFile:
    return CanvasRemoteFile(
        remote_file_id=str(row["remote_file_id"]),
        remote_course_id=str(row["remote_course_id"]),
        display_name=str(row["display_name"] or ""),
        filename=str(row["filename"]),
        content_type=str(row["content_type"] or ""),
        byte_size=row["byte_size"],
        remote_created_at=row["remote_created_at"],
        remote_updated_at=row["remote_updated_at"],
        remote_modified_at=row["remote_modified_at"],
        locked=bool(row["locked"]),
        hidden=bool(row["hidden"]),
        hidden_for_user=bool(row["hidden_for_user"]),
    )


def normalize_requested_offering_ids(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("offering_ids must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("offering_ids must contain only strings")
        stable_id = value.strip()
        if not stable_id or len(stable_id) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in stable_id):
            raise ValueError("Invalid CourseOffering id")
        if stable_id not in seen:
            out.append(stable_id)
            seen.add(stable_id)
    return out


def selected_canvas_offerings(
    conn: sqlite3.Connection,
    authority_id: str,
    requested_offering_ids: list[str],
) -> list[sqlite3.Row]:
    requested = set(requested_offering_ids)
    if requested:
        placeholders = ",".join("?" for _ in requested)
        known = conn.execute(
            f"SELECT stable_id, provider, authority_id FROM course_offerings WHERE stable_id IN ({placeholders})",
            sorted(requested),
        ).fetchall()
        known_ids = {str(row["stable_id"]) for row in known}
        if known_ids != requested:
            raise ValueError("unknown_canvas_course_offering")
        if any(row["provider"] != "canvas" for row in known):
            raise ValueError("unsupported_course_offering_provider")
        if any(row["authority_id"] != authority_id for row in known):
            raise ValueError("canvas_course_offering_authority_mismatch")
    params: list[Any] = [authority_id]
    where = "co.provider='canvas' AND co.authority_id=? AND sel.selected=1"
    if requested:
        placeholders = ",".join("?" for _ in requested)
        where += f" AND co.stable_id IN ({placeholders})"
        params.extend(sorted(requested))
    rows = conn.execute(
        f"""
        SELECT co.* FROM course_offerings co
        JOIN course_offering_selections sel ON sel.offering_id=co.id
        WHERE {where}
        ORDER BY co.stable_id
        """,
        params,
    ).fetchall()
    if requested and {str(row["stable_id"]) for row in rows} != requested:
        raise ValueError("canvas_course_offering_not_selected")
    return rows


def canvas_baseline_revision(conn: sqlite3.Connection, offering_ids: list[int]) -> str:
    rows: list[sqlite3.Row] = []
    if offering_ids:
        placeholders = ",".join("?" for _ in offering_ids)
        rows = conn.execute(
            f"""
            SELECT b.stable_id, b.provider, b.authority_id,
              co.stable_id AS course_offering_stable_id, b.remote_course_id,
              b.remote_file_id, b.baseline_fingerprint
            FROM canvas_file_sync_baselines b
            JOIN course_offerings co ON co.id=b.course_offering_id
            WHERE b.course_offering_id IN ({placeholders})
            ORDER BY b.stable_id
            """,
            offering_ids,
        ).fetchall()
    payload = [dict(row) for row in rows]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def canvas_manifest_revision(manifests: list[tuple[str, CanvasRemoteFile]]) -> str:
    payload = [
        {
            "offering_id": offering_id,
            "remote_file_id": remote_file.remote_file_id,
            "fingerprint": canvas_remote_revision_fingerprint(remote_file),
        }
        for offering_id, remote_file in sorted(manifests, key=lambda item: (item[0], item[1].remote_file_id))
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def classify_canvas_file_change(current: CanvasRemoteFile, baseline: CanvasRemoteFile) -> tuple[str, list[str]]:
    current_payload = canvas_remote_file_payload(current)
    baseline_payload = canvas_remote_file_payload(baseline)
    changed = sorted(key for key in current_payload if current_payload[key] != baseline_payload[key])
    content_signals = {"byte_size", "remote_updated_at", "remote_modified_at"}
    if content_signals.intersection(changed):
        return "POTENTIAL_CONTENT_CHANGE", changed
    if changed:
        return "METADATA_CHANGE", changed
    return "UNCHANGED", []


def canvas_file_sync_plan(
    conn: sqlite3.Connection,
    connector: CanvasConnector,
    requested_offering_ids: list[str] | None = None,
) -> dict[str, Any]:
    authority_id = canvas_authority_id(connector.record.origin)
    offering_rows = selected_canvas_offerings(conn, authority_id, requested_offering_ids or [])
    offering_internal_ids = [int(row["id"]) for row in offering_rows]
    baseline_revision = canvas_baseline_revision(conn, offering_internal_ids)
    manifests: list[tuple[str, CanvasRemoteFile]] = []
    offering_sections: list[dict[str, Any]] = []
    summary = {kind: 0 for kind in ("ADD", "POTENTIAL_CONTENT_CHANGE", "METADATA_CHANGE", "UNCHANGED", "MISSING_FROM_REMOTE_DISCOVERY")}
    for offering in offering_rows:
        files = connector.list_course_files(str(offering["remote_course_id"]))
        if len(files) != len({remote_file.remote_file_id for remote_file in files}):
            raise CanvasConnectorError("duplicate_canvas_file_id")
        manifests.extend((str(offering["stable_id"]), remote_file) for remote_file in files)
        baseline_rows = conn.execute(
            "SELECT * FROM canvas_file_sync_baselines WHERE course_offering_id=? ORDER BY remote_file_id",
            (offering["id"],),
        ).fetchall()
        baselines = {str(row["remote_file_id"]): row for row in baseline_rows}
        current = {remote_file.remote_file_id: remote_file for remote_file in files}
        actions: list[dict[str, Any]] = []
        for remote_file_id in sorted(current):
            remote_file = current[remote_file_id]
            baseline_row = baselines.get(remote_file_id)
            if baseline_row is None:
                kind, changed_fields = "ADD", []
            else:
                kind, changed_fields = classify_canvas_file_change(remote_file, canvas_baseline_file(baseline_row))
            metadata = canvas_remote_file_payload(remote_file)
            actions.append(
                {
                    "kind": kind,
                    "remote_file_stable_id": canvas_remote_file_stable_id(authority_id, str(offering["stable_id"]), remote_file_id),
                    "remote_file_id": remote_file_id,
                    "metadata": metadata,
                    "changed_fields": changed_fields,
                    "reason_codes": [kind.lower()],
                    "acquisition_eligible": not metadata["currently_unavailable"],
                }
            )
            summary[kind] += 1
        for remote_file_id in sorted(set(baselines) - set(current)):
            baseline_file = canvas_baseline_file(baselines[remote_file_id])
            actions.append(
                {
                    "kind": "MISSING_FROM_REMOTE_DISCOVERY",
                    "remote_file_stable_id": str(baselines[remote_file_id]["stable_id"]),
                    "remote_file_id": remote_file_id,
                    "metadata": canvas_remote_file_payload(baseline_file),
                    "changed_fields": [],
                    "reason_codes": ["remote_discovery_missing_advisory"],
                    "acquisition_eligible": False,
                }
            )
            summary["MISSING_FROM_REMOTE_DISCOVERY"] += 1
        offering_sections.append(
            {
                "course_offering_id": offering["stable_id"],
                "remote_course_id": offering["remote_course_id"],
                "course_label": offering["remote_course_code"] or offering["remote_name"],
                "actions": actions,
            }
        )
    manifest_revision = canvas_manifest_revision(manifests)
    plan_seed = {
        "version": CANVAS_SYNC_PLAN_VERSION,
        "authority_id": authority_id,
        "offering_ids": sorted(str(row["stable_id"]) for row in offering_rows),
        "local_baseline_revision": baseline_revision,
        "remote_manifest_revision": manifest_revision,
    }
    plan_revision = hashlib.sha256(json.dumps(plan_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "version": CANVAS_SYNC_PLAN_VERSION,
        "authority_id": authority_id,
        "generated_at": now_iso(),
        "local_baseline_revision": baseline_revision,
        "remote_manifest_revision": manifest_revision,
        "plan_revision": plan_revision,
        "offerings": offering_sections,
        "summary": summary,
    }


def upsert_canvas_file_sync_baselines(
    conn: sqlite3.Connection,
    offering: sqlite3.Row | dict[str, Any],
    remote_files: list[CanvasRemoteFile],
    *,
    applied_at: str | None = None,
    fail_after: int | None = None,
) -> None:
    if offering["provider"] != "canvas":
        raise ValueError("unsupported_course_offering_provider")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    savepoint = "canvas_file_baseline_upsert"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        now = now_iso()
        applied = applied_at or now
        for index, remote_file in enumerate(remote_files):
            if remote_file.remote_course_id != offering["remote_course_id"]:
                raise ValueError("canvas_file_course_mismatch")
            stable_id = canvas_remote_file_stable_id(
                str(offering["authority_id"]),
                str(offering["stable_id"]),
                remote_file.remote_file_id,
            )
            payload = canvas_remote_file_payload(remote_file)
            conn.execute(
                """
                INSERT INTO canvas_file_sync_baselines(
                  stable_id, course_offering_id, provider, authority_id, remote_course_id,
                  remote_file_id, display_name, filename, content_type, byte_size,
                  remote_created_at, remote_updated_at, remote_modified_at, locked, hidden,
                  hidden_for_user, baseline_fingerprint, applied_at, created_at, updated_at
                ) VALUES (?, ?, 'canvas', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, authority_id, course_offering_id, remote_file_id) DO UPDATE SET
                  display_name=excluded.display_name, filename=excluded.filename,
                  content_type=excluded.content_type, byte_size=excluded.byte_size,
                  remote_created_at=excluded.remote_created_at,
                  remote_updated_at=excluded.remote_updated_at,
                  remote_modified_at=excluded.remote_modified_at, locked=excluded.locked,
                  hidden=excluded.hidden, hidden_for_user=excluded.hidden_for_user,
                  baseline_fingerprint=excluded.baseline_fingerprint,
                  applied_at=excluded.applied_at, updated_at=excluded.updated_at
                """,
                (
                    stable_id, offering["id"], offering["authority_id"], remote_file.remote_course_id,
                    remote_file.remote_file_id, payload["display_name"], payload["filename"],
                    payload["content_type"], payload["byte_size"], payload["remote_created_at"],
                    payload["remote_updated_at"], payload["remote_modified_at"], int(payload["locked"]),
                    int(payload["hidden"]), int(payload["hidden_for_user"]),
                    canvas_remote_revision_fingerprint(remote_file), applied, now, now,
                ),
            )
            if fail_after is not None and index + 1 >= fail_after:
                raise RuntimeError("synthetic_baseline_failure")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def normalize_requested_canvas_file_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("remote_file_stable_ids must be a list")
    if len(values) > CANVAS_MAX_APPLY_FILES:
        raise ValueError("Too many Canvas files requested")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("remote_file_stable_ids must contain only strings")
        stable_id = value.strip()
        if not stable_id or len(stable_id) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in stable_id):
            raise ValueError("Invalid Canvas remote file id")
        if stable_id not in seen:
            normalized.append(stable_id)
            seen.add(stable_id)
    return normalized


def canvas_action_remote_file(action: dict[str, Any]) -> CanvasRemoteFile:
    metadata = action["metadata"]
    return CanvasRemoteFile(
        remote_file_id=str(metadata["remote_file_id"]),
        remote_course_id=str(metadata["remote_course_id"]),
        display_name=str(metadata["display_name"] or ""),
        filename=str(metadata["filename"]),
        content_type=str(metadata["content_type"] or ""),
        byte_size=metadata["byte_size"],
        remote_created_at=metadata["remote_created_at"],
        remote_updated_at=metadata["remote_updated_at"],
        remote_modified_at=metadata["remote_modified_at"],
        locked=bool(metadata["locked"]),
        hidden=bool(metadata["hidden"]),
        hidden_for_user=bool(metadata["hidden_for_user"]),
    )


def safe_canvas_file_extension(filename: str) -> str:
    if "\x00" in filename or "/" in filename or "\\" in filename or ".." in filename:
        raise CanvasConnectorError("unsupported_canvas_file_type")
    extension = Path(filename).suffix.lower()
    if extension not in ACADEMIC_EXTS or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        raise CanvasConnectorError("unsupported_canvas_file_type")
    return extension


def ensure_canvas_course_binding(conn: sqlite3.Connection, offering: sqlite3.Row | dict[str, Any]) -> tuple[sqlite3.Row, sqlite3.Row]:
    binding = conn.execute(
        "SELECT * FROM course_offering_local_bindings WHERE course_offering_id=?",
        (offering["id"],),
    ).fetchone()
    course_stable_id = deterministic_stable_id("course", f"canvas-offering\0{offering['stable_id']}")
    now = now_iso()
    if binding:
        course = conn.execute("SELECT * FROM courses WHERE id=?", (binding["legacy_course_id"],)).fetchone()
        if course is None:
            raise CanvasConnectorError("canvas_course_binding_invalid")
        conn.execute(
            """
            UPDATE courses SET
              code=CASE WHEN metadata_locked=1 THEN code ELSE ? END,
              course_code=CASE WHEN metadata_locked=1 THEN course_code ELSE ? END,
              name=CASE WHEN metadata_locked=1 THEN name ELSE ? END,
              display_name=CASE WHEN metadata_locked=1 THEN display_name ELSE ? END,
              source_kind='canvas_remote', active=1, updated_at=? WHERE id=?
            """,
            (
                str(offering["remote_course_code"] or ""),
                str(offering["remote_course_code"] or ""),
                str(offering["remote_name"]),
                str(offering["remote_name"]),
                now,
                course["id"],
            ),
        )
        conn.execute("UPDATE course_offering_local_bindings SET updated_at=? WHERE id=?", (now, binding["id"]))
        course = conn.execute("SELECT * FROM courses WHERE id=?", (course["id"],)).fetchone()
    else:
        term = ensure_term(conn, "Imported Courses", stable_id="term_imported")
        folder_name = f"__canvas_{hashlib.sha256(str(offering['stable_id']).encode()).hexdigest()[:20]}"
        managed_path = str(safe_child_path(canvas_remote_content_root(), str(offering["stable_id"])))
        conn.execute(
            """
            INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
              source_folder, source_kind, term_id, archived, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'canvas_remote', ?, 0, 1, ?, ?)
            """,
            (
                course_stable_id,
                str(offering["remote_course_code"] or ""),
                str(offering["remote_course_code"] or ""),
                str(offering["remote_name"]),
                str(offering["remote_name"]),
                folder_name,
                managed_path,
                managed_path,
                term["id"],
                now,
                now,
            ),
        )
        course = conn.execute("SELECT * FROM courses WHERE stable_id=?", (course_stable_id,)).fetchone()
        conn.execute(
            "INSERT INTO course_offering_local_bindings(course_offering_id, legacy_course_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (offering["id"], course["id"], now, now),
        )
    week_stable_id = deterministic_stable_id("week", f"canvas-files\0{offering['stable_id']}")
    week = conn.execute("SELECT * FROM weeks WHERE stable_id=?", (week_stable_id,)).fetchone()
    if week is None:
        conn.execute(
            """
            INSERT INTO weeks(stable_id, course_id, week_label, week_number, path, has_materials,
              file_count, kind, origin, archived, created_at, updated_at)
            VALUES (?, ?, 'Canvas Files', NULL, '', 0, 0, 'module', 'canvas', 0, ?, ?)
            """,
            (week_stable_id, course["id"], now, now),
        )
        week = conn.execute("SELECT * FROM weeks WHERE stable_id=?", (week_stable_id,)).fetchone()
    return course, week


def acquire_canvas_file(
    connector: CanvasConnector,
    offering: sqlite3.Row | dict[str, Any],
    action: dict[str, Any],
    *,
    open_url: Callable[[str, int], Any] = default_canvas_download_open,
) -> CanvasAcquiredFile:
    expected = canvas_action_remote_file(action)
    before = connector.get_course_file(str(offering["remote_course_id"]), expected.remote_file_id)
    if canvas_remote_revision_fingerprint(before.remote_file) != canvas_remote_revision_fingerprint(expected):
        raise CanvasConnectorError("stale_canvas_sync_plan")
    if before.remote_file.locked or before.remote_file.hidden or before.remote_file.hidden_for_user:
        raise CanvasConnectorError("canvas_file_unavailable")
    extension = safe_canvas_file_extension(before.remote_file.filename)
    staging_root = canvas_acquisition_staging_root()
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_path = safe_child_path(staging_root, f"stage-{secrets.token_hex(16)}{extension}")
    digest, byte_size = stream_canvas_download(
        before.download_url,
        staging_path,
        declared_size=before.remote_file.byte_size,
        open_url=open_url,
    )
    try:
        after = connector.get_course_file(str(offering["remote_course_id"]), expected.remote_file_id)
        if canvas_remote_revision_fingerprint(before.remote_file) != canvas_remote_revision_fingerprint(after.remote_file):
            raise CanvasConnectorError("canvas_file_changed_during_acquisition")
        if canvas_remote_revision_fingerprint(after.remote_file) != canvas_remote_revision_fingerprint(expected):
            raise CanvasConnectorError("stale_canvas_sync_plan")
        retained_dir = safe_child_path(canvas_remote_content_root(), str(action["remote_file_stable_id"]))
        retained_dir.mkdir(parents=True, exist_ok=True)
        retained_path = safe_child_path(retained_dir, f"{digest}{extension}")
        created = not retained_path.exists()
        if created:
            os.replace(staging_path, retained_path)
            os.chmod(retained_path, 0o600)
        else:
            if retained_path.stat().st_size != byte_size or sha256_file(retained_path) != digest:
                raise CanvasConnectorError("canvas_retained_content_conflict")
            staging_path.unlink(missing_ok=True)
        return CanvasAcquiredFile(
            remote_file=after.remote_file,
            remote_file_stable_id=str(action["remote_file_stable_id"]),
            retained_path=retained_path,
            sha256=digest,
            byte_size=byte_size,
            extension=extension,
            created_retained_file=created,
        )
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise


def cleanup_new_canvas_retained_files(acquired: list[CanvasAcquiredFile]) -> None:
    for item in acquired:
        if item.created_retained_file:
            item.retained_path.unlink(missing_ok=True)


def academic_change_event_stable_id(
    event_kind: str,
    authority_id: str,
    offering_stable_id: str,
    remote_item_stable_id: str,
    predecessor_event_stable_id: str | None,
    resulting_revision_id: str,
) -> str:
    if event_kind not in ACADEMIC_CHANGE_EVENT_KINDS:
        raise ValueError("Unsupported academic change event kind")
    return deterministic_stable_id(
        "change",
        "\0".join(
            (
                ACADEMIC_CHANGE_EVENT_VERSION,
                event_kind,
                "canvas",
                authority_id,
                offering_stable_id,
                remote_item_stable_id,
                predecessor_event_stable_id or "none",
                resulting_revision_id,
            )
        ),
    )


def latest_academic_change_event_id(
    conn: sqlite3.Connection,
    *,
    authority_id: str,
    offering_stable_id: str,
    remote_item_stable_id: str,
) -> str | None:
    row = conn.execute(
        """
        SELECT stable_id
        FROM academic_change_events
        WHERE provider='canvas' AND authority_id=?
          AND course_offering_stable_id=? AND remote_item_stable_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (authority_id, offering_stable_id, remote_item_stable_id),
    ).fetchone()
    return str(row["stable_id"]) if row is not None else None


def current_material_version_identity(
    conn: sqlite3.Connection,
    material_stable_id: str,
) -> tuple[str, str]:
    row = conn.execute(
        """
        SELECT mv.stable_id, mv.sha256
        FROM materials m
        JOIN material_versions mv ON mv.id=m.current_version_id
        WHERE m.stable_id=?
        """,
        (material_stable_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("canvas_material_version_missing")
    return str(row["stable_id"]), str(row["sha256"])


def record_academic_change_event(
    conn: sqlite3.Connection,
    *,
    event_kind: str,
    offering: sqlite3.Row | dict[str, Any],
    remote_item_stable_id: str,
    predecessor_event_stable_id: str | None,
    material_stable_id: str,
    material_label: str,
    changed_fields: list[str],
    resulting_revision_id: str,
    resulting_material_version_stable_id: str | None,
    resulting_sha256: str | None,
    applied_at: str,
) -> str:
    if event_kind not in ACADEMIC_CHANGE_EVENT_KINDS:
        raise ValueError("Unsupported academic change event kind")
    allowed_changed_fields = {
        "display_name",
        "filename",
        "content_type",
        "byte_size",
        "remote_created_at",
        "remote_updated_at",
        "remote_modified_at",
        "locked",
        "hidden",
        "hidden_for_user",
    }
    normalized_fields = sorted({str(field) for field in changed_fields if str(field) in allowed_changed_fields})
    offering_stable_id = str(offering["stable_id"])
    authority_id = str(offering["authority_id"])
    stable_id = academic_change_event_stable_id(
        event_kind,
        authority_id,
        offering_stable_id,
        remote_item_stable_id,
        predecessor_event_stable_id,
        resulting_revision_id,
    )
    course_label = clean_metadata_text(
        offering["remote_course_code"] or offering["remote_name"],
        "Course label",
        required=False,
        limit=200,
    )
    safe_material_label = clean_metadata_text(
        material_label,
        "Material label",
        required=False,
        limit=512,
    )
    normalized_fields_json = json.dumps(normalized_fields, separators=(",", ":"))
    expected_fact = (
        event_kind,
        "canvas",
        authority_id,
        int(offering["id"]),
        offering_stable_id,
        material_stable_id,
        remote_item_stable_id,
        predecessor_event_stable_id,
        resulting_material_version_stable_id,
        resulting_sha256,
        course_label,
        safe_material_label,
        normalized_fields_json,
    )
    existing = conn.execute(
        """
        SELECT event_kind, provider, authority_id, course_offering_id,
          course_offering_stable_id, material_stable_id, remote_item_stable_id,
          predecessor_event_stable_id, resulting_material_version_stable_id,
          resulting_sha256, course_label, material_label, changed_fields_json
        FROM academic_change_events
        WHERE stable_id=?
        """,
        (stable_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != expected_fact:
            raise RuntimeError("academic_change_event_identity_conflict")
        return stable_id
    if latest_academic_change_event_id(
        conn,
        authority_id=authority_id,
        offering_stable_id=offering_stable_id,
        remote_item_stable_id=remote_item_stable_id,
    ) != predecessor_event_stable_id:
        raise RuntimeError("academic_change_event_predecessor_conflict")
    conn.execute(
        """
        INSERT INTO academic_change_events(
          stable_id, event_kind, provider, authority_id, course_offering_id,
          course_offering_stable_id, material_stable_id, remote_item_stable_id,
          predecessor_event_stable_id, resulting_material_version_stable_id,
          resulting_sha256, course_label,
          material_label, changed_fields_json, applied_at, created_at
        ) VALUES (?, ?, 'canvas', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stable_id) DO NOTHING
        """,
        (
            stable_id,
            event_kind,
            authority_id,
            offering["id"],
            offering_stable_id,
            material_stable_id,
            remote_item_stable_id,
            predecessor_event_stable_id,
            resulting_material_version_stable_id,
            resulting_sha256,
            course_label,
            safe_material_label,
            normalized_fields_json,
            applied_at,
            applied_at,
        ),
    )
    inserted = conn.execute(
        """
        SELECT event_kind, provider, authority_id, course_offering_id,
          course_offering_stable_id, material_stable_id, remote_item_stable_id,
          predecessor_event_stable_id, resulting_material_version_stable_id,
          resulting_sha256, course_label, material_label, changed_fields_json
        FROM academic_change_events
        WHERE stable_id=?
        """,
        (stable_id,),
    ).fetchone()
    if inserted is None or tuple(inserted) != expected_fact:
        raise RuntimeError("academic_change_event_identity_conflict")
    return stable_id


def list_academic_changes(
    conn: sqlite3.Connection,
    *,
    unseen_only: bool = False,
    course_offering_stable_id: str = "",
    limit: int = WHATS_NEW_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > WHATS_NEW_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {WHATS_NEW_MAX_LIMIT}")
    params: list[Any] = []
    where = ["1=1"]
    if unseen_only:
        where.append("state.seen_at IS NULL")
    if course_offering_stable_id:
        if len(course_offering_stable_id) > 160 or any(ord(char) < 32 for char in course_offering_stable_id):
            raise ValueError("Invalid CourseOffering id")
        where.append("event.course_offering_stable_id=?")
        params.append(course_offering_stable_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT event.*, state.seen_at,
          CASE WHEN f.active=1 AND f.removed_at IS NULL THEN f.id ELSE NULL END AS file_id
        FROM academic_change_events event
        LEFT JOIN academic_change_event_state state ON state.event_id=event.id
        LEFT JOIN materials material ON material.stable_id=event.material_stable_id
        LEFT JOIN files f ON f.id=material.legacy_file_id
        WHERE {' AND '.join(where)}
        ORDER BY event.applied_at DESC, event.stable_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            changed_fields = json.loads(row["changed_fields_json"])
        except (TypeError, json.JSONDecodeError):
            changed_fields = []
        result.append(
            {
                "event_id": row["stable_id"],
                "event_kind": row["event_kind"],
                "course_offering_id": row["course_offering_stable_id"],
                "course_label": row["course_label"],
                "material_id": row["material_stable_id"],
                "material_label": row["material_label"],
                "changed_fields": changed_fields if isinstance(changed_fields, list) else [],
                "applied_at": row["applied_at"],
                "seen": bool(row["seen_at"]),
                "file_id": row["file_id"],
            }
        )
    return result


def academic_change_counts(
    conn: sqlite3.Connection,
    *,
    course_offering_stable_id: str = "",
) -> dict[str, int]:
    params: list[Any] = []
    where = ["1=1"]
    if course_offering_stable_id:
        if len(course_offering_stable_id) > 160 or any(ord(char) < 32 for char in course_offering_stable_id):
            raise ValueError("Invalid CourseOffering id")
        where.append("event.course_offering_stable_id=?")
        params.append(course_offering_stable_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_count,
          COALESCE(SUM(CASE WHEN state.seen_at IS NULL THEN 1 ELSE 0 END), 0) AS unseen_count
        FROM academic_change_events event
        LEFT JOIN academic_change_event_state state ON state.event_id=event.id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    return {
        "unseen_count": int(row["unseen_count"]),
        "total_count": int(row["total_count"]),
    }


def canonical_backup_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def backup_record_id(prefix: str, *parts: Any) -> str:
    return deterministic_stable_id(prefix, "\0".join(str(part or "") for part in parts))


def safe_backup_text(value: Any, *, limit: int) -> str:
    text = str(value or "")[:limit]
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    private_path_patterns = (
        re.compile(
            r"(?<!:)" + r"/(?:" + "|".join(("Users", "home", "private", "var", "tmp")) + r")/[^\s'\"`<>)]*"
        ),
        re.compile(r"(?<![A-Za-z0-9:])/(?:[^/\s'\"`<>]+/)+[^\s'\"`<>)]*"),
        re.compile(r"[A-Za-z]:\\" + "Users" + r"\\[^\\\s'\"`<>]+(?:\\[^\s'\"`<>]*)?"),
        re.compile(r"[A-Za-z]:\\(?:[^\\\s'\"`<>]+\\)+[^\s'\"`<>]*"),
        re.compile(r"\\\\[^\\\s'\"`<>]+\\[^\s'\"`<>]+(?:\\[^\s'\"`<>]*)?"),
    )
    secret_patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.IGNORECASE),
        re.compile(r"(?i)\b(?:token|password|secret|api[_ -]?key)\s*[:=]\s*[^\s,;]{8,}"),
        re.compile(r"https?://[^\s]+(?:canvas|download|verifier|signature|token)[^\s]*", re.IGNORECASE),
    )
    for pattern in private_path_patterns:
        text = pattern.sub("[redacted-local-path]", text)
    for pattern in secret_patterns:
        text = pattern.sub("[redacted-sensitive-value]", text)
    return text


def backup_material_maps(conn: sqlite3.Connection) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    material_by_file: dict[int, str] = {}
    course_by_id = {
        int(row["id"]): str(row["stable_id"])
        for row in conn.execute("SELECT id, stable_id FROM courses WHERE stable_id!=''").fetchall()
    }
    week_by_id = {
        int(row["id"]): str(row["stable_id"])
        for row in conn.execute("SELECT id, stable_id FROM weeks WHERE stable_id!=''").fetchall()
    }
    rows = conn.execute(
        """
        SELECT f.id AS file_id, COALESCE(NULLIF(m.stable_id, ''), NULLIF(f.stable_id, '')) AS material_stable_id
        FROM files f LEFT JOIN materials m ON m.legacy_file_id=f.id
        """
    ).fetchall()
    for row in rows:
        stable_id = str(row["material_stable_id"] or "")
        if stable_id:
            material_by_file[int(row["file_id"])] = stable_id
    return material_by_file, course_by_id, week_by_id


def require_unique_backup_records(dataset: str, records: list[dict[str, Any]]) -> None:
    identities: set[str] = set()
    for record in records:
        identity = str(record.get("stable_id") or record.get("record_id") or "")
        if not BACKUP_STABLE_ID_RE.fullmatch(identity):
            raise ValueError(f"Invalid portable identity in {dataset}")
        if identity in identities:
            raise ValueError(f"Duplicate portable identity in {dataset}")
        identities.add(identity)
    if len(records) > BACKUP_MAX_RECORDS:
        raise ValueError(f"Too many records in {dataset}")


def assign_backup_occurrence_ids(
    prefix: str,
    records: list[dict[str, Any]],
    *,
    identity_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Assign deterministic IDs to a multiset without exposing SQLite row IDs."""
    groups: dict[bytes, list[dict[str, Any]]] = {}
    for record in records:
        if "stable_id" in record:
            raise ValueError("Occurrence-based backup record already has an identity")
        semantic_identity = {field: record.get(field) for field in identity_fields}
        identity_bytes = canonical_backup_json(semantic_identity)
        groups.setdefault(identity_bytes, []).append(dict(record))
    identified: list[dict[str, Any]] = []
    for identity_bytes in sorted(groups):
        ordered = sorted(groups[identity_bytes], key=canonical_backup_json)
        for occurrence_index, record in enumerate(ordered, start=1):
            record["stable_id"] = backup_record_id(
                prefix,
                identity_bytes.decode("utf-8"),
                occurrence_index,
            )
            identified.append(record)
    return identified


def project_user_owned_backup(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    material_by_file, course_by_id, week_by_id = backup_material_maps(conn)
    datasets: dict[str, list[dict[str, Any]]] = {member: [] for member in BACKUP_DATA_MEMBERS}

    terms = [
        {
            "stable_id": str(row["stable_id"]),
            "name": safe_backup_text(row["name"], limit=120),
            "archived": bool(row["archived"]),
            "sort_order": int(row["sort_order"] or 0),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in conn.execute("SELECT * FROM terms ORDER BY stable_id").fetchall()
        if row["stable_id"]
    ]
    datasets["data/terms.json"] = terms
    term_by_id = {
        int(row["id"]): str(row["stable_id"])
        for row in conn.execute("SELECT id, stable_id FROM terms WHERE stable_id!=''").fetchall()
    }

    courses: list[dict[str, Any]] = []
    included_course_ids: set[int] = set()
    for row in conn.execute("SELECT * FROM courses ORDER BY stable_id").fetchall():
        source_kind = str(row["source_kind"] or "")
        if source_kind in {"system", "demo"}:
            continue
        if source_kind == "canvas_remote" and not bool(row["metadata_locked"]):
            continue
        stable_id = str(row["stable_id"] or "")
        if not stable_id:
            continue
        included_course_ids.add(int(row["id"]))
        courses.append(
            {
                "stable_id": stable_id,
                "term_ref": term_by_id.get(int(row["term_id"])) if row["term_id"] else None,
                "display_name": safe_backup_text(row["display_name"] or row["name"], limit=120),
                "course_code": safe_backup_text(row["course_code"] or row["code"], limit=40),
                "archived": bool(row["archived"]),
                "active": bool(row["active"]),
                "sort_order": int(row["sort_order"] or 0),
                "metadata_locked": bool(row["metadata_locked"]),
                "course_kind": "remote_overlay" if source_kind == "canvas_remote" else "local",
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
        )
    datasets["data/courses.json"] = courses

    learning_units: list[dict[str, Any]] = []
    included_week_ids: set[int] = set()
    for row in conn.execute("SELECT * FROM weeks ORDER BY stable_id").fetchall():
        if int(row["course_id"]) not in included_course_ids or str(row["origin"] or "") != "managed":
            continue
        stable_id = str(row["stable_id"] or "")
        if not stable_id:
            continue
        included_week_ids.add(int(row["id"]))
        learning_units.append(
            {
                "stable_id": stable_id,
                "course_ref": course_by_id.get(int(row["course_id"])),
                "label": safe_backup_text(row["week_label"], limit=80),
                "number": int(row["week_number"]) if row["week_number"] is not None else None,
                "kind": safe_backup_text(row["kind"], limit=20),
                "archived": bool(row["archived"]),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
        )
    datasets["data/learning_units.json"] = learning_units

    overlays: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM files WHERE metadata_locked=1 ORDER BY stable_id").fetchall():
        material_ref = material_by_file.get(int(row["id"]))
        if not material_ref:
            raise ValueError("Material overlay lacks a portable material identity")
        overlays.append(
            {
                "stable_id": backup_record_id("overlay", material_ref),
                "material_ref": material_ref,
                "course_ref": course_by_id.get(int(row["course_id"])) if int(row["course_id"]) in included_course_ids else None,
                "learning_unit_ref": week_by_id.get(int(row["week_id"])) if row["week_id"] and int(row["week_id"]) in included_week_ids else None,
                "display_name": safe_backup_text(row["display_name"] or row["filename"], limit=240),
                "material_type": safe_backup_text(row["material_type"], limit=40),
                "category": safe_backup_text(row["category"], limit=80),
                "exercise_type": safe_backup_text(row["exercise_type"], limit=80),
                "inbox": bool(row["inbox"]),
                "active": bool(row["active"]),
                "removed_at": str(row["removed_at"] or "") or None,
                "updated_at": str(row["material_updated_at"] or row["indexed_at"] or ""),
            }
        )
    datasets["data/material_overlays.json"] = overlays

    local_sources: list[dict[str, Any]] = []
    source_rows = conn.execute(
        """
        SELECT f.*, m.stable_id AS material_stable_id,
          s.authority AS domain_authority
        FROM files f
        LEFT JOIN materials m ON m.legacy_file_id=f.id
        LEFT JOIN material_versions mv ON mv.id=m.current_version_id
        LEFT JOIN sources s ON s.id=mv.source_id
        ORDER BY m.stable_id, f.stable_id
        """
    ).fetchall()
    library_root = DEFAULT_STUDY_ROOT.resolve()
    for row in source_rows:
        if str(row["import_mode"] or "") == "canvas_remote" or str(row["domain_authority"] or "") == "REMOTE_AUTHORITATIVE":
            continue
        material_ref = str(row["material_stable_id"] or row["stable_id"] or "")
        if not material_ref:
            raise ValueError("Local source lacks a portable material identity")
        source_path = Path(str(row["original_path"] or "")).expanduser().resolve(strict=False)
        try:
            relative_path = source_path.relative_to(library_root).as_posix()
            if not relative_path or relative_path.startswith("../") or "\\" in relative_path:
                raise ValueError("Unsafe library-relative source path")
            location = {"kind": "library_relative", "relative_path": relative_path}
        except ValueError:
            location = {"kind": "relink_required", "basename": safe_backup_text(source_path.name or row["filename"], limit=240)}
        local_sources.append(
            {
                "stable_id": backup_record_id("backup_source", material_ref),
                "material_ref": material_ref,
                "location": location,
                "display_name": safe_backup_text(row["display_name"] or row["filename"], limit=240),
                "sha256": str(row["sha256"] or row["hash"] or ""),
                "byte_size": int(row["file_size"] or row["size"] or 0),
                "missing": bool(row["source_missing"]),
            }
        )
    datasets["data/local_sources.json"] = local_sources

    notes: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM notes ORDER BY created_at, updated_at, id").fetchall():
        if str(row["target_type"] or "") != "file" or not row["target_id"]:
            raise ValueError("Unsupported note target in backup v1")
        material_ref = material_by_file.get(int(row["target_id"]))
        if not material_ref:
            raise ValueError("Note target lacks a portable material identity")
        body = safe_backup_text(row["body"], limit=10_000)
        notes.append(
            {
                "target_type": "material",
                "target_ref": material_ref,
                "body": body,
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
        )
    datasets["data/notes.json"] = assign_backup_occurrence_ids(
        "note",
        notes,
        identity_fields=("target_type", "target_ref", "body", "created_at"),
    )

    stars: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM stars ORDER BY created_at, id").fetchall():
        if str(row["target_type"] or "") != "file":
            raise ValueError("Unsupported star target in backup v1")
        material_ref = material_by_file.get(int(row["target_id"]))
        if not material_ref:
            raise ValueError("Star target lacks a portable material identity")
        stars.append(
            {
                "stable_id": backup_record_id("star", material_ref),
                "target_type": "material",
                "target_ref": material_ref,
                "created_at": str(row["created_at"] or ""),
            }
        )
    datasets["data/stars.json"] = stars

    study_records: list[dict[str, Any]] = []
    attempt_records: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM attempts ORDER BY created_at, id").fetchall():
        material_ref = material_by_file.get(int(row["file_id"])) if row["file_id"] else None
        answer = safe_backup_text(row["answer_text"], limit=20_000)
        result = safe_backup_text(row["result"], limit=120)
        attempt_records.append(
            {
                "record_type": "attempt",
                "material_ref": material_ref,
                "answer_text": answer,
                "result": result,
                "created_at": str(row["created_at"] or ""),
            }
        )
    study_records.extend(
        assign_backup_occurrence_ids(
            "attempt",
            attempt_records,
            identity_fields=("record_type", "material_ref", "answer_text", "result", "created_at"),
        )
    )
    wrong_question_records: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM wrong_questions ORDER BY created_at, id").fetchall():
        material_ref = material_by_file.get(int(row["source_file_id"])) if row["source_file_id"] else None
        course_ref = course_by_id.get(int(row["course_id"])) if row["course_id"] and int(row["course_id"]) in included_course_ids else None
        question_ref = safe_backup_text(row["question_ref"], limit=240)
        mistake = safe_backup_text(row["mistake"], limit=10_000)
        concept = safe_backup_text(row["concept"], limit=500)
        wrong_question_records.append(
            {
                "record_type": "wrong_question_state",
                "course_ref": course_ref,
                "material_ref": material_ref,
                "learning_unit_label": safe_backup_text(row["week_label"], limit=80),
                "question_ref": question_ref,
                "mistake": mistake,
                "concept": concept,
                "attempt_count": int(row["attempts"] or 0),
                "last_reviewed": str(row["last_reviewed"] or "") or None,
                "mastery": safe_backup_text(row["mastery"], limit=40),
                "created_at": str(row["created_at"] or ""),
            }
        )
    study_records.extend(
        assign_backup_occurrence_ids(
            "wrong",
            wrong_question_records,
            identity_fields=("record_type", "course_ref", "material_ref", "question_ref", "created_at"),
        )
    )
    for row in conn.execute("SELECT * FROM bookmarks ORDER BY created_at, id").fetchall():
        if str(row["target_type"] or "") != "file":
            raise ValueError("Unsupported bookmark target in backup v1")
        material_ref = material_by_file.get(int(row["target_id"]))
        if not material_ref:
            raise ValueError("Bookmark target lacks a portable material identity")
        study_records.append(
            {
                "stable_id": backup_record_id("bookmark", material_ref),
                "record_type": "bookmark",
                "target_ref": material_ref,
                "label": safe_backup_text(row["label"], limit=240),
                "created_at": str(row["created_at"] or ""),
            }
        )
    session_records: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM study_sessions ORDER BY started_at, id").fetchall():
        course_ref = course_by_id.get(int(row["course_id"])) if row["course_id"] and int(row["course_id"]) in included_course_ids else None
        session_records.append(
            {
                "record_type": "study_session",
                "course_ref": course_ref,
                "learning_unit_label": safe_backup_text(row["week_label"], limit=80),
                "mode": safe_backup_text(row["mode"], limit=40),
                "started_at": str(row["started_at"] or ""),
                "ended_at": str(row["ended_at"] or "") or None,
            }
        )
    study_records.extend(
        assign_backup_occurrence_ids(
            "study_session",
            session_records,
            identity_fields=("record_type", "course_ref", "started_at"),
        )
    )
    datasets["data/study_records.json"] = study_records

    # Current AI rows have no portable immutable identity; v1 keeps the member empty.
    datasets["data/ai_history.json"] = []

    # Canvas selection needs provider reconciliation data that v1 deliberately excludes.
    datasets["data/connector_user_state.json"] = []
    seen_state: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT event.stable_id, state.seen_at, state.updated_at
        FROM academic_change_event_state state
        JOIN academic_change_events event ON event.id=state.event_id
        WHERE state.seen_at IS NOT NULL
        ORDER BY event.stable_id
        """
    ).fetchall():
        seen_state.append(
            {
                "stable_id": backup_record_id("change_ack", row["stable_id"]),
                "event_stable_id": str(row["stable_id"]),
                "seen_at": str(row["seen_at"]),
                "updated_at": str(row["updated_at"] or ""),
            }
        )
    datasets["data/whats_new_state.json"] = seen_state

    for member, records in datasets.items():
        require_unique_backup_records(member, records)
    return datasets


def backup_dataset_bytes(member: str, records: list[dict[str, Any]]) -> bytes:
    return canonical_backup_json(
        {
            "dataset": Path(member).stem,
            "format_version": BACKUP_FORMAT_VERSION,
            "records": sorted(records, key=lambda record: str(record.get("stable_id") or record.get("record_id"))),
        }
    )


def safe_backup_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    info.create_system = 3
    return info


BACKUP_RECORD_FIELDS: dict[str, set[str]] = {
    "data/terms.json": {"stable_id", "name", "archived", "sort_order", "created_at", "updated_at"},
    "data/courses.json": {
        "stable_id", "term_ref", "display_name", "course_code", "archived", "active",
        "sort_order", "metadata_locked", "course_kind", "created_at", "updated_at",
    },
    "data/learning_units.json": {
        "stable_id", "course_ref", "label", "number", "kind", "archived", "created_at", "updated_at",
    },
    "data/material_overlays.json": {
        "stable_id", "material_ref", "course_ref", "learning_unit_ref", "display_name",
        "material_type", "category", "exercise_type", "inbox", "active", "removed_at", "updated_at",
    },
    "data/local_sources.json": {
        "stable_id", "material_ref", "location", "display_name", "sha256", "byte_size", "missing",
    },
    "data/notes.json": {"stable_id", "target_type", "target_ref", "body", "created_at", "updated_at"},
    "data/stars.json": {"stable_id", "target_type", "target_ref", "created_at"},
    "data/whats_new_state.json": {"stable_id", "event_stable_id", "seen_at", "updated_at"},
}
BACKUP_STUDY_RECORD_FIELDS: dict[str, set[str]] = {
    "attempt": {"stable_id", "record_type", "material_ref", "answer_text", "result", "created_at"},
    "wrong_question_state": {
        "stable_id", "record_type", "course_ref", "material_ref", "learning_unit_label",
        "question_ref", "mistake", "concept", "attempt_count", "last_reviewed", "mastery", "created_at",
    },
    "bookmark": {"stable_id", "record_type", "target_ref", "label", "created_at"},
    "study_session": {
        "stable_id", "record_type", "course_ref", "learning_unit_label", "mode", "started_at", "ended_at",
    },
}
def validate_backup_record_schema(member: str, record: Any) -> None:
    if not isinstance(record, dict):
        raise ValueError("Backup record must be an object")
    if member in {"data/ai_history.json", "data/connector_user_state.json"}:
        raise ValueError("Backup v1 excluded dataset must remain empty")
    expected = BACKUP_RECORD_FIELDS.get(member)
    if member == "data/study_records.json":
        expected = BACKUP_STUDY_RECORD_FIELDS.get(str(record.get("record_type") or ""))
    if expected is None or set(record) != expected:
        raise ValueError("Backup record schema is invalid")
    if member == "data/local_sources.json":
        location = record.get("location")
        if not isinstance(location, dict):
            raise ValueError("Backup source location is invalid")
        location_kind = location.get("kind")
        expected_location = {"kind", "relative_path"} if location_kind == "library_relative" else {"kind", "basename"}
        if location_kind not in {"library_relative", "relink_required"} or set(location) != expected_location:
            raise ValueError("Backup source location is invalid")
        if location_kind == "library_relative":
            relative = str(location.get("relative_path") or "")
            pure = Path(relative)
            if not relative or pure.is_absolute() or ".." in pure.parts or "\\" in relative:
                raise ValueError("Backup source location is unsafe")


def validate_backup_relationships(parsed: dict[str, list[dict[str, Any]]]) -> None:
    term_ids = {str(record["stable_id"]) for record in parsed["data/terms.json"]}
    course_ids = {str(record["stable_id"]) for record in parsed["data/courses.json"]}
    unit_ids = {str(record["stable_id"]) for record in parsed["data/learning_units.json"]}
    for record in parsed["data/courses.json"]:
        if record["term_ref"] is not None and record["term_ref"] not in term_ids:
            raise ValueError("Backup course references an unknown term")
    for record in parsed["data/learning_units.json"]:
        if record["course_ref"] not in course_ids:
            raise ValueError("Backup learning unit references an unknown course")
    for member in ("data/material_overlays.json", "data/ai_history.json"):
        for record in parsed[member]:
            if record.get("course_ref") is not None and record["course_ref"] not in course_ids:
                raise ValueError("Backup record references an unknown course")
            if record.get("learning_unit_ref") is not None and record["learning_unit_ref"] not in unit_ids:
                raise ValueError("Backup record references an unknown learning unit")


def read_backup_archive_snapshot(path: Path) -> bytes:
    """Read one bounded regular-file snapshot for all validation and parsing."""
    archive_path = Path(path)
    try:
        if archive_path.is_symlink():
            raise ValueError("Backup archive must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(archive_path, flags)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise ValueError("Backup archive is missing or invalid") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > BACKUP_MAX_ARCHIVE_BYTES:
            raise ValueError("Backup archive is missing or too large")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            snapshot = handle.read(BACKUP_MAX_ARCHIVE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(snapshot) > BACKUP_MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive is too large")
    return snapshot


def validate_backup_snapshot(
    snapshot: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Validate and parse the exact immutable archive bytes supplied."""
    if len(snapshot) > BACKUP_MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive is too large")
    try:
        archive_context = zipfile.ZipFile(io.BytesIO(snapshot), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("Backup archive is invalid") from exc
    with archive_context as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > BACKUP_MAX_ENTRIES or len(names) != len(set(names)):
            raise ValueError("Backup archive has duplicate or excessive entries")
        if set(names) != set(BACKUP_MEMBERS):
            raise ValueError("Backup archive contains unexpected or missing entries")
        total_size = 0
        for info in infos:
            pure = Path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if info.filename.startswith(("/", "\\")) or "\\" in info.filename or ".." in pure.parts or pure.is_absolute():
                raise ValueError("Backup archive contains an unsafe path")
            if mode == 0o120000:
                raise ValueError("Backup archive contains a symbolic link")
            if info.flag_bits & 0x1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("Backup archive uses unsupported ZIP features")
            if info.file_size > BACKUP_MAX_ENTRY_BYTES:
                raise ValueError("Backup archive member is too large")
            total_size += info.file_size
        if total_size > BACKUP_MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Backup archive expands beyond the allowed size")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Backup manifest is malformed") from exc
        if manifest.get("format") != BACKUP_FORMAT_NAME or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError("Unsupported backup format version")
        manifest_fields = {
            "format", "format_version", "minimum_restore_format_version", "created_at", "app_version",
            "runtime_profile", "disposable_demo", "features", "entries", "total_records",
        }
        if set(manifest) != manifest_fields or manifest.get("minimum_restore_format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError("Backup manifest schema is invalid")
        expected_features = {
            "restore_supported": False,
            "original_files_included": False,
            "canvas_selection_included": False,
            "ai_history_included": False,
            "whats_new_acknowledgements": "reattach-only",
        }
        if manifest.get("features") != expected_features:
            raise ValueError("Backup manifest features are invalid")
        entries = manifest.get("entries")
        if not isinstance(entries, dict) or set(entries) != set(BACKUP_DATA_MEMBERS):
            raise ValueError("Backup manifest inventory is invalid")
        total_records = 0
        all_portable_ids: set[str] = set()
        parsed_datasets: dict[str, list[dict[str, Any]]] = {}
        for member in BACKUP_DATA_MEMBERS:
            raw = archive.read(member)
            expected = entries.get(member)
            if (
                not isinstance(expected, dict)
                or set(expected) != {"sha256", "byte_size", "record_count"}
                or expected.get("sha256") != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError("Backup member checksum mismatch")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Backup dataset is malformed") from exc
            if set(payload) != {"dataset", "format_version", "records"} or payload.get("format_version") != BACKUP_FORMAT_VERSION:
                raise ValueError("Backup dataset schema is invalid")
            if payload.get("dataset") != Path(member).stem or not isinstance(payload.get("records"), list):
                raise ValueError("Backup dataset identity is invalid")
            records = payload["records"]
            if len(records) != expected.get("record_count") or len(records) > BACKUP_MAX_RECORDS:
                raise ValueError("Backup record count mismatch")
            if expected.get("byte_size") != len(raw):
                raise ValueError("Backup member size mismatch")
            require_unique_backup_records(member, records)
            for record in records:
                validate_backup_record_schema(member, record)
                identity = str(record.get("stable_id") or record.get("record_id"))
                if identity in all_portable_ids:
                    raise ValueError("Backup portable identities collide")
                all_portable_ids.add(identity)
                for key, value in record.items():
                    if key.endswith("_ref") and value is not None and not BACKUP_STABLE_ID_RE.fullmatch(str(value)):
                        raise ValueError("Backup contains a malformed stable reference")
            parsed_datasets[member] = records
            total_records += len(records)
        validate_backup_relationships(parsed_datasets)
        if total_records != manifest.get("total_records"):
            raise ValueError("Backup total record count mismatch")
        validation = {
            "format": BACKUP_FORMAT_NAME,
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": str(manifest.get("created_at") or ""),
            "record_counts": {member: int(entries[member]["record_count"]) for member in BACKUP_DATA_MEMBERS},
            "total_records": total_records,
        }
        return validation, manifest, parsed_datasets


def validate_backup_archive(path: Path) -> dict[str, Any]:
    validation, _manifest, _datasets = validate_backup_snapshot(read_backup_archive_snapshot(path))
    return validation


def export_user_owned_backup(
    conn: sqlite3.Connection,
    *,
    destination_dir: Path | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if conn.in_transaction:
        raise ValueError("Backup export requires a clean database snapshot")
    output_dir = (destination_dir or EXPORT_DIR).resolve()
    data_root = DATA_DIR.resolve()
    if output_dir != data_root and data_root not in output_dir.parents:
        raise PermissionError("Backup destination must remain inside the active StudyHub profile")
    output_dir.mkdir(parents=True, exist_ok=True)
    created = created_at or now_iso()
    temporary_path: Path | None = None
    conn.execute("BEGIN")
    try:
        datasets = project_user_owned_backup(conn)
    finally:
        conn.rollback()
    member_bytes = {member: backup_dataset_bytes(member, records) for member, records in datasets.items()}
    entries = {
        member: {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
            "record_count": len(datasets[member]),
        }
        for member, raw in sorted(member_bytes.items())
    }
    manifest = {
        "format": BACKUP_FORMAT_NAME,
        "format_version": BACKUP_FORMAT_VERSION,
        "minimum_restore_format_version": BACKUP_FORMAT_VERSION,
        "created_at": created,
        "app_version": "0.3.0-beta.1",
        "runtime_profile": RUNTIME_PROFILE,
        "disposable_demo": RUNTIME_PROFILE == "demo-test",
        "features": {
            "restore_supported": False,
            "original_files_included": False,
            "canvas_selection_included": False,
            "ai_history_included": False,
            "whats_new_acknowledgements": "reattach-only",
        },
        "entries": entries,
        "total_records": sum(item["record_count"] for item in entries.values()),
    }
    filename = f"studyhub-backup-v1-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip"
    final_path = output_dir / filename
    try:
        with tempfile.NamedTemporaryFile(prefix=".studyhub-backup-", suffix=".tmp", dir=output_dir, delete=False) as handle:
            temporary_path = Path(handle.name)
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.writestr(safe_backup_zip_info("manifest.json"), canonical_backup_json(manifest))
            for member in BACKUP_DATA_MEMBERS:
                archive.writestr(safe_backup_zip_info(member), member_bytes[member])
        validate_backup_archive(temporary_path)
        os.replace(temporary_path, final_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return {
        "export_id": final_path.stem,
        "filename": final_path.name,
        "byte_size": final_path.stat().st_size,
        "created_at": created,
        "format": BACKUP_FORMAT_NAME,
        "format_version": BACKUP_FORMAT_VERSION,
    }


def export_current_profile_backup(*, created_at: str | None = None) -> dict[str, Any]:
    conn = connect_backup_db_readonly()
    try:
        return export_user_owned_backup(conn, created_at=created_at)
    finally:
        conn.close()


RESTORE_CLASSIFICATIONS = (
    "CREATE",
    "MATCH",
    "UPDATE_USER_STATE",
    "NOOP",
    "UNRESOLVED_REFERENCE",
    "CONFLICT",
    "RELINK_REQUIRED",
    "UNSUPPORTED_BY_V1",
)


def normalize_restore_id(value: Any) -> str:
    restore_id = str(value or "")
    if not RESTORE_ID_RE.fullmatch(restore_id):
        raise ValueError("Invalid restore staging ID")
    return restore_id


def staged_restore_path(restore_id: Any) -> Path:
    normalized = normalize_restore_id(restore_id)
    root = RESTORE_INBOX_DIR.resolve()
    candidate = (root / f"{normalized}.zip").resolve(strict=False)
    if candidate.parent != root:
        raise PermissionError("Restore staging path denied")
    return candidate


def read_validated_backup_archive(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    try:
        snapshot = read_backup_archive_snapshot(path)
        validation, manifest, datasets = validate_backup_snapshot(snapshot)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup archive is invalid") from exc
    return validation, manifest, datasets


def hash_explicit_restore_candidate(path: Path, expected_size: int) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if path.is_symlink() or not path.is_file() or expected_size < 0 or metadata.st_size != expected_size:
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    if path.lstat().st_size != metadata.st_size:
        return ""
    return digest.hexdigest()


def restore_action(
    dataset: str,
    record: dict[str, Any],
    classification: str,
    *,
    label: str,
    differing_fields: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if classification not in RESTORE_CLASSIFICATIONS:
        raise ValueError("Unknown restore action classification")
    action = {
        "dataset": dataset,
        "stable_id": str(record.get("stable_id") or ""),
        "classification": classification,
        "label": safe_backup_text(label, limit=240),
        "differing_fields": sorted(set(differing_fields or [])),
        "reason": reason,
    }
    if not BACKUP_STABLE_ID_RE.fullmatch(action["stable_id"]):
        raise ValueError("Restore action lacks a portable identity")
    return action


def restore_payload(record: dict[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    omitted = {"stable_id"} | (exclude or set())
    return {key: value for key, value in record.items() if key not in omitted}


def restore_differences(
    backup: dict[str, Any],
    current: dict[str, Any],
    *,
    fields: tuple[str, ...],
) -> list[str]:
    return [field for field in fields if backup.get(field) != current.get(field)]


def restore_state_token(value: Any) -> str:
    """Hash private reconciliation state without exposing its local values."""
    return hashlib.sha256(canonical_backup_json(value)).hexdigest()


def restore_record_state_token(
    record: dict[str, Any] | None,
    *,
    fields: tuple[str, ...],
) -> str:
    if record is None:
        return restore_state_token({"presence": "ABSENT"})
    return restore_state_token(
        {
            "presence": "PRESENT",
            "values": {field: record.get(field) for field in fields},
        }
    )


def restore_multiset_state_token(
    backup_records: list[dict[str, Any]],
    current_records: list[dict[str, Any]],
    *,
    identity_fields: tuple[str, ...],
) -> str:
    relevant_identities = {
        canonical_backup_json({field: record.get(field) for field in identity_fields})
        for record in backup_records
    }
    relevant_current = [
        restore_payload(record)
        for record in current_records
        if canonical_backup_json({field: record.get(field) for field in identity_fields})
        in relevant_identities
    ]
    return restore_state_token(sorted(relevant_current, key=canonical_backup_json))


def restore_multiset_actions(
    dataset: str,
    backup_records: list[dict[str, Any]],
    current_records: list[dict[str, Any]],
    *,
    identity_fields: tuple[str, ...],
    label: str,
    allow_state_conflict: bool,
) -> list[dict[str, Any]]:
    current_groups: dict[bytes, list[dict[str, Any]]] = {}
    for record in current_records:
        key = canonical_backup_json({field: record.get(field) for field in identity_fields})
        current_groups.setdefault(key, []).append(record)
    for records in current_groups.values():
        records.sort(key=lambda item: canonical_backup_json(restore_payload(item)))

    actions: list[dict[str, Any]] = []
    for record in sorted(backup_records, key=lambda item: str(item["stable_id"])):
        key = canonical_backup_json({field: record.get(field) for field in identity_fields})
        candidates = current_groups.get(key, [])
        payload = canonical_backup_json(restore_payload(record))
        exact_index = next(
            (index for index, candidate in enumerate(candidates) if canonical_backup_json(restore_payload(candidate)) == payload),
            None,
        )
        if exact_index is not None:
            candidates.pop(exact_index)
            actions.append(restore_action(dataset, record, "NOOP", label=label))
        elif candidates and allow_state_conflict:
            candidate = candidates.pop(0)
            differing = sorted(
                key
                for key in set(restore_payload(record)) | set(restore_payload(candidate))
                if restore_payload(record).get(key) != restore_payload(candidate).get(key)
            )
            actions.append(
                restore_action(
                    dataset,
                    record,
                    "CONFLICT",
                    label=label,
                    differing_fields=differing,
                    reason="local_user_state_differs",
                )
            )
        else:
            actions.append(restore_action(dataset, record, "CREATE", label=label))
    return actions


def build_restore_plan(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    datasets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if conn.in_transaction:
        raise ValueError("Restore preview requires a clean read snapshot")
    actions: list[dict[str, Any]] = []
    source_inspection: dict[str, dict[str, Any]] = {}
    target_state_tokens: dict[str, str] = {}
    conn.execute("BEGIN")
    try:
        current = project_user_owned_backup(conn)
        material_rows = conn.execute(
            """
            SELECT m.stable_id, COALESCE(mv.sha256, f.sha256, f.hash, '') AS sha256,
              COALESCE(mv.byte_size, f.file_size, f.size, 0) AS byte_size
            FROM materials m
            LEFT JOIN material_versions mv ON mv.id=m.current_version_id
            LEFT JOIN files f ON f.id=m.legacy_file_id
            WHERE m.stable_id!=''
            ORDER BY m.stable_id
            """
        ).fetchall()
        event_rows = conn.execute(
            """
            SELECT event.stable_id, state.seen_at
            FROM academic_change_events event
            LEFT JOIN academic_change_event_state state ON state.event_id=event.id
            ORDER BY event.stable_id
            """
        ).fetchall()
    finally:
        conn.rollback()

    current_by_member = {
        member: {str(record["stable_id"]): record for record in records}
        for member, records in current.items()
    }
    current_materials = {
        str(row["stable_id"]): {"sha256": str(row["sha256"] or ""), "byte_size": int(row["byte_size"] or 0)}
        for row in material_rows
    }
    materials_by_hash: dict[str, list[str]] = {}
    for stable_id, item in current_materials.items():
        if item["sha256"]:
            materials_by_hash.setdefault(item["sha256"], []).append(stable_id)
    material_resolution: dict[str, str] = {}
    material_failure: dict[str, str] = {}

    term_fields = ("name", "archived", "sort_order")
    for record in datasets["data/terms.json"]:
        local = current_by_member["data/terms.json"].get(str(record["stable_id"]))
        target_state_tokens[f"term:{record['stable_id']}"] = restore_record_state_token(
            local,
            fields=term_fields,
        )
        if local is None:
            classification, differing = "CREATE", []
        else:
            differing = restore_differences(record, local, fields=term_fields)
            classification = "NOOP" if not differing else "CONFLICT"
        actions.append(restore_action("terms", record, classification, label=str(record["name"]), differing_fields=differing))
    resolvable_terms = set(current_by_member["data/terms.json"]) | {
        str(record["stable_id"]) for record in datasets["data/terms.json"]
    }

    course_fields = (
        "term_ref", "display_name", "course_code", "archived", "active", "sort_order", "metadata_locked", "course_kind",
    )
    resolvable_courses = set(current_by_member["data/courses.json"])
    for record in datasets["data/courses.json"]:
        stable_id = str(record["stable_id"])
        local = current_by_member["data/courses.json"].get(stable_id)
        target_state_tokens[f"course:{stable_id}"] = restore_record_state_token(
            local,
            fields=course_fields,
        )
        term_ref = record.get("term_ref")
        if local is not None:
            differing = restore_differences(record, local, fields=course_fields)
            classification = "NOOP" if not differing else "CONFLICT"
            resolvable_courses.add(stable_id)
        elif record.get("course_kind") == "remote_overlay":
            classification, differing = "UNRESOLVED_REFERENCE", []
        elif term_ref is not None and term_ref not in resolvable_terms:
            classification, differing = "UNRESOLVED_REFERENCE", []
        else:
            classification, differing = "CREATE", []
            resolvable_courses.add(stable_id)
        actions.append(
            restore_action(
                "courses",
                record,
                classification,
                label=str(record["display_name"]),
                differing_fields=differing,
                reason="remote_overlay_requires_existing_course" if classification == "UNRESOLVED_REFERENCE" else "",
            )
        )

    unit_fields = ("course_ref", "label", "number", "kind", "archived")
    resolvable_units = set(current_by_member["data/learning_units.json"])
    for record in datasets["data/learning_units.json"]:
        stable_id = str(record["stable_id"])
        local = current_by_member["data/learning_units.json"].get(stable_id)
        target_state_tokens[f"learning_unit:{stable_id}"] = restore_record_state_token(
            local,
            fields=unit_fields,
        )
        if record.get("course_ref") not in resolvable_courses:
            classification, differing = "UNRESOLVED_REFERENCE", []
        elif local is None:
            classification, differing = "CREATE", []
            resolvable_units.add(stable_id)
        else:
            differing = restore_differences(record, local, fields=unit_fields)
            classification = "NOOP" if not differing else "CONFLICT"
        actions.append(
            restore_action("learning_units", record, classification, label=str(record["label"]), differing_fields=differing)
        )

    study_root = DEFAULT_STUDY_ROOT.resolve()
    for record in datasets["data/local_sources.json"]:
        material_ref = str(record["material_ref"])
        expected_sha = str(record.get("sha256") or "")
        expected_size = int(record.get("byte_size") or 0)
        location = record["location"]
        label = str(record.get("display_name") or "Material")
        current_material = current_materials.get(material_ref)
        target_state_tokens[f"material:{material_ref}"] = restore_state_token(
            {
                "current": current_material or {"presence": "ABSENT"},
                "same_content_candidates": sorted(materials_by_hash.get(expected_sha, [])),
            }
        )
        observed_sha: str | None = ""
        if location.get("kind") == "library_relative" and not bool(record.get("missing")):
            relative = str(location.get("relative_path") or "")
            candidate = (study_root / relative).resolve(strict=False)
            if candidate != study_root and study_root not in candidate.parents:
                raise ValueError("Backup source location is unsafe")
            observed_sha = hash_explicit_restore_candidate(candidate, expected_size)
            source_inspection[material_ref] = {
                "exists": observed_sha is not None,
                "matches": bool(observed_sha and observed_sha == expected_sha),
                "sha256": observed_sha or "",
            }
        if location.get("kind") == "relink_required" or bool(record.get("missing")):
            if current_material is not None:
                material_resolution[material_ref] = material_ref
            classification, reason = "RELINK_REQUIRED", "source_requires_explicit_relink"
        elif observed_sha is None:
            classification, reason = "RELINK_REQUIRED", "source_file_missing"
        elif not observed_sha or observed_sha != expected_sha:
            classification, reason = "CONFLICT", "source_content_differs"
        elif current_material is not None:
            material_resolution[material_ref] = material_ref
            if expected_sha and current_material["sha256"] == expected_sha:
                classification, reason = "NOOP", ""
            elif expected_sha and current_material["sha256"] != expected_sha:
                classification, reason = "CONFLICT", "material_content_differs"
            else:
                classification, reason = "MATCH", ""
        elif len(materials_by_hash.get(expected_sha, [])) == 1:
            material_resolution[material_ref] = materials_by_hash[expected_sha][0]
            classification, reason = "MATCH", ""
        else:
            classification, reason = "RELINK_REQUIRED", "matching_file_is_not_registered"
        if classification in {"CONFLICT", "RELINK_REQUIRED", "UNRESOLVED_REFERENCE"}:
            material_failure[material_ref] = classification
        actions.append(restore_action("local_sources", record, classification, label=label, reason=reason))

    current_overlays = {
        str(record["material_ref"]): record for record in current["data/material_overlays.json"]
    }
    overlay_fields = (
        "course_ref", "learning_unit_ref", "display_name", "material_type", "category", "exercise_type", "inbox", "active", "removed_at",
    )
    for record in datasets["data/material_overlays.json"]:
        backup_ref = str(record["material_ref"])
        resolved_ref = material_resolution.get(backup_ref)
        if not resolved_ref:
            classification = material_failure.get(backup_ref, "UNRESOLVED_REFERENCE")
            differing: list[str] = []
            local = None
        else:
            local = current_overlays.get(resolved_ref)
            normalized = dict(record)
            normalized["material_ref"] = resolved_ref
            if local is None:
                classification, differing = "UPDATE_USER_STATE", []
            else:
                differing = restore_differences(normalized, local, fields=overlay_fields)
                classification = "NOOP" if not differing else "CONFLICT"
        target_state_tokens[f"material_overlay:{record['stable_id']}"] = restore_record_state_token(
            local,
            fields=overlay_fields,
        )
        actions.append(
            restore_action(
                "material_overlays",
                record,
                classification,
                label=str(record["display_name"]),
                differing_fields=differing,
            )
        )

    def resolved_records(records: list[dict[str, Any]], reference_fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        resolved: list[dict[str, Any]] = []
        unresolved_actions: list[dict[str, Any]] = []
        for record in records:
            normalized = dict(record)
            failure = ""
            for field in reference_fields:
                value = record.get(field)
                if value is None:
                    continue
                mapped = material_resolution.get(str(value))
                if mapped:
                    normalized[field] = mapped
                else:
                    failure = material_failure.get(str(value), "UNRESOLVED_REFERENCE")
            if failure:
                unresolved_actions.append(restore_action("user_records", record, failure, label="Study record"))
            else:
                resolved.append(normalized)
        return resolved, unresolved_actions

    backup_notes, note_failures = resolved_records(datasets["data/notes.json"], ("target_ref",))
    actions.extend(note_failures)
    target_state_tokens["notes"] = restore_multiset_state_token(
        backup_notes,
        current["data/notes.json"],
        identity_fields=("target_type", "target_ref", "body", "created_at"),
    )
    actions.extend(
        restore_multiset_actions(
            "notes",
            backup_notes,
            current["data/notes.json"],
            identity_fields=("target_type", "target_ref", "body", "created_at"),
            label="Note",
            allow_state_conflict=True,
        )
    )

    current_stars = {str(record["target_ref"]): record for record in current["data/stars.json"]}
    for record in datasets["data/stars.json"]:
        backup_ref = str(record["target_ref"])
        resolved_ref = material_resolution.get(backup_ref)
        if not resolved_ref:
            classification = material_failure.get(backup_ref, "UNRESOLVED_REFERENCE")
        else:
            classification = "NOOP" if resolved_ref in current_stars else "CREATE"
        target_state_tokens[f"star:{record['stable_id']}"] = restore_state_token(
            {"present": bool(resolved_ref and resolved_ref in current_stars)}
        )
        actions.append(restore_action("stars", record, classification, label="Star"))

    backup_study = datasets["data/study_records.json"]
    current_study = current["data/study_records.json"]
    for record_type, identity_fields, label, conflict in (
        ("attempt", ("record_type", "material_ref", "answer_text", "result", "created_at"), "Attempt", False),
        ("wrong_question_state", ("record_type", "course_ref", "material_ref", "question_ref", "created_at"), "Wrong question", True),
        ("study_session", ("record_type", "course_ref", "started_at"), "Study session", True),
    ):
        selected = [record for record in backup_study if record["record_type"] == record_type]
        refs = ("material_ref",) if record_type in {"attempt", "wrong_question_state"} else ()
        normalized, failures = resolved_records(selected, refs)
        actions.extend(failures)
        target_state_tokens[f"study_records:{record_type}"] = restore_multiset_state_token(
            normalized,
            [record for record in current_study if record["record_type"] == record_type],
            identity_fields=identity_fields,
        )
        actions.extend(
            restore_multiset_actions(
                "study_records",
                normalized,
                [record for record in current_study if record["record_type"] == record_type],
                identity_fields=identity_fields,
                label=label,
                allow_state_conflict=conflict,
            )
        )

    current_bookmarks = {
        str(record["target_ref"]): record
        for record in current_study
        if record["record_type"] == "bookmark"
    }
    for record in [item for item in backup_study if item["record_type"] == "bookmark"]:
        backup_ref = str(record["target_ref"])
        resolved_ref = material_resolution.get(backup_ref)
        if not resolved_ref:
            classification, differing = material_failure.get(backup_ref, "UNRESOLVED_REFERENCE"), []
        elif resolved_ref not in current_bookmarks:
            classification, differing = "CREATE", []
        else:
            local = current_bookmarks[resolved_ref]
            differing = restore_differences(record, local, fields=("label",))
            classification = "NOOP" if not differing else "CONFLICT"
        target_state_tokens[f"bookmark:{record['stable_id']}"] = restore_record_state_token(
            current_bookmarks.get(resolved_ref) if resolved_ref else None,
            fields=("label",),
        )
        actions.append(restore_action("study_records", record, classification, label="Bookmark", differing_fields=differing))

    events = {str(row["stable_id"]): bool(row["seen_at"]) for row in event_rows}
    for record in datasets["data/whats_new_state.json"]:
        event_ref = str(record["event_stable_id"])
        target_state_tokens[f"whats_new:{event_ref}"] = restore_state_token(
            {"present": event_ref in events, "seen": events.get(event_ref)}
        )
        if event_ref not in events:
            classification, reason = "UNSUPPORTED_BY_V1", "academic_event_not_present"
        elif events[event_ref]:
            classification, reason = "NOOP", ""
        else:
            classification, reason = "UPDATE_USER_STATE", ""
        actions.append(restore_action("whats_new_state", record, classification, label="What's New acknowledgement", reason=reason))

    actions.sort(key=lambda item: (item["dataset"], item["stable_id"]))
    summary = {classification.lower(): 0 for classification in RESTORE_CLASSIFICATIONS}
    for action in actions:
        summary[action["classification"].lower()] += 1
    summary["total"] = len(actions)
    sections = [
        {"dataset": dataset, "actions": [action for action in actions if action["dataset"] == dataset]}
        for dataset in sorted({action["dataset"] for action in actions})
    ]
    revision_input = {
        "plan_version": RESTORE_PLAN_VERSION,
        "archive_entries": {
            member: manifest["entries"][member]["sha256"] for member in sorted(manifest["entries"])
        },
        "source_inspection": source_inspection,
        "relevant_target_state": target_state_tokens,
        "actions": actions,
    }
    plan_revision = "restore_" + hashlib.sha256(canonical_backup_json(revision_input)).hexdigest()[:24]
    return {
        "plan_version": RESTORE_PLAN_VERSION,
        "backup": {
            "format": BACKUP_FORMAT_NAME,
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": str(manifest.get("created_at") or ""),
            "total_records": int(manifest.get("total_records") or 0),
            "record_counts": {
                Path(member).stem: int(manifest["entries"][member]["record_count"])
                for member in BACKUP_DATA_MEMBERS
            },
        },
        "summary": summary,
        "sections": sections,
        "conflicts": [action for action in actions if action["classification"] == "CONFLICT"],
        "unresolved": [
            action
            for action in actions
            if action["classification"] in {"UNRESOLVED_REFERENCE", "RELINK_REQUIRED", "UNSUPPORTED_BY_V1"}
        ],
        "warnings": [
            "original_files_not_included",
            "ai_history_not_included",
            "canvas_state_not_included",
            "review_only_apply_unavailable",
        ],
        "plan_revision": plan_revision,
    }


def preview_staged_restore(restore_id: Any) -> dict[str, Any]:
    path = staged_restore_path(restore_id)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Staged backup not found")
    _validation, manifest, datasets = read_validated_backup_archive(path)
    conn = connect_backup_db_readonly()
    try:
        return build_restore_plan(conn, manifest, datasets)
    finally:
        conn.close()


def discard_staged_restore(restore_id: Any) -> dict[str, Any]:
    path = staged_restore_path(restore_id)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Staged backup not found")
    path.unlink()
    return {"ok": True, "discarded": normalize_restore_id(restore_id)}


def normalize_academic_change_event_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("event_ids must be a list")
    if not values or len(values) > WHATS_NEW_MAX_MARK_SEEN:
        raise ValueError(f"event_ids must contain between 1 and {WHATS_NEW_MAX_MARK_SEEN} items")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"change_[0-9a-f]{20}", value):
            raise ValueError("Invalid academic change event id")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def mark_academic_changes_seen(conn: sqlite3.Connection, event_stable_ids: list[str]) -> dict[str, Any]:
    event_ids = normalize_academic_change_event_ids(event_stable_ids)
    placeholders = ",".join("?" for _ in event_ids)
    rows = conn.execute(
        f"SELECT id, stable_id FROM academic_change_events WHERE stable_id IN ({placeholders})",
        event_ids,
    ).fetchall()
    if {str(row["stable_id"]) for row in rows} != set(event_ids):
        raise ValueError("Unknown academic change event id")
    now = now_iso()
    for row in rows:
        conn.execute(
            """
            INSERT INTO academic_change_event_state(event_id, seen_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (row["id"], now, now),
        )
    return {"ok": True, "marked": len(rows)}


def apply_canvas_file_sync(
    conn: sqlite3.Connection,
    connector: CanvasConnector,
    expected_plan_revision: str,
    requested_remote_file_stable_ids: list[str],
    *,
    requested_offering_ids: list[str] | None = None,
    open_url: Callable[[str, int], Any] = default_canvas_download_open,
    fail_after_ingestion: int | None = None,
    fail_baseline: bool = False,
    fail_after_event: int | None = None,
) -> dict[str, Any]:
    plan = canvas_file_sync_plan(conn, connector, requested_offering_ids)
    if not expected_plan_revision or plan["plan_revision"] != expected_plan_revision:
        raise CanvasConnectorError("stale_canvas_sync_plan")
    actions: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for section in plan["offerings"]:
        for action in section["actions"]:
            actions[str(action["remote_file_stable_id"])] = (section, action)
    requested = normalize_requested_canvas_file_ids(requested_remote_file_stable_ids)
    if any(stable_id not in actions for stable_id in requested):
        raise CanvasConnectorError("unknown_canvas_sync_item")
    authority_id = canvas_authority_id(connector.record.origin)
    offerings = {
        str(row["stable_id"]): row
        for row in selected_canvas_offerings(conn, authority_id, [section["course_offering_id"] for section, _ in actions.values()])
    }
    acquired: list[CanvasAcquiredFile] = []
    metadata_only: list[tuple[sqlite3.Row, CanvasRemoteFile, str]] = []
    noops: list[dict[str, str]] = []
    try:
        for stable_id in requested:
            section, action = actions[stable_id]
            offering = offerings[str(section["course_offering_id"])]
            kind = str(action["kind"])
            if kind in {"ADD", "POTENTIAL_CONTENT_CHANGE"}:
                acquired.append(acquire_canvas_file(connector, offering, action, open_url=open_url))
            elif kind == "METADATA_CHANGE":
                authoritative = connector.get_course_file(str(offering["remote_course_id"]), str(action["remote_file_id"]))
                expected = canvas_action_remote_file(action)
                if canvas_remote_revision_fingerprint(authoritative.remote_file) != canvas_remote_revision_fingerprint(expected):
                    raise CanvasConnectorError("stale_canvas_sync_plan")
                material = conn.execute(
                    """
                    SELECT m.id FROM materials m
                    JOIN material_versions mv ON mv.id=m.current_version_id
                    JOIN sources s ON s.id=mv.source_id
                    WHERE m.stable_id=? AND s.provider_kind='canvas' AND s.authority='REMOTE_AUTHORITATIVE'
                    """,
                    (stable_id,),
                ).fetchone()
                if material is None:
                    raise CanvasConnectorError("canvas_material_not_acquired")
                metadata_only.append((offering, authoritative.remote_file, stable_id))
            elif kind == "UNCHANGED":
                noops.append({"remote_file_stable_id": stable_id, "status": "unchanged"})
            else:
                raise CanvasConnectorError("canvas_sync_action_not_applicable")
        for item in acquired:
            section, _action = actions[item.remote_file_stable_id]
            offering = offerings[str(section["course_offering_id"])]
            authoritative = connector.get_course_file(
                str(offering["remote_course_id"]), item.remote_file.remote_file_id
            )
            if canvas_remote_revision_fingerprint(authoritative.remote_file) != canvas_remote_revision_fingerprint(item.remote_file):
                raise CanvasConnectorError("canvas_file_changed_during_acquisition")
        for offering, remote_file, _stable_id in metadata_only:
            authoritative = connector.get_course_file(str(offering["remote_course_id"]), remote_file.remote_file_id)
            if canvas_remote_revision_fingerprint(authoritative.remote_file) != canvas_remote_revision_fingerprint(remote_file):
                raise CanvasConnectorError("stale_canvas_sync_plan")
        offering_ids = [int(row["id"]) for row in offerings.values()]
        if canvas_baseline_revision(conn, offering_ids) != plan["local_baseline_revision"]:
            raise CanvasConnectorError("stale_canvas_sync_plan")
        if not conn.in_transaction:
            conn.execute("BEGIN")
        savepoint = "canvas_file_sync_apply"
        conn.execute(f"SAVEPOINT {savepoint}")
        results: list[dict[str, Any]] = list(noops)
        baseline_updates: dict[int, tuple[sqlite3.Row, list[CanvasRemoteFile]]] = {}
        applied_at = now_iso()
        event_count = 0
        try:
            for index, item in enumerate(acquired):
                section, action = actions[item.remote_file_stable_id]
                offering = offerings[str(section["course_offering_id"])]
                course, week = ensure_canvas_course_binding(conn, offering)
                provenance = IngestionSourceProvenance(
                    stable_source_id=canvas_source_stable_id(
                        str(offering["authority_id"]), str(offering["stable_id"]), item.remote_file.remote_file_id
                    ),
                    provider_kind="canvas",
                    authority="REMOTE_AUTHORITATIVE",
                    connector_id=str(offering["authority_id"]),
                    remote_id=item.remote_file.remote_file_id,
                    remote_updated_at=item.remote_file.remote_updated_at or item.remote_file.remote_modified_at or "",
                    fetched_at=now_iso(),
                    source_label="Canvas",
                    source_type="Canvas",
                    import_mode="canvas_remote",
                )
                result = ingest_material_candidate(
                    conn,
                    IngestionCandidate(
                        path=item.retained_path,
                        course_id=int(course["id"]),
                        week_id=int(week["id"]),
                        course_code=str(course["course_code"] or ""),
                        course_name=str(course["display_name"] or course["name"]),
                        week_label="Canvas Files",
                        week_number=None,
                        section="Canvas Files",
                        category="Canvas",
                        exercise_type="",
                        material_type="other",
                        source="official",
                        source_label="Canvas",
                        source_type="Canvas",
                        rel_path=f"canvas/{item.remote_file_stable_id}/{item.sha256}{item.extension}",
                        import_mode="canvas_remote",
                        is_official=1,
                        is_solution=is_solution_file(Path(item.remote_file.filename), "Canvas"),
                        is_question_source=is_question_source_file(Path(item.remote_file.filename), "Canvas Files", "Canvas"),
                        display_name=item.remote_file.display_name or item.remote_file.filename,
                        stable_id=item.remote_file_stable_id,
                        provenance=provenance,
                        remote_managed_identity=True,
                    ),
                )
                if fail_after_ingestion is not None and index + 1 >= fail_after_ingestion:
                    raise RuntimeError("synthetic_canvas_ingestion_failure")
                version_stable_id, version_sha256 = current_material_version_identity(
                    conn,
                    result.material_stable_id,
                )
                if str(action["kind"]) == "ADD":
                    event_kind = "CANVAS_FILE_ADDED"
                elif result.version_changed:
                    event_kind = "CANVAS_FILE_CONTENT_UPDATED"
                else:
                    event_kind = "CANVAS_FILE_METADATA_UPDATED"
                resulting_revision_id = (
                    version_stable_id
                    if event_kind != "CANVAS_FILE_METADATA_UPDATED"
                    else canvas_remote_revision_fingerprint(item.remote_file)
                )
                predecessor_event_stable_id = latest_academic_change_event_id(
                    conn,
                    authority_id=str(offering["authority_id"]),
                    offering_stable_id=str(offering["stable_id"]),
                    remote_item_stable_id=item.remote_file_stable_id,
                )
                record_academic_change_event(
                    conn,
                    event_kind=event_kind,
                    offering=offering,
                    remote_item_stable_id=item.remote_file_stable_id,
                    predecessor_event_stable_id=predecessor_event_stable_id,
                    material_stable_id=result.material_stable_id,
                    material_label=item.remote_file.display_name or item.remote_file.filename,
                    changed_fields=list(action.get("changed_fields") or []),
                    resulting_revision_id=resulting_revision_id,
                    resulting_material_version_stable_id=version_stable_id,
                    resulting_sha256=version_sha256,
                    applied_at=applied_at,
                )
                event_count += 1
                if fail_after_event is not None and event_count >= fail_after_event:
                    raise RuntimeError("synthetic_academic_event_failure")
                baseline_updates.setdefault(int(offering["id"]), (offering, []))[1].append(item.remote_file)
                results.append(
                    {
                        "remote_file_stable_id": item.remote_file_stable_id,
                        "status": "applied",
                        "material_stable_id": result.material_stable_id,
                        "sha256": result.sha256,
                        "new_version": result.version_changed,
                    }
                )
            for offering, remote_file, stable_id in metadata_only:
                _section, action = actions[stable_id]
                version_stable_id, version_sha256 = current_material_version_identity(conn, stable_id)
                predecessor_event_stable_id = latest_academic_change_event_id(
                    conn,
                    authority_id=str(offering["authority_id"]),
                    offering_stable_id=str(offering["stable_id"]),
                    remote_item_stable_id=stable_id,
                )
                record_academic_change_event(
                    conn,
                    event_kind="CANVAS_FILE_METADATA_UPDATED",
                    offering=offering,
                    remote_item_stable_id=stable_id,
                    predecessor_event_stable_id=predecessor_event_stable_id,
                    material_stable_id=stable_id,
                    material_label=remote_file.display_name or remote_file.filename,
                    changed_fields=list(action.get("changed_fields") or []),
                    resulting_revision_id=canvas_remote_revision_fingerprint(remote_file),
                    resulting_material_version_stable_id=version_stable_id,
                    resulting_sha256=version_sha256,
                    applied_at=applied_at,
                )
                event_count += 1
                if fail_after_event is not None and event_count >= fail_after_event:
                    raise RuntimeError("synthetic_academic_event_failure")
                baseline_updates.setdefault(int(offering["id"]), (offering, []))[1].append(remote_file)
                results.append({"remote_file_stable_id": stable_id, "status": "metadata_updated"})
            if canvas_baseline_revision(conn, offering_ids) != plan["local_baseline_revision"]:
                raise CanvasConnectorError("stale_canvas_sync_plan")
            for _offering_id, (offering, remote_files) in sorted(baseline_updates.items()):
                upsert_canvas_file_sync_baselines(
                    conn,
                    offering,
                    remote_files,
                    fail_after=1 if fail_baseline else None,
                )
            refresh_course_week_counts(conn)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return {"plan_revision": plan["plan_revision"], "results": results}
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            cleanup_new_canvas_retained_files(acquired)
            raise
    except Exception:
        cleanup_new_canvas_retained_files(acquired)
        raise


def run_canvas_sync_apply(
    connector: CanvasConnector,
    expected_plan_revision: str,
    requested_remote_file_stable_ids: list[str],
    *,
    requested_offering_ids: list[str] | None = None,
    open_url: Callable[[str, int], Any] = default_canvas_download_open,
) -> dict[str, Any]:
    """Serialize one complete local Canvas apply through its caller-owned commit."""

    with _CANVAS_SYNC_APPLY_LOCK:
        conn = connect_db()
        init_db(conn)
        try:
            result = apply_canvas_file_sync(
                conn,
                connector,
                expected_plan_revision,
                requested_remote_file_stable_ids,
                requested_offering_ids=requested_offering_ids,
                open_url=open_url,
            )
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS terms (
          id INTEGER PRIMARY KEY,
          stable_id TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          archived INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
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
        CREATE TABLE IF NOT EXISTS ai_conversations (
          id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          scope TEXT,
          context_json TEXT NOT NULL DEFAULT '{}',
          course_code TEXT,
          week_label TEXT,
          file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ai_messages (
          id INTEGER PRIMARY KEY,
          conversation_id INTEGER NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL,
          body TEXT NOT NULL,
          status TEXT,
          sources_json TEXT NOT NULL DEFAULT '[]',
          questions_json TEXT NOT NULL DEFAULT '[]',
          solutions_json TEXT NOT NULL DEFAULT '[]',
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
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
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
        "courses",
        {
            "stable_id": "TEXT DEFAULT ''",
            "display_name": "TEXT DEFAULT ''",
            "course_code": "TEXT DEFAULT ''",
            "term_id": "INTEGER REFERENCES terms(id)",
            "archived": "INTEGER NOT NULL DEFAULT 0",
            "source_folder": "TEXT DEFAULT ''",
            "source_kind": "TEXT NOT NULL DEFAULT 'folder'",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "removed_at": "TEXT",
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
            "metadata_locked": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    ensure_columns(
        conn,
        "weeks",
        {
            "stable_id": "TEXT DEFAULT ''",
            "kind": "TEXT NOT NULL DEFAULT 'week'",
            "origin": "TEXT NOT NULL DEFAULT 'scan'",
            "archived": "INTEGER NOT NULL DEFAULT 0",
            "removed_at": "TEXT",
            "created_at": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''",
        },
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
            "display_name": "TEXT DEFAULT ''",
            "material_type": "TEXT DEFAULT ''",
            "import_mode": "TEXT NOT NULL DEFAULT 'scanned'",
            "inbox": "INTEGER NOT NULL DEFAULT 0",
            "metadata_locked": "INTEGER NOT NULL DEFAULT 0",
            "source_missing": "INTEGER NOT NULL DEFAULT 0",
            "removed_at": "TEXT",
            "material_created_at": "TEXT DEFAULT ''",
            "material_updated_at": "TEXT DEFAULT ''",
        },
    )
    now = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO terms(stable_id, name, archived, sort_order, created_at, updated_at) VALUES ('term_imported', 'Imported Courses', 0, 0, ?, ?)",
        (now, now),
    )
    # v0.2.0 removes only records that carry the old bundled-fixture provenance.
    # Names and course codes are deliberately not used as deletion criteria.
    conn.execute("DELETE FROM courses WHERE source_kind='demo'")
    conn.execute("DELETE FROM terms WHERE stable_id='term_demo' AND NOT EXISTS (SELECT 1 FROM courses WHERE term_id=terms.id)")
    imported_term_id = conn.execute("SELECT id FROM terms WHERE stable_id='term_imported'").fetchone()["id"]
    course_rows = conn.execute("SELECT id, folder_name, created_at, code, name, path, stable_id FROM courses").fetchall()
    for row in course_rows:
        stable_id = row["stable_id"] or deterministic_stable_id("course", f"{row['folder_name']}|{row['created_at']}")
        conn.execute(
            """
            UPDATE courses SET stable_id=?, display_name=COALESCE(NULLIF(display_name, ''), name),
              course_code=COALESCE(NULLIF(course_code, ''), code), term_id=COALESCE(term_id, ?),
              source_folder=COALESCE(NULLIF(source_folder, ''), path)
            WHERE id=?
            """,
            (stable_id, imported_term_id, row["id"]),
        )
    week_rows = conn.execute("SELECT id, course_id, week_label, stable_id, created_at, updated_at FROM weeks").fetchall()
    for row in week_rows:
        stable_id = row["stable_id"] or deterministic_stable_id("week", f"{row['course_id']}|{row['week_label']}")
        conn.execute(
            "UPDATE weeks SET stable_id=?, created_at=COALESCE(NULLIF(created_at, ''), ?), updated_at=COALESCE(NULLIF(updated_at, ''), ?) WHERE id=?",
            (stable_id, now, now, row["id"]),
        )
    conn.execute("UPDATE files SET stable_id=COALESCE(NULLIF(stable_id, ''), hash) WHERE stable_id='' OR stable_id IS NULL")
    conn.execute("UPDATE files SET absolute_path=original_path WHERE absolute_path='' OR absolute_path IS NULL")
    conn.execute("UPDATE files SET file_extension=extension WHERE file_extension='' OR file_extension IS NULL")
    conn.execute("UPDATE files SET file_size=size WHERE file_size=0 OR file_size IS NULL")
    conn.execute("UPDATE files SET sha256=hash WHERE sha256='' OR sha256 IS NULL")
    conn.execute("UPDATE files SET display_name=filename WHERE display_name='' OR display_name IS NULL")
    conn.execute("UPDATE files SET material_created_at=indexed_at WHERE material_created_at='' OR material_created_at IS NULL")
    conn.execute("UPDATE files SET material_updated_at=indexed_at WHERE material_updated_at='' OR material_updated_at IS NULL")
    conn.execute(
        """
        UPDATE files SET material_type=CASE lower(COALESCE(NULLIF(material_type, ''), category, 'other'))
          WHEN 'lecture' THEN 'lecture' WHEN 'tutorial' THEN 'tutorial'
          WHEN 'workshop' THEN 'workshop' WHEN 'lab' THEN 'lab'
          WHEN 'quiz' THEN 'quiz' WHEN 'assignment' THEN 'assignment'
          WHEN 'reading' THEN 'reading' WHEN 'exam' THEN 'exam'
          ELSE 'other' END
        """
    )
    conn.execute("UPDATE files SET source_missing=CASE WHEN active=0 AND missing_at IS NOT NULL THEN 1 ELSE source_missing END")
    conn.execute("UPDATE courses SET active=0 WHERE source_kind='folder' AND removed_at IS NULL AND NOT EXISTS (SELECT 1 FROM files WHERE files.course_id=courses.id AND files.active=1)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_stable_id ON courses(stable_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_weeks_stable_id ON weeks(stable_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_courses_term_active ON courses(term_id, archived, active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_management ON files(course_id, week_id, material_type, inbox, active)")
    reconcile_domain_projection(conn)
    conn.commit()


def ensure_term(conn: sqlite3.Connection, name: str, *, stable_id: str | None = None) -> sqlite3.Row:
    clean_name = clean_metadata_text(name, "Term name")
    if stable_id:
        row = conn.execute("SELECT * FROM terms WHERE stable_id=?", (stable_id,)).fetchone()
        if row:
            return row
    existing = conn.execute("SELECT * FROM terms WHERE lower(name)=lower(?) AND archived=0", (clean_name,)).fetchone()
    if existing:
        return existing
    now = now_iso()
    conn.execute(
        "INSERT INTO terms(stable_id, name, archived, sort_order, created_at, updated_at) VALUES (?, ?, 0, 0, ?, ?)",
        (stable_id or new_stable_id("term"), clean_name, now, now),
    )
    return conn.execute("SELECT * FROM terms WHERE id=last_insert_rowid()").fetchone()


def ensure_week_record(
    conn: sqlite3.Connection,
    course_id: int,
    label: str,
    *,
    number: int | None = None,
    path: str = "",
    origin: str = "managed",
    kind: str | None = None,
) -> sqlite3.Row:
    clean_label = clean_metadata_text(label, "Week or module name", limit=80)
    row = conn.execute(
        "SELECT * FROM weeks WHERE course_id=? AND week_label=? AND removed_at IS NULL",
        (course_id, clean_label),
    ).fetchone()
    if row:
        if path:
            conn.execute("UPDATE weeks SET path=?, updated_at=? WHERE id=?", (path, now_iso(), row["id"]))
            row = conn.execute("SELECT * FROM weeks WHERE id=?", (row["id"],)).fetchone()
        return row
    detected_number = number
    if detected_number is None:
        match = re.search(r"\b(\d{1,3})\b", clean_label)
        detected_number = int(match.group(1)) if match else None
    week_kind = kind or ("week" if detected_number is not None and clean_label.lower().startswith("week") else "module")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO weeks(stable_id, course_id, week_label, week_number, path, has_materials, file_count,
          kind, origin, archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 0, ?, ?)
        """,
        (new_stable_id("week"), course_id, clean_label, detected_number, path, week_kind, origin, now, now),
    )
    return conn.execute("SELECT * FROM weeks WHERE id=last_insert_rowid()").fetchone()


def refresh_course_week_counts(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE weeks SET has_materials=0, file_count=0")
    conn.execute(
        """
        UPDATE weeks SET
          file_count=(SELECT COUNT(*) FROM files WHERE files.week_id=weeks.id AND files.active=1 AND files.removed_at IS NULL),
          has_materials=CASE WHEN (SELECT COUNT(*) FROM files WHERE files.week_id=weeks.id AND files.active=1 AND files.removed_at IS NULL) > 0 THEN 1 ELSE 0 END
        WHERE removed_at IS NULL
        """
    )
    conn.execute(
        """
        UPDATE courses SET active=CASE
          WHEN source_kind IN ('managed','system') AND removed_at IS NULL THEN 1
          WHEN EXISTS (SELECT 1 FROM files WHERE files.course_id=courses.id AND files.active=1 AND files.removed_at IS NULL) THEN 1
          ELSE 0 END
        """,
    )


def ensure_inbox_course(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM courses WHERE source_kind='system' AND folder_name=?", (SYSTEM_INBOX_FOLDER,)).fetchone()
    if row:
        return row
    term = ensure_term(conn, "Imported Courses", stable_id="term_imported")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path, source_folder,
          source_kind, term_id, archived, active, created_at, updated_at)
        VALUES (?, '', '', 'Unclassified', 'Unclassified', ?, '', '', 'system', ?, 0, 1, ?, ?)
        """,
        ("course_inbox", SYSTEM_INBOX_FOLDER, term["id"], now, now),
    )
    course = conn.execute("SELECT * FROM courses WHERE id=last_insert_rowid()").fetchone()
    ensure_week_record(conn, course["id"], "Unclassified", origin="system", kind="module")
    return course


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


@dataclass(frozen=True)
class IngestionIdentity:
    sha256: str
    size: int
    mtime_ns: int
    modified_at: str
    extension: str
    mime_type: str
    suspicious: str


@dataclass(frozen=True)
class IngestionSourceProvenance:
    stable_source_id: str
    provider_kind: str
    authority: str
    connector_id: str
    remote_id: str
    remote_updated_at: str
    fetched_at: str
    source_label: str
    source_type: str
    import_mode: str


@dataclass(frozen=True)
class IngestionCandidate:
    path: Path
    course_id: int
    week_id: int | None
    course_code: str
    course_name: str
    week_label: str
    week_number: int | None
    section: str
    category: str
    exercise_type: str
    material_type: str
    source: str
    source_label: str
    source_type: str
    rel_path: str
    import_mode: str
    is_official: int
    is_solution: int
    is_question_source: int
    inbox: int = 0
    metadata_locked: int = 0
    display_name: str = ""
    stable_id: str | None = None
    force_index: bool = False
    identity: IngestionIdentity | None = None
    provenance: IngestionSourceProvenance | None = None
    remote_managed_identity: bool = False


@dataclass(frozen=True)
class IngestionResult:
    status: str
    legacy_file_id: int
    material_stable_id: str
    sha256: str
    version_changed: bool
    indexed: bool
    suspicious: str
    error: str
    questions: int = 0
    solutions: int = 0


class IngestionSourceChanged(RuntimeError):
    pass


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


def text_cache_available(text_cache: str) -> bool:
    if not text_cache:
        return False
    try:
        cache_path = safe_cache_path(text_cache)
    except PermissionError:
        return False
    return bool(cache_path and cache_path.exists() and cache_path.stat().st_size > 0)


def text_cache_matches_current_pipeline(text_cache: str, digest: str) -> bool:
    if not text_cache:
        return False
    try:
        cache_path = safe_cache_path(text_cache)
    except PermissionError:
        return False
    return bool(
        cache_path
        and cache_path.name == text_cache_filename(digest)
        and cache_path.exists()
        and cache_path.stat().st_size > 0
    )


def should_retry_incomplete_index(
    existing: sqlite3.Row,
    ext: str,
    chunk_count: int,
    local_ai_state: sqlite3.Row | None,
    digest: str,
) -> bool:
    if ext not in TEXT_EXTRACTABLE_EXTS:
        return False
    status = local_ai_state["status"] if local_ai_state else ""
    error = local_ai_state["error"] if local_ai_state and "error" in local_ai_state.keys() else ""
    return (
        not text_cache_matches_current_pipeline(existing["text_cache_path"] or "", digest)
        or chunk_count == 0
        or status in {"not_indexed", "failed", "error", "missing"}
        or bool(error)
        or bool(existing["ai_index_error"])
    )


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
    elif file_row["is_official"]:
        for solution in detect_solution_headings_from_chunks(file_row, chunks):
            conn.execute(
                """
                INSERT INTO solutions(file_id, course_code, week_label, exercise_type, solution_label, source_location, official_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    file_id,
                    solution["course_code"],
                    solution["week_label"],
                    solution["exercise_type"],
                    solution["solution_label"],
                    solution["source_location"],
                    now_iso(),
                ),
            )
            solution_count += 1
    status = "indexed" if file_row["is_official"] and not file_row["suspicious"] and text else "not_indexed"
    if status == "indexed":
        error = ""
    elif text:
        error = file_row["suspicious"]
    else:
        error = file_row["ai_index_error"] or "No extracted text"
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


def _legacy_scan_library(study_root: Path = DEFAULT_STUDY_ROOT) -> ScanStats:
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
                ai_seen = conn.execute("SELECT status, error FROM ai_index_state WHERE file_id=? AND provider='local'", (existing["id"],)).fetchone()
                same_file = existing["sha256"] == digest and existing["modified_at"] == modified
                needs_reindex = not same_file or should_retry_incomplete_index(existing, ext, chunk_count, ai_seen, digest)
                if needs_reindex:
                    stats.updated_files += 1
                else:
                    stats.unchanged_files += 1
            else:
                stats.new_files += 1
            if needs_reindex and not suspicious:
                text = extract_text(path)
                extraction_error = extraction_error_for(path, text)
                if text:
                    text_cache_path = expected_text_cache_path(digest)
                    text_cache_path.write_text(text, encoding="utf-8", errors="ignore")
                    text_cache = str(text_cache_path)
                    chunks = extract_document_chunks(path, text)
                    stats.indexed_text += 1
                else:
                    text_cache = ""
            elif text_cache_available(text_cache):
                cache_path = safe_cache_path(text_cache)
                text = cache_path.read_text(encoding="utf-8", errors="ignore") if cache_path else ""
                extraction_error = ""
            else:
                extraction_error = ""
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
                if extraction_error:
                    conn.execute("UPDATE files SET ai_index_error=? WHERE id=?", (extraction_error, file_id))
                    file_row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
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
    all_rows = conn.execute("SELECT id, original_path FROM files WHERE import_mode!='canvas_remote'").fetchall()
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
    conn.commit()
    conn.close()
    log_event("scan", json.dumps({"files": stats.files, "root": "configured-study-library"}, ensure_ascii=False))
    return stats


def ingestion_stat_fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def identity_matches_path(path: Path, identity: IngestionIdentity) -> bool:
    try:
        size, mtime_ns = ingestion_stat_fingerprint(path)
    except OSError:
        return False
    return size == identity.size and mtime_ns == identity.mtime_ns


def identify_ingestion_path(path: Path) -> IngestionIdentity:
    path = path.expanduser().resolve()
    for _attempt in range(3):
        before_size, before_mtime_ns = ingestion_stat_fingerprint(path)
        digest = sha256_file(path)
        stat = path.stat()
        if before_size == stat.st_size and before_mtime_ns == stat.st_mtime_ns:
            break
    else:
        raise IngestionSourceChanged("Source file changed during identification. Try again.")
    modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
    ext = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return IngestionIdentity(
        sha256=digest,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        modified_at=modified,
        extension=ext,
        mime_type=mime,
        suspicious=is_suspicious_file(path),
    )


def index_material_content(
    conn: sqlite3.Connection,
    file_id: int,
    path: Path,
    *,
    force: bool = False,
    identity: IngestionIdentity | None = None,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise FileNotFoundError("Material record was not found")
    if not path.exists() or not path.is_file():
        conn.execute(
            "UPDATE files SET source_missing=1, missing_at=COALESCE(missing_at, ?), ai_index_status='missing', ai_index_error='Original file missing' WHERE id=?",
            (now_iso(), file_id),
        )
        return {"indexed": False, "missing": True, "updated": False, "questions": 0, "solutions": 0}
    identified = identity or identify_ingestion_path(path)
    digest = identified.sha256
    modified = identified.modified_at
    ext = identified.extension
    suspicious = identified.suspicious
    chunk_count = conn.execute("SELECT COUNT(*) AS c FROM document_chunks WHERE file_id=?", (file_id,)).fetchone()["c"]
    ai_seen = conn.execute("SELECT status, error FROM ai_index_state WHERE file_id=? AND provider='local'", (file_id,)).fetchone()
    same_file = row["sha256"] == digest and row["modified_at"] == modified
    needs_reindex = force or not same_file or should_retry_incomplete_index(row, ext, chunk_count, ai_seen, digest)
    text_cache = row["text_cache_path"] or ""
    text = ""
    chunks: list[dict[str, Any]] = []
    extraction_error = ""
    if needs_reindex and not suspicious:
        text = extract_text(path)
        extraction_error = extraction_error_for(path, text)
        if text:
            text_cache_path = expected_text_cache_path(digest)
            text_cache_path.write_text(text, encoding="utf-8", errors="ignore")
            text_cache = str(text_cache_path)
            chunks = extract_document_chunks(path, text)
        else:
            text_cache = ""
    elif text_cache_available(text_cache):
        cache_path = safe_cache_path(text_cache)
        text = cache_path.read_text(encoding="utf-8", errors="ignore") if cache_path else ""
    mime = identified.mime_type
    stable_id = row["stable_id"] or new_stable_id("material")
    conn.execute(
        """
        UPDATE files SET filename=?, hash=?, size=?, modified_at=?, indexed_at=?, extension=?, mime_type=?,
          suspicious=?, text_cache_path=?, stable_id=?, absolute_path=?, file_extension=?, file_size=?, sha256=?,
          source_missing=0, missing_at=NULL, active=1, ai_index_error=? WHERE id=?
        """,
        (path.name, digest, identified.size, modified, now_iso(), ext, mime, suspicious, text_cache, stable_id,
         str(path), ext, identified.size, digest, extraction_error, file_id),
    )
    upsert_file_version(conn, file_id, stable_id, digest, identified.size, modified, text_cache)
    question_count = solution_count = 0
    if needs_reindex:
        current = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        _chunk_count, question_count, solution_count = rebuild_file_index(conn, file_id, text, chunks, current)
    return {
        "indexed": bool(text),
        "missing": False,
        "updated": needs_reindex,
        "questions": question_count,
        "solutions": solution_count,
    }


def ingest_material_candidate(conn: sqlite3.Connection, candidate: IngestionCandidate) -> IngestionResult:
    """Central local ingestion boundary for current scanner/import inputs.

    Callers own discovery, duplicate policy, missing-file reconciliation, and
    commit/reconcile policy. This helper owns per-file identity, compatibility
    persistence, extraction, indexing, and version recording.
    """

    path = candidate.path.expanduser().resolve()
    if candidate.remote_managed_identity:
        if candidate.provenance is None or not candidate.stable_id or not is_inside(path, canvas_remote_content_root()):
            raise PermissionError("Remote-managed ingestion requires trusted StudyHub retained content")
    elif candidate.provenance is not None:
        raise PermissionError("Remote provenance requires remote-managed ingestion")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    supplied_identity = candidate.identity
    last_source_change: IngestionSourceChanged | None = None
    for attempt in range(2):
        identity = supplied_identity if attempt == 0 and supplied_identity and identity_matches_path(path, supplied_identity) else identify_ingestion_path(path)
        savepoint = f"ingest_{hashlib.sha256(f'{id(conn)}|{path}|{attempt}|{now_iso()}'.encode()).hexdigest()[:16]}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            if not identity_matches_path(path, identity):
                raise IngestionSourceChanged("Source file changed before ingestion. Try again.")
            if candidate.remote_managed_identity:
                existing = conn.execute("SELECT * FROM files WHERE stable_id=?", (candidate.stable_id,)).fetchone()
            else:
                existing = conn.execute("SELECT * FROM files WHERE original_path=?", (str(path),)).fetchone()
            now = now_iso()
            if existing is None:
                stable_id = candidate.stable_id or new_stable_id("material")
                display_name = candidate.display_name or path.name
                conn.execute(
                    """
                    INSERT INTO files(course_id, week_id, course_code, week_label, section, category, exercise_type,
                      filename, original_path, rel_path, source, source_label, hash, size, modified_at, indexed_at,
                      extension, mime_type, is_official, suspicious, text_cache_path, stable_id, course_name,
                      week_number, absolute_path, source_type, file_extension, file_size, sha256, is_solution,
                      is_question_source, active, missing_at, display_name, material_type, import_mode, inbox,
                      metadata_locked, source_missing, removed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        candidate.course_id,
                        candidate.week_id,
                        candidate.course_code,
                        candidate.week_label,
                        candidate.section,
                        candidate.category,
                        candidate.exercise_type,
                        path.name,
                        str(path),
                        candidate.rel_path,
                        candidate.source,
                        candidate.source_label,
                        identity.sha256,
                        identity.size,
                        identity.modified_at,
                        now,
                        identity.extension,
                        identity.mime_type,
                        candidate.is_official,
                        identity.suspicious,
                        stable_id,
                        candidate.course_name,
                        candidate.week_number,
                        str(path),
                        candidate.source_type,
                        identity.extension,
                        identity.size,
                        identity.sha256,
                        candidate.is_solution,
                        candidate.is_question_source,
                        display_name,
                        candidate.material_type,
                        candidate.import_mode,
                        candidate.inbox,
                        candidate.metadata_locked,
                    ),
                )
                file_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
                status = "added"
                force = True
            else:
                file_id = int(existing["id"])
                locked = int(bool(existing["metadata_locked"]))
                if candidate.remote_managed_identity:
                    conn.execute(
                        "UPDATE files SET original_path=?, absolute_path=? WHERE id=?",
                        (str(path), str(path), file_id),
                    )
                conn.execute(
                    """
                    UPDATE files SET course_id=CASE WHEN ?=1 THEN course_id ELSE ? END,
                      week_id=CASE WHEN ?=1 THEN week_id ELSE ? END,
                      course_code=CASE WHEN ?=1 THEN course_code ELSE ? END,
                      course_name=CASE WHEN ?=1 THEN course_name ELSE ? END,
                      week_label=CASE WHEN ?=1 THEN week_label ELSE ? END,
                      week_number=CASE WHEN ?=1 THEN week_number ELSE ? END,
                      section=CASE WHEN ?=1 THEN section ELSE ? END,
                      category=CASE WHEN ?=1 THEN category ELSE ? END,
                      exercise_type=CASE WHEN ?=1 THEN exercise_type ELSE ? END,
                      material_type=CASE WHEN ?=1 THEN material_type ELSE ? END,
                      rel_path=?, source=?, source_label=?, is_official=?, import_mode=?,
                      active=1, source_missing=0, missing_at=NULL, removed_at=NULL WHERE id=?
                    """,
                    (
                        locked,
                        candidate.course_id,
                        locked,
                        candidate.week_id,
                        locked,
                        candidate.course_code,
                        locked,
                        candidate.course_name,
                        locked,
                        candidate.week_label,
                        locked,
                        candidate.week_number,
                        locked,
                        candidate.section,
                        locked,
                        candidate.category,
                        locked,
                        candidate.exercise_type,
                        locked,
                        candidate.material_type,
                        candidate.rel_path,
                        candidate.source,
                        candidate.source_label,
                        candidate.is_official,
                        candidate.import_mode,
                        file_id,
                    ),
                )
                status = "existing"
                force = candidate.force_index
            conn.execute(
                "UPDATE files SET material_created_at=COALESCE(NULLIF(material_created_at, ''), indexed_at), material_updated_at=? WHERE id=?",
                (now, file_id),
            )
            indexed = index_material_content(conn, file_id, path, force=force, identity=identity)
            if not identity_matches_path(path, identity):
                raise IngestionSourceChanged("Source file changed during ingestion. Try again.")
            if candidate.remote_managed_identity:
                conn.execute(
                    "UPDATE files SET filename=?, display_name=?, original_path=?, absolute_path=? WHERE id=?",
                    (candidate.display_name or path.name, candidate.display_name or path.name, str(path), str(path), file_id),
                )
            current = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            upsert_domain_projection_for_file(conn, current, candidate.provenance)
            current = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            result = IngestionResult(
                status=status,
                legacy_file_id=file_id,
                material_stable_id=current["stable_id"],
                sha256=identity.sha256,
                version_changed=bool(indexed["updated"]),
                indexed=bool(indexed["indexed"]),
                suspicious=identity.suspicious,
                error=current["ai_index_error"] or "",
                questions=int(indexed["questions"]),
                solutions=int(indexed["solutions"]),
            )
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except IngestionSourceChanged as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            last_source_change = exc
            supplied_identity = None
            continue
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
    raise RuntimeError(str(last_source_change) if last_source_change else "Source file changed during ingestion. Try again.")


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
    source_kind = "folder"
    term = ensure_term(conn, "Imported Courses", stable_id="term_imported")

    for course_dir in sorted(path for path in study_root.iterdir() if path.is_dir()):
        if course_dir.name.startswith(".") or course_dir.name == "Needs Review":
            continue
        code, name = parse_course_dir(course_dir)
        now = now_iso()
        resolved_course = str(course_dir.resolve())
        course = conn.execute("SELECT * FROM courses WHERE source_folder=?", (resolved_course,)).fetchone()
        if course is not None and course["removed_at"]:
            continue
        if course is None:
            folder_key = course_dir.name
            if conn.execute("SELECT 1 FROM courses WHERE folder_name=?", (folder_key,)).fetchone():
                folder_key = f"{course_dir.name}__{hashlib.sha256(resolved_course.encode()).hexdigest()[:10]}"
            conn.execute(
                """
                INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
                  source_folder, source_kind, term_id, archived, active, metadata_locked, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?, ?)
                """,
                (new_stable_id("course"), code, code, name, name, folder_key, resolved_course,
                 resolved_course, source_kind, term["id"], now, now),
            )
            course_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        else:
            course_id = int(course["id"])
            conn.execute(
                """
                UPDATE courses SET code=CASE WHEN metadata_locked=1 THEN code ELSE ? END,
                  course_code=CASE WHEN metadata_locked=1 THEN course_code ELSE ? END,
                  name=CASE WHEN metadata_locked=1 THEN name ELSE ? END,
                  display_name=CASE WHEN metadata_locked=1 THEN display_name ELSE ? END,
                  path=?, source_folder=?, source_kind=?, term_id=CASE WHEN metadata_locked=1 THEN term_id ELSE ? END, active=1,
                  removed_at=NULL, updated_at=? WHERE id=?
                """,
                (code, code, name, name, resolved_course, resolved_course, source_kind, term["id"], now, course_id),
            )
        stats.courses += 1

        for child in sorted(path for path in course_dir.iterdir() if path.is_dir()):
            unit_label, unit_number, unit_kind = extract_learning_unit([child.name])
            if unit_label != "Unclassified":
                ensure_week_record(conn, course_id, unit_label, number=unit_number, path=str(child.resolve()), origin="scan", kind=unit_kind)

        for path in sorted(course_dir.rglob("*")):
            if not path.is_file() or path.name.startswith((".", "~$", ".~")):
                continue
            if path.name.lower() in INTERNAL_FILENAMES or "Needs Review" in path.parts or path.suffix.lower() not in ACADEMIC_EXTS:
                continue
            resolved_path = str(path.resolve())
            indexed_paths.add(resolved_path)
            rel = safe_relative(path, study_root)
            rel_parts = list(Path(rel).parts)
            week_label, week_number, week_kind = extract_learning_unit(rel_parts)
            week = ensure_week_record(conn, course_id, week_label, number=week_number, origin="scan", kind=week_kind)
            section, category, source_label = human_category(rel_parts)
            material_type = normalize_material_type(category or path.parent.name)
            category = material_type_label(material_type)
            source_row = src_index.get(rel, {})
            if source_row.get("Original or Saved Page"):
                source_label = "Official Canvas Material" if "official" in source_row.get("Original or Saved Page", "").lower() else source_label
            source = "official" if guess_is_official(rel, source_label) else "user"
            exercise_type = category if section == "02 Exercises" else ""
            existing = conn.execute("SELECT * FROM files WHERE original_path=?", (resolved_path,)).fetchone()
            if existing is None:
                stats.new_files += 1
                force = True
            else:
                force = False
            result = ingest_material_candidate(
                conn,
                IngestionCandidate(
                    path=path,
                    course_id=course_id,
                    week_id=int(week["id"]),
                    course_code=code,
                    course_name=name,
                    week_label=week_label,
                    week_number=week_number,
                    section=section,
                    category=category,
                    exercise_type=exercise_type,
                    material_type=material_type,
                    source=source,
                    source_label=source_label,
                    source_type=source_type_for(source_label),
                    rel_path=rel,
                    import_mode="scanned",
                    is_official=1 if source == "official" else 0,
                    is_solution=is_solution_file(path, category),
                    is_question_source=is_question_source_file(path, section, category),
                    display_name=path.name,
                    force_index=force,
                ),
            )
            stats.files += 1
            if result.version_changed and not force:
                stats.updated_files += 1
            elif not result.version_changed:
                stats.unchanged_files += 1
            if result.indexed:
                stats.indexed_text += 1
            stats.questions_detected += result.questions
            stats.solutions_detected += result.solutions
            if result.suspicious:
                stats.suspicious += 1
                stats.failed_files += 1

    for row in conn.execute("SELECT id, original_path, import_mode, removed_at FROM files").fetchall():
        if row["removed_at"]:
            continue
        if row["import_mode"] == "canvas_remote":
            continue
        original = Path(row["original_path"]).expanduser().resolve()
        if row["import_mode"] == "reference":
            missing = not original.exists()
            conn.execute(
                "UPDATE files SET active=1, source_missing=?, missing_at=CASE WHEN ? THEN COALESCE(missing_at, ?) ELSE NULL END WHERE id=?",
                (1 if missing else 0, missing, now_iso(), row["id"]),
            )
            continue
        if row["original_path"] not in indexed_paths:
            same_workspace = is_inside(original, study_root)
            conn.execute("DELETE FROM files_fts WHERE rowid=?", (row["id"],))
            conn.execute(
                "UPDATE files SET active=?, source_missing=?, missing_at=COALESCE(missing_at, ?) WHERE id=?",
                (1 if same_workspace else 0, 1 if same_workspace else 0, now_iso(), row["id"]),
            )
            conn.execute("UPDATE file_versions SET active=0 WHERE file_id=?", (row["id"],))
            conn.execute("UPDATE ai_index_state SET status='missing', error='Original file missing', last_synced_at=? WHERE file_id=?", (now_iso(), row["id"]))
            stats.removed_files += 1
    refresh_course_week_counts(conn)
    for week in conn.execute("SELECT id, path, has_materials, origin FROM weeks WHERE removed_at IS NULL").fetchall():
        if week["origin"] == "scan" and not week["has_materials"] and week["path"] and not Path(week["path"]).exists():
            conn.execute("UPDATE weeks SET removed_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), week["id"]))
    conn.execute("INSERT INTO sync_events(event_type, detail, created_at) VALUES (?, ?, ?)", ("scan", json.dumps(stats.__dict__), now_iso()))
    reconcile_domain_projection(conn)
    conn.commit()
    conn.close()
    log_event("scan", json.dumps({"files": stats.files, "root": "configured-study-library"}, ensure_ascii=False))
    return stats


def course_row_with_counts(conn: sqlite3.Connection, course_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"""
        SELECT c.*, t.name AS term_name, COUNT(DISTINCT f.id) AS file_count,
          MAX(CASE WHEN w.has_materials=1 THEN w.week_number ELSE NULL END) AS latest_week
        FROM courses c LEFT JOIN terms t ON t.id=c.term_id
        LEFT JOIN files f ON f.course_id=c.id AND f.active=1 AND f.removed_at IS NULL
        LEFT JOIN weeks w ON w.course_id=c.id AND w.removed_at IS NULL
        WHERE c.id=? GROUP BY c.id
        """,
        (course_id,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError("Course was not found")
    return row


def manage_term(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "create")
    now = now_iso()
    if action == "create":
        term = ensure_term(conn, str(body.get("name") or ""))
    else:
        term_id = int(body.get("id") or 0)
        term = conn.execute("SELECT * FROM terms WHERE id=?", (term_id,)).fetchone()
        if term is None:
            raise FileNotFoundError("Term was not found")
        if term["stable_id"] == "term_imported" and action == "archive":
            raise PermissionError("The built-in term cannot be archived")
        if action == "rename":
            name = clean_metadata_text(body.get("name"), "Term name")
            conn.execute("UPDATE terms SET name=?, updated_at=? WHERE id=?", (name, now, term_id))
        elif action in {"archive", "restore"}:
            conn.execute("UPDATE terms SET archived=?, updated_at=? WHERE id=?", (1 if action == "archive" else 0, now, term_id))
        else:
            raise ValueError("Unknown term action")
        term = conn.execute("SELECT * FROM terms WHERE id=?", (term_id,)).fetchone()
    conn.commit()
    return dict(term)


def manage_course(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "create")
    now = now_iso()
    if action == "create":
        display_name = clean_metadata_text(body.get("display_name") or body.get("name"), "Course name")
        course_code = clean_metadata_text(body.get("course_code") or body.get("code"), "Course code", required=False, limit=40)
        term_id = int(body.get("term_id") or 0)
        if term_id:
            term = conn.execute("SELECT * FROM terms WHERE id=? AND archived=0", (term_id,)).fetchone()
            if term is None:
                raise ValueError("Choose an active term")
        else:
            default_term_name = "Imported Courses"
            term = ensure_term(conn, str(body.get("term_name") or default_term_name), stable_id="term_imported" if not body.get("term_name") else None)
            term_id = int(term["id"])
        stable_id = new_stable_id("course")
        source_kind = "managed"
        conn.execute(
            """
            INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
              source_folder, source_kind, term_id, archived, active, removed_at, sort_order,
              metadata_locked, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, 0, 1, NULL, 0, 1, ?, ?)
            """,
            (stable_id, course_code, course_code, display_name, display_name, f"managed-{stable_id}", source_kind, term_id, now, now),
        )
        course_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    else:
        course_id = int(body.get("id") or 0)
        course = conn.execute("SELECT * FROM courses WHERE id=? AND source_kind!='system'", (course_id,)).fetchone()
        if course is None:
            raise FileNotFoundError("Course was not found")
        if action == "update":
            display_name = clean_metadata_text(body.get("display_name") or body.get("name") or course["display_name"], "Course name")
            course_code = clean_metadata_text(body.get("course_code") if "course_code" in body else course["course_code"], "Course code", required=False, limit=40)
            term_id = int(body.get("term_id") or course["term_id"] or 0)
            if not conn.execute("SELECT 1 FROM terms WHERE id=?", (term_id,)).fetchone():
                raise ValueError("Term was not found")
            conn.execute(
                "UPDATE courses SET code=?, course_code=?, name=?, display_name=?, term_id=?, metadata_locked=1, updated_at=? WHERE id=?",
                (course_code, course_code, display_name, display_name, term_id, now, course_id),
            )
            conn.execute("UPDATE files SET course_code=?, course_name=? WHERE course_id=?", (course_code, display_name, course_id))
            conn.execute("UPDATE document_chunks SET course_code=? WHERE file_id IN (SELECT id FROM files WHERE course_id=?)", (course_code, course_id))
            conn.execute("UPDATE questions SET course_code=? WHERE file_id IN (SELECT id FROM files WHERE course_id=?)", (course_code, course_id))
            conn.execute("UPDATE solutions SET course_code=? WHERE file_id IN (SELECT id FROM files WHERE course_id=?)", (course_code, course_id))
            conn.execute("UPDATE ai_conversations SET course_code=? WHERE course_code=?", (course_code, course["course_code"]))
            conn.execute("UPDATE files_fts SET course_code=? WHERE rowid IN (SELECT id FROM files WHERE course_id=?)", (course_code, course_id))
        elif action in {"archive", "restore"}:
            conn.execute("UPDATE courses SET archived=?, updated_at=? WHERE id=?", (1 if action == "archive" else 0, now, course_id))
        elif action == "remove":
            conn.execute("UPDATE courses SET active=0, removed_at=?, updated_at=? WHERE id=?", (now, now, course_id))
            conn.execute("UPDATE files SET active=0, removed_at=COALESCE(removed_at, ?) WHERE course_id=?", (now, course_id))
            conn.execute("DELETE FROM files_fts WHERE rowid IN (SELECT id FROM files WHERE course_id=?)", (course_id,))
        elif action == "reorder":
            conn.execute("UPDATE courses SET sort_order=?, updated_at=? WHERE id=?", (int(body.get("sort_order") or 0), now, course_id))
        else:
            raise ValueError("Unknown course action")
    refresh_course_week_counts(conn)
    reconcile_domain_projection(conn)
    conn.commit()
    return public_course(course_row_with_counts(conn, course_id))


def manage_week(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "create")
    if action == "create":
        course_id = int(body.get("course_id") or 0)
        if not conn.execute("SELECT 1 FROM courses WHERE id=? AND removed_at IS NULL", (course_id,)).fetchone():
            raise FileNotFoundError("Course was not found")
        row = ensure_week_record(conn, course_id, str(body.get("label") or body.get("week_label") or ""), number=body.get("number"), origin="managed", kind=str(body.get("kind") or "") or None)
    else:
        week_id = int(body.get("id") or 0)
        row = conn.execute("SELECT * FROM weeks WHERE id=? AND removed_at IS NULL", (week_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("Week or module was not found")
        if action == "rename":
            label = clean_metadata_text(body.get("label") or body.get("week_label"), "Week or module name", limit=80)
            if conn.execute("SELECT 1 FROM weeks WHERE course_id=? AND week_label=? AND id!=? AND removed_at IS NULL", (row["course_id"], label, week_id)).fetchone():
                raise ValueError("That week or module already exists")
            number = body.get("number")
            if number is None:
                match = re.search(r"\b(\d{1,3})\b", label)
                number = int(match.group(1)) if match else None
            conn.execute("UPDATE weeks SET week_label=?, week_number=?, kind=?, origin='managed', updated_at=? WHERE id=?", (label, number, body.get("kind") or row["kind"], now_iso(), week_id))
            conn.execute("UPDATE files SET week_label=?, week_number=?, metadata_locked=1 WHERE week_id=?", (label, number, week_id))
            for table in ("document_chunks", "questions", "solutions"):
                conn.execute(f"UPDATE {table} SET week_label=? WHERE file_id IN (SELECT id FROM files WHERE week_id=?)", (label, week_id))
            conn.execute("UPDATE notes SET week_label=? WHERE course_id=? AND week_label=?", (label, row["course_id"], row["week_label"]))
            conn.execute("UPDATE wrong_questions SET week_label=? WHERE course_id=? AND week_label=?", (label, row["course_id"], row["week_label"]))
            conn.execute("UPDATE files_fts SET week_label=? WHERE rowid IN (SELECT id FROM files WHERE week_id=?)", (label, week_id))
        elif action == "remove":
            count = conn.execute("SELECT COUNT(*) AS c FROM files WHERE week_id=? AND active=1 AND removed_at IS NULL", (week_id,)).fetchone()["c"]
            if count:
                raise ValueError("Only an empty week or module can be removed")
            conn.execute("UPDATE weeks SET removed_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), week_id))
        else:
            raise ValueError("Unknown week action")
        row = conn.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    refresh_course_week_counts(conn)
    reconcile_domain_projection(conn)
    conn.commit()
    return public_week(row)


def classification_suggestion(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    haystack = " ".join(path.parts[-5:])
    course = None
    for row in conn.execute("SELECT * FROM courses WHERE active=1 AND archived=0 AND removed_at IS NULL AND source_kind!='system'").fetchall():
        code = str(row["course_code"] or "")
        if code and re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", haystack, re.IGNORECASE):
            course = row
            break
    week_label, week_number, week_kind = extract_learning_unit(list(path.parts[-5:]))
    lowered = haystack.lower()
    material_type = "other"
    for candidate in MATERIAL_TYPES[:-1]:
        if candidate.lower() in lowered:
            material_type = candidate
            break
    return {
        "course_id": int(course["id"]) if course else None,
        "course_code": course["course_code"] if course else "",
        "week_label": "" if week_label == "Unclassified" else week_label,
        "week_number": week_number,
        "week_kind": week_kind,
        "material_type": material_type,
    }


def validate_reference_path(raw: Any) -> Path:
    if RUNTIME_PROFILE == "demo-test":
        raise PermissionError("Demo/Test mode cannot reference external study files.")
    text = str(raw or "")
    if not text or "\x00" in text:
        raise ValueError("Choose a normal local file")
    path = Path(text).expanduser()
    if not path.is_absolute() or not path.exists() or not path.is_file():
        raise FileNotFoundError("Selected file was not found")
    if path.suffix.lower() not in ACADEMIC_EXTS:
        raise ValueError("That file type is not supported")
    return path.resolve()


def register_material_paths(conn: sqlite3.Connection, body: dict[str, Any], *, defer_domain_reconcile: bool = False) -> dict[str, Any]:
    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 200:
        raise ValueError("Choose between 1 and 200 files")
    course_id = int(body.get("course_id") or 0)
    week_id = int(body.get("week_id") or 0)
    week_label = str(body.get("week_label") or "").strip()
    duplicate_policy = str(body.get("duplicate_policy") or "skip")
    is_official = bool(body.get("is_official", False))
    if course_id:
        course = conn.execute("SELECT * FROM courses WHERE id=? AND active=1 AND removed_at IS NULL", (course_id,)).fetchone()
        if course is None:
            raise FileNotFoundError("Course was not found")
        inbox = 0
    else:
        course = ensure_inbox_course(conn)
        course_id = int(course["id"])
        inbox = 1
    if week_id:
        week = conn.execute("SELECT * FROM weeks WHERE id=? AND course_id=? AND removed_at IS NULL", (week_id, course_id)).fetchone()
        if week is None:
            raise ValueError("Week or module does not belong to this course")
    else:
        week = ensure_week_record(conn, course_id, week_label or "Unclassified", origin="managed", kind="module" if not week_label else None)
    material_type = normalize_material_type(body.get("material_type") or "other")
    category_label = material_type_label(material_type)
    section = section_for_material_type(material_type)
    exercise_type = category_label if section == "02 Exercises" else ""
    results = []
    for raw_path in raw_paths:
        path = validate_reference_path(raw_path)
        existing_path = conn.execute("SELECT * FROM files WHERE original_path=?", (str(path),)).fetchone()
        if existing_path:
            if existing_path["removed_at"]:
                conn.execute("UPDATE files SET active=1, removed_at=NULL, source_missing=0, missing_at=NULL WHERE id=?", (existing_path["id"],))
            results.append({"status": "existing", "id": existing_path["id"], "filename": existing_path["filename"]})
            continue
        identity = identify_ingestion_path(path)
        duplicate = conn.execute("SELECT id, filename, course_code, week_label, material_type FROM files WHERE sha256=? AND active=1 AND removed_at IS NULL ORDER BY id LIMIT 1", (identity.sha256,)).fetchone()
        if duplicate and duplicate_policy != "add_anyway":
            results.append({"status": "duplicate", "id": duplicate["id"], "filename": path.name, "existing": public_file(duplicate)})
            continue
        stable_id = new_stable_id("material")
        rel_path = f"references/{stable_id}/{path.name}"
        result = ingest_material_candidate(
            conn,
            IngestionCandidate(
                path=path,
                course_id=course_id,
                week_id=int(week["id"]),
                course_code=course["course_code"],
                course_name=course["display_name"],
                week_label=week["week_label"],
                week_number=week["week_number"],
                section=section,
                category=category_label,
                exercise_type=exercise_type,
                material_type=material_type,
                source="official" if is_official else "user",
                source_label="Teacher-provided material" if is_official else "User-selected local file",
                source_type="Local Reference",
                rel_path=rel_path,
                import_mode="reference",
                is_official=1 if is_official else 0,
                is_solution=0,
                is_question_source=0,
                inbox=inbox,
                metadata_locked=1,
                display_name=path.name,
                stable_id=stable_id,
                force_index=True,
                identity=identity,
            ),
        )
        results.append({"status": "added", "id": result.legacy_file_id, "filename": path.name, "indexed": result.indexed})
    refresh_course_week_counts(conn)
    if not defer_domain_reconcile:
        reconcile_domain_projection(conn)
    conn.commit()
    return {"ok": True, "items": results, "added": sum(item["status"] == "added" for item in results)}


def import_course_folder(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    if RUNTIME_PROFILE == "demo-test":
        raise PermissionError("Demo/Test mode cannot import an external course folder.")
    raw = str(body.get("path") or "")
    if not raw or "\x00" in raw:
        raise ValueError("Choose a normal local course folder")
    folder = Path(raw).expanduser()
    if not folder.is_absolute() or not folder.exists() or not folder.is_dir():
        raise FileNotFoundError("Selected course folder was not found")
    folder = folder.resolve()
    candidates = sorted(
        path for path in folder.rglob("*")
        if path.is_file()
        and not path.name.startswith((".", "~$", ".~"))
        and path.suffix.lower() in ACADEMIC_EXTS
    )
    if len(candidates) > 500:
        raise ValueError("Choose a course folder with no more than 500 supported files")

    existing = conn.execute(
        "SELECT * FROM courses WHERE source_folder=? AND removed_at IS NULL",
        (str(folder),),
    ).fetchone()
    if existing:
        course_id = int(existing["id"])
        conn.execute("UPDATE courses SET active=1, archived=0, updated_at=? WHERE id=?", (now_iso(), course_id))
    else:
        code, inferred_name = parse_course_dir(folder)
        display_name = clean_metadata_text(body.get("display_name") or inferred_name or folder.name, "Course name")
        course_code = clean_metadata_text(body.get("course_code") or code, "Course code", required=False, limit=40)
        term_id = int(body.get("term_id") or 0)
        if term_id and not conn.execute("SELECT 1 FROM terms WHERE id=? AND archived=0", (term_id,)).fetchone():
            raise ValueError("Choose an active term")
        if not term_id:
            term_id = int(ensure_term(conn, "Imported Courses", stable_id="term_imported")["id"])
        stable_id = new_stable_id("course")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO courses(stable_id, code, course_code, name, display_name, folder_name, path,
              source_folder, source_kind, term_id, archived, active, removed_at, sort_order,
              metadata_locked, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'managed', ?, 0, 1, NULL, 0, 1, ?, ?)
            """,
            (stable_id, course_code, course_code, display_name, display_name, f"folder-{stable_id}",
             str(folder), str(folder), term_id, now, now),
        )
        course_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    results: list[dict[str, Any]] = []
    for path in candidates:
        relative_parts = list(path.relative_to(folder).parts)
        week_label, week_number, week_kind = extract_learning_unit(relative_parts)
        week = ensure_week_record(
            conn,
            course_id,
            week_label,
            number=week_number,
            origin="folder-import",
            kind=week_kind,
        )
        _section, detected_category, _source = human_category(relative_parts)
        material_type = normalize_material_type(detected_category or classification_suggestion(conn, path)["material_type"])
        item = register_material_paths(
            conn,
            {
                "paths": [str(path)],
                "course_id": course_id,
                "week_id": int(week["id"]),
                "material_type": material_type,
                "duplicate_policy": body.get("duplicate_policy") or "skip",
                "is_official": bool(body.get("is_official", False)),
            },
            defer_domain_reconcile=True,
        )["items"][0]
        results.append(item)
    refresh_course_week_counts(conn)
    reconcile_domain_projection(conn)
    conn.commit()
    course = public_course(course_row_with_counts(conn, course_id))
    return {
        "ok": True,
        "course": course,
        "items": results,
        "detected": len(candidates),
        "added": sum(item["status"] == "added" for item in results),
        "duplicates": sum(item["status"] in {"duplicate", "existing"} for item in results),
    }


def manage_materials(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "")
    ids = body.get("ids") or ([body.get("id")] if body.get("id") else [])
    if not isinstance(ids, list) or not ids or len(ids) > 200:
        raise ValueError("Choose one or more materials")
    material_ids = [int(value) for value in ids]
    rows = conn.execute(f"SELECT * FROM files WHERE id IN ({','.join('?' for _ in material_ids)}) AND removed_at IS NULL", material_ids).fetchall()
    if len(rows) != len(set(material_ids)):
        raise FileNotFoundError("One or more materials were not found")
    if action in {"move", "classify"}:
        course_id = int(body.get("course_id") or rows[0]["course_id"])
        course = conn.execute("SELECT * FROM courses WHERE id=? AND active=1 AND removed_at IS NULL", (course_id,)).fetchone()
        if course is None:
            raise FileNotFoundError("Course was not found")
        week_id = int(body.get("week_id") or 0)
        if week_id:
            week = conn.execute("SELECT * FROM weeks WHERE id=? AND course_id=? AND removed_at IS NULL", (week_id, course_id)).fetchone()
            if week is None:
                raise ValueError("Week or module does not belong to this course")
        else:
            week = ensure_week_record(conn, course_id, str(body.get("week_label") or rows[0]["week_label"] or "Unclassified"), origin="managed")
        material_type = normalize_material_type(body.get("material_type") or rows[0]["material_type"] or rows[0]["category"])
        category_label = material_type_label(material_type)
        section = section_for_material_type(material_type)
        exercise_type = category_label if section == "02 Exercises" else ""
        placeholders = ",".join("?" for _ in material_ids)
        params = [course_id, week["id"], course["course_code"], course["display_name"], week["week_label"], week["week_number"], section, category_label, exercise_type, material_type, *material_ids]
        conn.execute(
            f"UPDATE files SET course_id=?, week_id=?, course_code=?, course_name=?, week_label=?, week_number=?, section=?, category=?, exercise_type=?, material_type=?, inbox=0, metadata_locked=1 WHERE id IN ({placeholders})",
            params,
        )
        for table in ("document_chunks", "questions", "solutions"):
            conn.execute(
                f"UPDATE {table} SET course_code=?, week_label=?, exercise_type=? WHERE file_id IN ({placeholders})",
                [course["course_code"], week["week_label"], exercise_type, *material_ids],
            )
        conn.execute(
            f"UPDATE files_fts SET course_code=?, week_label=?, category=? WHERE rowid IN ({placeholders})",
            [course["course_code"], week["week_label"], category_label, *material_ids],
        )
    elif action == "rename":
        if len(material_ids) != 1:
            raise ValueError("Rename one material at a time")
        display_name = clean_metadata_text(body.get("display_name"), "Material name", limit=180)
        conn.execute("UPDATE files SET display_name=?, metadata_locked=1 WHERE id=?", (display_name, material_ids[0]))
    elif action == "remove":
        placeholders = ",".join("?" for _ in material_ids)
        conn.execute(f"UPDATE files SET active=0, removed_at=? WHERE id IN ({placeholders})", [now_iso(), *material_ids])
        conn.execute(f"DELETE FROM files_fts WHERE rowid IN ({placeholders})", material_ids)
    elif action in {"star", "unstar"}:
        if action == "star":
            conn.executemany(
                "INSERT OR IGNORE INTO stars(target_type, target_id, created_at) VALUES ('file', ?, ?)",
                [(material_id, now_iso()) for material_id in material_ids],
            )
        else:
            placeholders = ",".join("?" for _ in material_ids)
            conn.execute(f"DELETE FROM stars WHERE target_type='file' AND target_id IN ({placeholders})", material_ids)
    elif action == "relink":
        if len(material_ids) != 1:
            raise ValueError("Relink one material at a time")
        path = validate_reference_path(body.get("path"))
        conflict = conn.execute("SELECT id FROM files WHERE original_path=? AND id!=?", (str(path), material_ids[0])).fetchone()
        if conflict:
            raise ValueError("That file is already registered in StudyHub")
        conn.execute("UPDATE files SET original_path=?, absolute_path=?, import_mode='reference', source_missing=0, missing_at=NULL, active=1 WHERE id=?", (str(path), str(path), material_ids[0]))
        index_material_content(conn, material_ids[0], path, force=True)
    else:
        raise ValueError("Unknown material action")
    placeholders = ",".join("?" for _ in material_ids)
    conn.execute(f"UPDATE files SET material_updated_at=? WHERE id IN ({placeholders})", [now_iso(), *material_ids])
    refresh_course_week_counts(conn)
    reconcile_domain_projection(conn)
    conn.commit()
    return {"ok": True, "ids": material_ids}


def clear_recreatable_cache(conn: sqlite3.Connection) -> dict[str, Any]:
    visibility = course_visibility_sql("c", include_system=True)
    visible_rows = conn.execute(
        f"""
        SELECT f.*
        FROM files f
        JOIN courses c ON c.id=f.course_id
        WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
        """
    ).fetchall()
    visible_ids = [int(row["id"]) for row in visible_rows]
    if visible_ids:
        placeholders = ",".join("?" for _ in visible_ids)
        for row in visible_rows:
            try:
                cache_path = safe_cache_path(row["text_cache_path"])
            except PermissionError:
                cache_path = None
            if cache_path:
                shared = conn.execute(
                    f"SELECT COUNT(*) AS c FROM files WHERE text_cache_path=? AND id NOT IN ({placeholders})",
                    [str(cache_path), *visible_ids],
                ).fetchone()["c"]
                if not shared:
                    cache_path.unlink(missing_ok=True)
            for derivative in preview_cache_dir().glob(f"file-{int(row['id'])}-*"):
                derivative.unlink(missing_ok=True)
        conn.execute(f"DELETE FROM files_fts WHERE rowid IN ({placeholders})", visible_ids)
        for table in ("document_chunks", "questions", "solutions"):
            conn.execute(f"DELETE FROM {table} WHERE file_id IN ({placeholders})", visible_ids)
        conn.execute(
            f"DELETE FROM ai_index_state WHERE provider='local' AND file_id IN ({placeholders})",
            visible_ids,
        )
        conn.execute(
            f"UPDATE files SET text_cache_path='', ai_index_status='not_indexed', ai_index_error='' WHERE id IN ({placeholders})",
            visible_ids,
        )
    ensure_dirs()
    reindexed = 0
    for row in visible_rows:
        path = Path(row["original_path"])
        if material_path_allowed(row, path) and path.exists():
            index_material_content(conn, row["id"], path, force=True)
            reindexed += 1
    reconcile_domain_projection(conn)
    conn.commit()
    return {"ok": True, "reindexed": reindexed}


def reset_studyhub_state(conn: sqlite3.Connection) -> dict[str, Any]:
    for table in (
        "ai_messages", "ai_conversations", "ai_interactions", "wrong_questions", "attempts", "bookmarks",
        "study_sessions", "notes", "stars", "questions", "solutions", "document_chunks", "file_versions",
        "academic_change_event_state", "academic_change_events",
        "material_versions", "materials", "sources", "blobs",
        "ai_index_state", "sync_events", "files", "weeks", "courses", "terms", "app_settings",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM files_fts")
    conn.commit()
    for child in CACHE_DIR.iterdir() if CACHE_DIR.exists() else []:
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    ensure_dirs()
    init_db(conn)
    return {"ok": True, "originalFilesChanged": False}


def configure_managed_workspace() -> dict[str, Any]:
    managed_root = DATA_DIR / "managed-library"
    managed_root.mkdir(parents=True, exist_ok=True)
    entries = read_local_env_entries()
    entries.pop("DEMO_MODE", None)
    entries["STUDY_LIBRARY_PATH"] = str(managed_root)
    entries.setdefault("HOST", "127.0.0.1")
    write_local_env_entries(entries)
    return {"ok": True, "restartRequired": True, "message": "Your private StudyHub workspace is ready."}


def course_visibility_sql(
    alias: str = "c",
    *,
    include_system: bool = False,
    include_archived_term: bool = False,
) -> str:
    mode = f"{alias}.source_kind!='demo'"
    system = "" if include_system else f" AND {alias}.source_kind!='system'"
    term = "" if include_archived_term else f" AND EXISTS (SELECT 1 FROM terms visible_term WHERE visible_term.id={alias}.term_id AND visible_term.archived=0)"
    return f"{mode}{system} AND {alias}.removed_at IS NULL{term}"


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def fts_query(raw: str) -> str:
    tokens = re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", raw, flags=re.UNICODE)
    return " ".join(tokens[:12])


def meaningful_search_words(query: str) -> list[str]:
    words = []
    for token in query.split():
        word = token.lower()
        if word in LOCAL_SEARCH_STOPWORDS:
            continue
        if len(word) < 3 and not re.search(r"\d", word):
            continue
        words.append(word)
    return words


def has_context_scope(context: dict[str, Any]) -> bool:
    return bool(context.get("fileId") or context.get("week") or context.get("course"))


def get_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"""
        SELECT f.*, s.id AS star_id
        FROM files f
        JOIN courses c ON c.id=f.course_id
        LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id
        WHERE f.id=? AND f.active=1 AND f.removed_at IS NULL AND f.source_missing=0
          AND c.archived=0 AND {course_visibility_sql('c', include_system=True)}
        """,
        (file_id,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError("File was not found. Scan Library to refresh your file list.")
    path = Path(row["original_path"]).expanduser().resolve()
    if not material_path_allowed(row, path):
        raise PermissionError("file outside StudyHub material allowlist")
    if not path.exists():
        raise FileNotFoundError("Original file is missing. Scan Library to refresh your file list.")
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
    visibility = course_visibility_sql("c", include_system=True)
    file_count = conn.execute(
        f"SELECT COUNT(*) AS c FROM files f JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {visibility}"
    ).fetchone()["c"]
    suspicious = conn.execute(
        f"SELECT COUNT(*) AS c FROM files f JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND f.removed_at IS NULL AND f.suspicious != '' AND c.archived=0 AND {visibility}"
    ).fetchone()["c"]
    ai_indexed = conn.execute(
        f"SELECT COUNT(*) AS c FROM ai_index_state ai JOIN files f ON f.id=ai.file_id JOIN courses c ON c.id=f.course_id WHERE ai.provider='local' AND ai.status='indexed' AND f.active=1 AND c.archived=0 AND {visibility}"
    ).fetchone()["c"]
    questions = conn.execute(
        f"SELECT COUNT(*) AS c FROM questions q JOIN files f ON f.id=q.file_id JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND c.archived=0 AND {visibility}"
    ).fetchone()["c"]
    chunks = conn.execute(
        f"SELECT COUNT(*) AS c FROM document_chunks dc JOIN files f ON f.id=dc.file_id JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND c.archived=0 AND {visibility}"
    ).fetchone()["c"]
    last_scan = conn.execute("SELECT created_at FROM sync_events WHERE event_type='scan' ORDER BY id DESC LIMIT 1").fetchone()
    last_ai = conn.execute("SELECT MAX(last_synced_at) AS ts FROM ai_index_state").fetchone()
    vector_store_id = current_vector_store_id(conn)
    pdf_dependency_error = pdf_extraction_dependency_error()
    office_dependency_error = office_preview_dependency_error()
    ca_bundle_available = certifi is not None and Path(certifi.where()).is_file()
    extraction_warnings = [pdf_dependency_error] if pdf_dependency_error else []
    return {
        "app": APP_NAME,
        "version": "0.3.0-beta.1",
        "runtimeProfile": RUNTIME_PROFILE,
        "desktopMode": DESKTOP_MODE,
        "packagedBackend": PACKAGED_BACKEND,
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
        "verifiedHttps": "Available" if ca_bundle_available else "Unavailable",
        "canvas": "Handled separately by an authenticated user-approved importer workflow",
        "pdfTextExtraction": "Unavailable" if pdf_dependency_error else "Available",
        "officeVisualPreview": "Unavailable" if office_dependency_error else "Available",
        "officePreviewWarning": office_dependency_error,
        "extractionWarnings": extraction_warnings,
    }


def preflight_item(code: str, severity: str, title: str, what: str, impact: str, next_step: str, details: str = "") -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "whatHappened": what,
        "impact": impact,
        "nextStep": next_step,
        "details": details,
    }


def api_preflight(conn: sqlite3.Connection) -> dict[str, Any]:
    health = api_health(conn)
    items: list[dict[str, str]] = []
    file_count = int(health["filesIndexed"] or 0)
    pdf_count = conn.execute(
        f"SELECT COUNT(*) AS c FROM files f JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND f.removed_at IS NULL AND lower(f.extension)='.pdf' AND c.archived=0 AND {course_visibility_sql('c', include_system=True)}"
    ).fetchone()["c"]
    root_exists = bool(health["studyLibraryConnected"])
    openai_configured = bool(os.environ.get("OPENAI_API_KEY"))
    database_parent = DB_PATH.expanduser().resolve().parent
    database_writable = database_parent.exists() and os.access(database_parent, os.W_OK)

    if not ENV_LOCAL_EXISTS:
        items.append(
            preflight_item(
                "first_launch",
                "info",
                "First launch is ready",
                "StudyHub created an empty local workspace for your metadata.",
                "Nothing private has been scanned.",
                "Create a course or import an existing course folder to begin.",
            )
        )
    if not root_exists:
        items.append(
            preflight_item(
                "study_library_missing",
                "warning",
                "Study folder not found",
                "The configured study folder could not be found.",
                "Courses and files cannot appear until StudyHub can see a local folder.",
                "Choose an existing local study folder in Settings, then restart StudyHub.",
            )
        )
    elif file_count == 0:
        items.append(
            preflight_item(
                "study_library_empty",
                "warning",
                "No study files found yet",
                "StudyHub can see your study folder, but no supported course files are ready.",
                "Home, Courses, Search, Practice, and AI will stay mostly empty.",
                "Add PDF, Word, PowerPoint, text, CSV, notebook, Python, or R files, then scan again.",
            )
        )
    if not database_writable:
        items.append(
            preflight_item(
                "database_not_writable",
                "error",
                "Local save location is not writable",
                "StudyHub cannot write its local app data.",
                "Notes, stars, scan results, and AI history may not save.",
                "Move the app to a writable folder or fix folder permissions, then restart.",
            )
        )
    if health["pdfTextExtraction"] == "Unavailable" and pdf_count:
        items.append(
            preflight_item(
                "pdf_text_missing",
                "warning",
                "PDF text support is missing",
                "StudyHub can list PDF files, but cannot read searchable text from many PDFs until Poppler is installed.",
                "PDF preview still works. Ask AI may not understand PDFs whose text has not been read.",
                "Install Poppler, then scan again.",
                "Expected command-line tools: pdftotext and pdfinfo.",
            )
        )
    if not (KATEX_DIST_DIR / "katex.min.js").exists():
        items.append(
            preflight_item(
                "math_renderer_missing",
                "warning",
                "Math rendering files are missing",
                "The local JavaScript math renderer dependency was not found.",
                "AI explanations can still load, but formulas may appear as raw LaTeX text.",
                "Run npm install, then restart StudyHub.",
            )
        )
    if not openai_configured:
        items.append(
            preflight_item(
                "openai_optional",
                "info",
                "AI is optional",
                "No OpenAI API key is configured.",
                "StudyHub can still browse, search, preview, star, and show local source excerpts.",
                "Add an OpenAI API key later if you want full AI explanations.",
            )
        )
    elif certifi is None:
        items.append(
            preflight_item(
                "tls_ca_bundle_missing",
                "warning",
                "OpenAI may fail certificate checks",
                "The Python CA bundle package was not found.",
                "Ask AI may fall back to local excerpts if HTTPS certificate verification fails.",
                "Install certifi for this Python environment, then restart StudyHub.",
            )
        )

    return {
        "readyToStudy": root_exists and file_count > 0 and database_writable,
        "firstLaunch": not ENV_LOCAL_EXISTS,
        "fileCount": file_count,
        "courseCount": conn.execute(
            f"SELECT COUNT(*) AS c FROM courses c WHERE c.active=1 AND c.archived=0 AND {course_visibility_sql('c')}"
        ).fetchone()["c"],
        "items": items,
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
    JOIN courses c ON c.id=f.course_id
    WHERE f.active=1 AND f.removed_at IS NULL AND f.is_official=1
      AND c.archived=0 AND {visibility}
    """
    sql = sql.format(visibility=course_visibility_sql("c"))
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
        words = meaningful_search_words(query)
        if not words:
            if not has_context_scope(context):
                return []
            sql += " ORDER BY dc.id LIMIT ?"
            params.append(limit)
            return rows_to_dicts(conn.execute(sql, params).fetchall())
        rows = conn.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            text = f"{row['filename']} {row['category']} {row['exercise_type']} {row['text']}".lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored and context.get("fileId"):
            sql += " ORDER BY dc.chunk_index LIMIT ?"
            params.append(limit)
            return rows_to_dicts(conn.execute(sql, params).fetchall())
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


def has_specific_teacher_question_scope(context: dict[str, Any], prompt: str) -> bool:
    if context.get("questionId") or context.get("questionNumber") or detect_question_number(prompt):
        return True
    if context.get("fileId"):
        return True
    if context.get("course") and context.get("week") and context.get("exerciseType") and QUESTION_INTENT_RE.search(prompt):
        return True
    return False


def teacher_questions_for_context(conn: sqlite3.Connection, context: dict[str, Any], prompt: str, limit: int = 5) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = """
    SELECT q.*, f.filename, f.rel_path, f.text_cache_path, f.stable_id, f.sha256
    FROM questions q JOIN files f ON f.id=q.file_id JOIN courses c ON c.id=f.course_id
    WHERE f.active=1 AND f.removed_at IS NULL AND q.official_source=1
      AND c.archived=0 AND {visibility}
    """
    sql = sql.format(visibility=course_visibility_sql("c"))
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
    FROM solutions s JOIN files f ON f.id=s.file_id JOIN courses c ON c.id=f.course_id
    WHERE f.active=1 AND f.removed_at IS NULL AND s.official_source=1
      AND c.archived=0 AND {visibility}
    """
    sql = sql.format(visibility=course_visibility_sql("c"))
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
    return [public_solution(row) for row in conn.execute(sql, params).fetchall()]


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
        f"""
        SELECT f.* FROM files f JOIN courses c ON c.id=f.course_id
        WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0
          AND {course_visibility_sql('c', include_system=True)}
          AND (
            f.stable_id LIKE ? OR REPLACE(f.stable_id, '-', '') LIKE ?
            OR f.sha256 LIKE ? OR f.hash LIKE ?
          )
        ORDER BY f.id LIMIT 1
        """,
        (f"{prefix}%", f"{prefix}%", f"{prefix}%", f"{prefix}%"),
    ).fetchone()
    if row is None:
        raise FileNotFoundError("NOT FOUND")
    path = Path(row["original_path"]).expanduser().resolve()
    if not material_path_allowed(row, path):
        raise PermissionError("DENY")
    if not path.exists():
        raise FileNotFoundError("NOT FOUND")
    return row


def mcp_file_filters(args: dict[str, Any]) -> tuple[str, list[Any]]:
    params: list[Any] = []
    sql = f" FROM files f JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {course_visibility_sql('c')}"
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
        f"""
        SELECT c.code, c.name, c.folder_name, COUNT(f.id) AS file_count,
          MAX(w.week_number) AS latest_week
        FROM courses c
        LEFT JOIN files f ON f.course_id=c.id AND f.active=1
        LEFT JOIN weeks w ON w.course_id=c.id AND w.has_materials=1
        WHERE c.archived=0 AND {course_visibility_sql('c')}
        GROUP BY c.id
        HAVING file_count > 0
        ORDER BY c.code
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
        f"""
        SELECT w.week_label, w.week_number, w.has_materials, w.file_count
        FROM weeks w JOIN courses c ON c.id=w.course_id
        WHERE c.code LIKE ? AND c.archived=0 AND w.removed_at IS NULL AND {course_visibility_sql('c')}
        ORDER BY CASE WHEN w.week_number IS NULL THEN 1 ELSE 0 END, w.week_number, w.week_label
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
        "note": "The original or retained provider file remains authoritative according to its source provenance.",
    }


def extract_question_from_source_text(text: str, question_number: str) -> str:
    num = question_number.upper().removeprefix("Q").strip()
    if not num:
        return ""
    marker = rf"(?:Q(?:uestion)?\.?\s*)?{re.escape(num)}[\).:]"
    next_marker = r"(?:Q(?:uestion)?\.?\s*)?(?:\d{1,3}|[A-Z])[\).:]"
    match = re.search(
        rf"(?ms)(?:^|\n)\s*\*?\s*{marker}\s*(?P<body>.*?)(?=(?:\n\s*\*?\s*{next_marker}\s+)|(?:\n\s*(?:(?:Official|Teacher)\s+){{0,2}}(?:Solution|Solutions|Answer|Answers)\s*:?\s*$)|\Z)",
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


def openai_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def openai_urlopen(req: urllib.request.Request, timeout: int = 60):
    return urllib.request.urlopen(req, timeout=timeout, context=openai_ssl_context())


def openai_error_kind(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return "api_error"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = f"{type(reason).__name__}: {reason}"
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
        return "ssl_certificate"
    if isinstance(exc, urllib.error.URLError):
        return "network"
    return "unknown"


def openai_error_summary(exc: BaseException) -> str:
    kind = openai_error_kind(exc)
    if kind == "ssl_certificate":
        return "OpenAI is configured, but the HTTPS request failed certificate verification."
    if kind == "network":
        return "OpenAI is configured, but the HTTPS request failed due to a network error."
    if kind == "api_error":
        status = getattr(exc, "code", "unknown")
        return f"OpenAI is configured, but the API returned HTTP {status}."
    return "OpenAI is configured, but the request failed."


def conversation_title(prompt: str, context: dict[str, Any]) -> str:
    cleaned = re.sub(r"\s+", " ", prompt).strip()
    base = cleaned[:54].rstrip(" .,;:") or "Study conversation"
    file_name = str(context.get("file") or "")
    if file_name and len(base) < 42:
        base = f"{base} — {Path(file_name).stem[:28]}"
    return base


def safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def source_history_projection(source: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "id",
        "file_id",
        "source_file_id",
        "course_code",
        "display_course_code",
        "week_label",
        "category",
        "exercise_type",
        "filename",
        "source_location",
        "page_start",
        "page_end",
        "slide_start",
        "slide_end",
        "heading",
    )
    out = {key: source.get(key) for key in allowed if key in source}
    if "course_code" in out:
        out["display_course_code"] = display_course_code(str(out["course_code"]))
    return out


def public_conversation(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    context = safe_json_loads(data.get("context_json"), {})
    return {
        "id": data["id"],
        "title": data["title"],
        "scope": data.get("scope") or context.get("scope") or "",
        "course_code": data.get("course_code") or context.get("course") or "",
        "display_course_code": display_course_code(data.get("course_code") or context.get("course") or ""),
        "week_label": data.get("week_label") or context.get("week") or "",
        "file_id": data.get("file_id") or context.get("fileId"),
        "filename": context.get("file") or "",
        "context": context,
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def public_ai_message(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "conversation_id": data["conversation_id"],
        "role": data["role"],
        "body": data["body"],
        "text": data["body"],
        "status": data.get("status") or "",
        "sources": safe_json_loads(data.get("sources_json"), []),
        "questions": safe_json_loads(data.get("questions_json"), []),
        "solutions": safe_json_loads(data.get("solutions_json"), []),
        "created_at": data["created_at"],
    }


def ensure_conversation(conn: sqlite3.Connection, conversation_id: int | None, context: dict[str, Any], prompt: str, scope: str) -> sqlite3.Row:
    now = now_iso()
    if conversation_id:
        row = conn.execute("SELECT * FROM ai_conversations WHERE id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
        if row:
            return row
    title = conversation_title(prompt, context)
    conn.execute(
        """
        INSERT INTO ai_conversations(title, scope, context_json, course_code, week_label, file_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            scope,
            json.dumps(context, ensure_ascii=False),
            context.get("course") or "",
            context.get("week") or "",
            int(context["fileId"]) if context.get("fileId") else None,
            now,
            now,
        ),
    )
    return conn.execute("SELECT * FROM ai_conversations WHERE id=last_insert_rowid()").fetchone()


def append_ai_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    role: str,
    body: str,
    status: str = "",
    sources: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    solutions: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ai_messages(conversation_id, role, body, status, sources_json, questions_json, solutions_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            role,
            body[:80_000],
            status,
            json.dumps([source_history_projection(source) for source in (sources or [])], ensure_ascii=False),
            json.dumps(questions or [], ensure_ascii=False),
            json.dumps(solutions or [], ensure_ascii=False),
            now_iso(),
        ),
    )


def update_conversation_context(conn: sqlite3.Connection, conversation_id: int, context: dict[str, Any], scope: str) -> None:
    conn.execute(
        """
        UPDATE ai_conversations
        SET scope=?, context_json=?, course_code=?, week_label=?, file_id=?, updated_at=?
        WHERE id=? AND deleted_at IS NULL
        """,
        (
            scope,
            json.dumps(context, ensure_ascii=False),
            context.get("course") or "",
            context.get("week") or "",
            int(context["fileId"]) if context.get("fileId") else None,
            now_iso(),
            conversation_id,
        ),
    )


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
        "When writing mathematics, use Markdown prose with $...$ for inline math and $$...$$ for display math. "
        "Never expose LaTeX commands outside math delimiters, and do not wrap mathematical explanations in code blocks. "
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
    with openai_urlopen(req, timeout=60) as resp:
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
    with openai_urlopen(req, timeout=60) as resp:
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
    with openai_urlopen(req, timeout=60) as resp:
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
    with openai_urlopen(req, timeout=120) as resp:
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
    visibility = course_visibility_sql("c")
    unchanged = conn.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM files f
        JOIN courses c ON c.id=f.course_id
        JOIN ai_index_state ais ON ais.file_id=f.id AND ais.provider='openai'
        WHERE f.active=1 AND f.is_official=1 AND f.suspicious='' AND f.ai_index_status='indexed'
          AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
          AND ais.sha256=f.sha256 AND ais.status IN ('indexed', 'completed', 'in_progress')
        """
    ).fetchone()["c"]
    rows = conn.execute(
        f"""
        SELECT f.*, ais.provider_file_id AS old_provider_file_id, ais.vector_store_id AS old_vector_store_id,
          ais.sha256 AS old_sha256
        FROM files f
        JOIN courses c ON c.id=f.course_id
        LEFT JOIN ai_index_state ais ON ais.file_id=f.id AND ais.provider='openai'
        WHERE f.active=1 AND f.is_official=1 AND f.suspicious='' AND f.ai_index_status='indexed'
          AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
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


def local_bridge_response(
    prompt: str,
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]] | None = None,
    intro: str | None = None,
) -> str:
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
    heading = intro or "OpenAI is not set up, so here are relevant excerpts from your local materials."
    return heading + "\n\n" + "\n\n".join(excerpts) + "\n\nSources\n" + format_sources(chunks)


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
        request_path = urllib.parse.urlparse(getattr(self, "path", "")).path
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if request_path.startswith("/preview/"):
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'self';",
            )
        else:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none';",
            )
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
            self.send_error_json(404, str(exc) if str(exc) else "Not found")
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
            elif parsed.path == "/api/conversations":
                self.handle_conversation_mutation()
            elif parsed.path == "/api/ai-sync":
                self.send_json(sync_openai_vector_store())
            elif parsed.path == "/api/config/study-library":
                self.handle_study_library_config()
            elif parsed.path == "/api/canvas/identity":
                self.handle_canvas_identity()
            elif parsed.path == "/api/canvas/courses":
                self.handle_canvas_courses()
            elif parsed.path == "/api/canvas/course-selection/preview":
                self.handle_canvas_course_selection_preview()
            elif parsed.path == "/api/canvas/course-selection/apply":
                self.handle_canvas_course_selection_apply()
            elif parsed.path == "/api/canvas/sync-preview":
                self.handle_canvas_sync_preview()
            elif parsed.path == "/api/canvas/sync-apply":
                self.handle_canvas_sync_apply()
            elif parsed.path == "/api/backup/export":
                self.handle_backup_export()
            elif parsed.path == "/api/backup/restore-preview":
                self.handle_backup_restore_preview()
            elif parsed.path == "/api/backup/restore-discard":
                self.handle_backup_restore_discard()
            elif parsed.path == "/api/whats-new/seen":
                self.handle_whats_new_seen()
            elif parsed.path in {
                "/api/terms/manage",
                "/api/courses/manage",
                "/api/weeks/manage",
                "/api/materials/import-paths",
                "/api/materials/import-folder",
                "/api/materials/manage",
                "/api/cache/clear",
                "/api/reset",
                "/api/config/managed-workspace",
            }:
                self.handle_product_management(parsed.path)
            else:
                self.send_error_json(404, "Not found")
        except Exception as exc:
            self.handle_exception(exc)

    def send_canvas_error(self, error: CanvasConnectorError) -> None:
        status = {
            "connection_not_configured": 409,
            "credential_missing": 409,
            "credential_backend_unavailable": 503,
            "credential_access_denied": 403,
            "credential_operation_failed": 503,
            "invalid_canvas_origin": 400,
            "malformed_canvas_connection": 409,
            "authentication_failed": 401,
            "permission_denied": 403,
            "rate_limited": 429,
            "canvas_unavailable": 503,
            "malformed_canvas_response": 502,
            "duplicate_canvas_file_id": 502,
            "canvas_file_identity_mismatch": 502,
            "unsafe_canvas_download_url": 502,
            "canvas_download_redirect_limit": 502,
            "canvas_download_failed": 502,
            "canvas_file_too_large": 413,
            "canvas_file_size_mismatch": 502,
            "canvas_retained_content_conflict": 409,
            "canvas_file_changed_during_acquisition": 409,
            "canvas_file_unavailable": 409,
            "unsupported_canvas_file_type": 409,
            "stale_canvas_sync_plan": 409,
            "unknown_canvas_sync_item": 400,
            "canvas_sync_action_not_applicable": 409,
            "canvas_material_not_acquired": 409,
            "canvas_course_binding_invalid": 409,
            "response_too_large": 502,
            "transport_failure": 503,
            "origin_mismatch": 403,
            "cross_origin_redirect": 403,
            "cross_origin_pagination": 403,
            "pagination_loop": 502,
            "pagination_limit_exceeded": 502,
            "unsupported_canvas_endpoint": 400,
            "canvas_api_error": 502,
        }.get(error.code, 500)
        self.send_json({"ok": False, "error": error.code}, status)

    def handle_canvas_identity(self) -> None:
        body = self.parse_body_json()
        if body:
            raise ValueError("Canvas identity validation does not accept request parameters")
        try:
            identity = with_canvas_connector(lambda connector: connector.identity())
            self.send_json({"ok": True, "identity": public_canvas_identity(identity)})
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_canvas_courses(self) -> None:
        body = self.parse_body_json()
        enrollment_state = str(body.get("enrollment_state") or "active")
        if enrollment_state not in {"active", "completed", "invited_or_pending"}:
            raise ValueError("Unsupported Canvas enrollment_state")
        try:
            courses = with_canvas_connector(lambda connector: connector.courses(enrollment_state=enrollment_state))
            self.send_json({"ok": True, "courses": [public_canvas_course(course) for course in courses]})
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_canvas_course_selection_preview(self) -> None:
        body = self.parse_body_json()
        requested_ids = normalize_requested_remote_ids(body.get("remote_course_ids", []))
        enrollment_state = str(body.get("enrollment_state") or "active")
        if enrollment_state not in {"active", "completed", "invited_or_pending"}:
            raise ValueError("Unsupported Canvas enrollment_state")
        try:
            def build(connector: CanvasConnector) -> dict[str, Any]:
                conn = connect_db()
                init_db(conn)
                try:
                    authority_id = canvas_authority_id(connector.record.origin)
                    courses = connector.courses(enrollment_state=enrollment_state)
                    return canvas_course_selection_plan(conn, authority_id, courses, requested_ids)
                finally:
                    conn.close()

            self.send_json({"ok": True, "plan": with_canvas_connector(build)})
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_canvas_course_selection_apply(self) -> None:
        body = self.parse_body_json()
        requested_ids = normalize_requested_remote_ids(body.get("remote_course_ids", []))
        expected_revision = str(body.get("selection_revision") or "")
        enrollment_state = str(body.get("enrollment_state") or "active")
        if enrollment_state not in {"active", "completed", "invited_or_pending"}:
            raise ValueError("Unsupported Canvas enrollment_state")
        try:
            def apply_selection(connector: CanvasConnector) -> dict[str, Any]:
                conn = connect_db()
                init_db(conn)
                try:
                    authority_id = canvas_authority_id(connector.record.origin)
                    courses = connector.courses(enrollment_state=enrollment_state)
                    result = apply_canvas_course_selection(conn, authority_id, courses, requested_ids, expected_revision)
                    conn.commit()
                    return result
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

            self.send_json({"ok": True} | with_canvas_connector(apply_selection))
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_canvas_sync_preview(self) -> None:
        body = self.parse_body_json()
        if set(body) - {"offering_ids"}:
            raise ValueError("Canvas sync preview accepts only offering_ids")
        offering_ids = normalize_requested_offering_ids(body.get("offering_ids", []))
        try:
            def build(connector: CanvasConnector) -> dict[str, Any]:
                conn = connect_db()
                try:
                    run_schema_migrations(conn)
                    conn.commit()
                    return canvas_file_sync_plan(conn, connector, offering_ids)
                finally:
                    conn.close()

            self.send_json({"ok": True, "plan": with_canvas_connector(build)})
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_canvas_sync_apply(self) -> None:
        body = self.parse_body_json()
        if set(body) - {"plan_revision", "remote_file_stable_ids", "offering_ids"}:
            raise ValueError("Canvas sync apply accepts only plan_revision, remote_file_stable_ids, and offering_ids")
        expected_revision = str(body.get("plan_revision") or "")
        requested_ids = normalize_requested_canvas_file_ids(body.get("remote_file_stable_ids", []))
        offering_ids = normalize_requested_offering_ids(body.get("offering_ids", []))
        try:
            def apply_sync(connector: CanvasConnector) -> dict[str, Any]:
                return run_canvas_sync_apply(
                    connector,
                    expected_revision,
                    requested_ids,
                    requested_offering_ids=offering_ids,
                )

            self.send_json({"ok": True} | with_canvas_connector(apply_sync))
        except CanvasConnectorError as exc:
            self.send_canvas_error(exc)

    def handle_backup_export(self) -> None:
        body = self.parse_body_json()
        if body:
            raise ValueError("Backup export does not accept request parameters")
        result = export_current_profile_backup()
        self.send_json({"ok": True, "backup": result})

    def handle_backup_restore_preview(self) -> None:
        body = self.parse_body_json()
        if set(body) != {"restore_id"}:
            raise ValueError("Restore preview accepts only restore_id")
        self.send_json({"ok": True, "plan": preview_staged_restore(body.get("restore_id"))})

    def handle_backup_restore_discard(self) -> None:
        body = self.parse_body_json()
        if set(body) != {"restore_id"}:
            raise ValueError("Restore discard accepts only restore_id")
        self.send_json(discard_staged_restore(body.get("restore_id")))

    def handle_whats_new_seen(self) -> None:
        body = self.parse_body_json()
        if set(body) != {"event_ids"}:
            raise ValueError("What's New seen update accepts only event_ids")
        event_ids = normalize_academic_change_event_ids(body.get("event_ids"))
        conn = connect_db()
        init_db(conn)
        try:
            result = mark_academic_changes_seen(conn, event_ids)
            conn.commit()
            self.send_json(result)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def handle_study_library_config(self) -> None:
        body = self.parse_body_json()
        self.send_json(save_study_library_config(str(body.get("path") or "")))

    def handle_product_management(self, path: str) -> None:
        body = self.parse_body_json()
        if path == "/api/config/managed-workspace":
            self.send_json(configure_managed_workspace())
            return
        if path in {"/api/materials/import-paths", "/api/materials/import-folder"} and not DESKTOP_MODE:
            raise PermissionError("Native file and folder import is available in the desktop app")
        conn = connect_db()
        init_db(conn)
        try:
            if path == "/api/terms/manage":
                result = manage_term(conn, body)
            elif path == "/api/courses/manage":
                result = manage_course(conn, body)
            elif path == "/api/weeks/manage":
                result = manage_week(conn, body)
            elif path == "/api/materials/import-paths":
                result = register_material_paths(conn, body)
            elif path == "/api/materials/import-folder":
                result = import_course_folder(conn, body)
            elif path == "/api/materials/manage":
                result = manage_materials(conn, body)
            elif path == "/api/cache/clear":
                result = clear_recreatable_cache(conn)
            elif path == "/api/reset":
                if body.get("confirmation") != "RESET STUDYHUB":
                    raise ValueError("Type RESET STUDYHUB to confirm the local metadata reset")
                result = reset_studyhub_state(conn)
            else:
                raise FileNotFoundError("Not found")
            self.send_json(result)
        finally:
            conn.close()

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
            elif path == "/api/preflight":
                self.send_json(api_preflight(conn))
            elif path == "/api/terms":
                include_archived = qs.get("include_archived", ["0"])[0] == "1"
                sql = "SELECT * FROM terms"
                if not include_archived:
                    sql += " WHERE archived=0"
                sql += " ORDER BY sort_order, name"
                self.send_json(rows_to_dicts(conn.execute(sql).fetchall()))
            elif path == "/api/material-types":
                self.send_json({"types": list(MATERIAL_TYPES)})
            elif path == "/api/courses":
                include_archived = qs.get("include_archived", ["0"])[0] == "1"
                rows = conn.execute(
                    f"""
                    SELECT c.*, t.name AS term_name, COUNT(DISTINCT f.id) AS file_count,
                      MAX(CASE WHEN w.has_materials=1 THEN w.week_number ELSE NULL END) AS latest_week
                    FROM courses c
                    LEFT JOIN terms t ON t.id=c.term_id
                    LEFT JOIN files f ON f.course_id=c.id AND f.active=1 AND f.removed_at IS NULL
                    LEFT JOIN weeks w ON w.course_id=c.id AND w.removed_at IS NULL
                    WHERE {course_visibility_sql('c', include_archived_term=include_archived)} {' ' if include_archived else 'AND c.archived=0'}
                    GROUP BY c.id
                    ORDER BY t.sort_order, t.name, c.sort_order, c.course_code, c.display_name
                    """
                ).fetchall()
                self.send_json([public_course(row) for row in rows])
            elif path == "/api/weeks":
                course_id = int(qs.get("course_id", ["0"])[0])
                include_archived = qs.get("include_archived", ["0"])[0] == "1"
                rows = conn.execute(
                    f"SELECT w.* FROM weeks w JOIN courses c ON c.id=w.course_id WHERE w.course_id=? AND w.removed_at IS NULL AND c.archived=0 AND {course_visibility_sql('c')} {' ' if include_archived else 'AND w.archived=0'} ORDER BY CASE WHEN w.week_number IS NULL THEN 1 ELSE 0 END, w.week_number, w.week_label",
                    (course_id,),
                ).fetchall()
                self.send_json([public_week(row) for row in rows])
            elif path == "/api/files":
                course_id = int(qs.get("course_id", ["0"])[0])
                week_label = qs.get("week", [""])[0]
                params: list[Any] = [course_id]
                sql = f"SELECT f.*, s.id AS star_id FROM files f JOIN courses c ON c.id=f.course_id LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id WHERE f.course_id=? AND f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {course_visibility_sql('c')}"
                if week_label:
                    sql += " AND f.week_label=?"
                    params.append(week_label)
                sql += " ORDER BY f.week_label, f.section, f.category, f.filename"
                self.send_json([public_file(row) for row in conn.execute(sql, params).fetchall()])
            elif path == "/api/inbox":
                rows = conn.execute(
                    """
                    SELECT f.*, s.id AS star_id FROM files f
                    LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id
                    WHERE f.active=1 AND f.removed_at IS NULL AND f.inbox=1
                    ORDER BY f.indexed_at DESC
                    """
                ).fetchall()
                self.send_json([public_file(row) for row in rows])
            elif path == "/api/materials/suggest":
                if not DESKTOP_MODE:
                    raise PermissionError("Native classification suggestions are available in the desktop app")
                raw = qs.get("path", [""])[0]
                self.send_json(classification_suggestion(conn, validate_reference_path(raw)))
            elif path == "/api/file":
                file_id = int(qs.get("id", ["0"])[0])
                row = get_file(conn, file_id)
                self.send_json(public_file(row, include_text=read_cached_text(row, 8000)))
            elif path == "/api/notes":
                self.send_json(self.handle_notes_get(conn, qs))
            elif path == "/api/conversations":
                self.send_json(self.handle_conversations_get(conn, qs))
            elif path == "/api/conversation":
                self.send_json(self.handle_conversation_get(conn, qs))
            elif path == "/api/search":
                query = qs.get("q", [""])[0].strip()
                course_id = qs.get("course_id", [""])[0]
                week = qs.get("week", [""])[0]
                scope = qs.get("scope", [""])[0]
                include_archived = qs.get("include_archived", ["0"])[0] == "1"
                archive_clause = "" if include_archived else "AND c.archived=0"
                visibility = course_visibility_sql("c", include_archived_term=include_archived)
                params: list[Any] = []
                normalized_query = fts_query(query)
                if normalized_query:
                    sql = """
                    SELECT f.*, s.id AS star_id, bm25(files_fts) AS rank
                    FROM files_fts JOIN files f ON files_fts.rowid=f.id
                    JOIN courses c ON c.id=f.course_id
                    LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id
                    WHERE files_fts MATCH ? AND f.active=1 AND f.removed_at IS NULL {archive_clause} AND {visibility}
                    """
                    sql = sql.format(archive_clause=archive_clause, visibility=visibility)
                    params.append(normalized_query)
                else:
                    sql = """
                    SELECT f.*, s.id AS star_id, 0 AS rank
                    FROM files f JOIN courses c ON c.id=f.course_id
                    LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id
                    WHERE f.active=1 AND f.removed_at IS NULL {archive_clause} AND {visibility}
                    """
                    sql = sql.format(archive_clause=archive_clause, visibility=visibility)
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
                rows = conn.execute(
                    """
                    SELECT f.*, s.id AS star_id
                    FROM files f JOIN courses c ON c.id=f.course_id
                    LEFT JOIN stars s ON s.target_type='file' AND s.target_id=f.id
                    WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
                    ORDER BY f.indexed_at DESC
                    LIMIT 20
                    """
                    .format(visibility=course_visibility_sql("c"))
                ).fetchall()
                self.send_json([public_file(row) for row in rows])
            elif path == "/api/starred":
                rows = conn.execute(
                    f"SELECT f.*, s.id AS star_id FROM stars s JOIN files f ON f.id=s.target_id JOIN courses c ON c.id=f.course_id WHERE s.target_type='file' AND f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {course_visibility_sql('c')} ORDER BY s.created_at DESC"
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
            elif path == "/api/canvas/selected-courses":
                self.send_json({"ok": True, "selected": remembered_canvas_course_selections(conn)})
            elif path == "/api/whats-new":
                unseen_value = qs.get("unseen_only", ["0"])[0]
                if unseen_value not in {"0", "1"}:
                    raise ValueError("unseen_only must be 0 or 1")
                course_offering_id = qs.get("course_offering_id", [""])[0].strip()
                try:
                    limit = int(qs.get("limit", [str(WHATS_NEW_DEFAULT_LIMIT)])[0])
                except ValueError as exc:
                    raise ValueError("limit must be an integer") from exc
                counts = academic_change_counts(conn, course_offering_stable_id=course_offering_id)
                self.send_json(
                    {
                        "events": list_academic_changes(
                            conn,
                            unseen_only=unseen_value == "1",
                            course_offering_stable_id=course_offering_id,
                            limit=limit,
                        ),
                        **counts,
                    }
                )
            elif path == "/api/wrong-questions":
                rows = conn.execute("SELECT * FROM wrong_questions ORDER BY created_at DESC LIMIT 100").fetchall()
                self.send_json(rows_to_dicts(rows))
            else:
                self.send_error_json(404, "Not found")
        finally:
            conn.close()

    def handle_notes_get(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> list[dict[str, Any]]:
        target_type = qs.get("targetType", ["file"])[0] or "file"
        target_id = int(qs.get("targetId", ["0"])[0] or 0)
        if target_type != "file" or not target_id:
            return []
        get_file(conn, target_id)
        rows = conn.execute(
            """
            SELECT id, target_type, target_id, course_id, week_label, body, created_at, updated_at
            FROM notes
            WHERE target_type='file' AND target_id=?
            ORDER BY updated_at DESC, id DESC
            LIMIT 50
            """,
            (target_id,),
        ).fetchall()
        return rows_to_dicts(rows)

    def handle_conversations_get(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = """
        SELECT c.*, COUNT(m.id) AS message_count
        FROM ai_conversations c
        LEFT JOIN ai_messages m ON m.conversation_id=c.id
        WHERE c.deleted_at IS NULL
        """
        file_id = int(qs.get("file_id", ["0"])[0] or 0)
        course = qs.get("course", [""])[0]
        query = qs.get("q", [""])[0].strip()
        if file_id:
            sql += " AND c.file_id=?"
            params.append(file_id)
        if course:
            sql += " AND c.course_code=?"
            params.append(course)
        if query:
            sql += " AND (c.title LIKE ? OR EXISTS (SELECT 1 FROM ai_messages mx WHERE mx.conversation_id=c.id AND mx.body LIKE ?))"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = public_conversation(row)
            item["message_count"] = row["message_count"]
            item["source_available"] = True
            if item.get("file_id"):
                file_row = conn.execute("SELECT active FROM files WHERE id=?", (item["file_id"],)).fetchone()
                item["source_available"] = bool(file_row and file_row["active"])
            out.append(item)
        return out

    def handle_conversation_get(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> dict[str, Any]:
        conversation_id = int(qs.get("id", ["0"])[0] or 0)
        row = conn.execute("SELECT * FROM ai_conversations WHERE id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
        if not row:
            raise FileNotFoundError("conversation not found")
        convo = public_conversation(row)
        if convo.get("file_id"):
            file_row = conn.execute("SELECT active FROM files WHERE id=?", (convo["file_id"],)).fetchone()
            convo["source_available"] = bool(file_row and file_row["active"])
        messages = conn.execute(
            """
            SELECT *
            FROM ai_messages
            WHERE conversation_id=?
            ORDER BY id
            """,
            (conversation_id,),
        ).fetchall()
        return {"conversation": convo, "messages": [public_ai_message(message) for message in messages]}

    def handle_conversation_mutation(self) -> None:
        body = self.parse_body_json()
        action = body.get("action", "create")
        conn = connect_db()
        init_db(conn)
        try:
            if action == "create":
                context = normalize_ask_context(conn, body.get("context", {}) or {}, "")
                scope = body.get("scope") or context.get("scope") or "course"
                title = str(body.get("title") or "New study conversation").strip()[:120]
                now = now_iso()
                conn.execute(
                    """
                    INSERT INTO ai_conversations(title, scope, context_json, course_code, week_label, file_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        scope,
                        json.dumps(context, ensure_ascii=False),
                        context.get("course") or "",
                        context.get("week") or "",
                        int(context["fileId"]) if context.get("fileId") else None,
                        now,
                        now,
                    ),
                )
                row = conn.execute("SELECT * FROM ai_conversations WHERE id=last_insert_rowid()").fetchone()
                conn.commit()
                self.send_json({"conversation": public_conversation(row), "messages": []})
                return
            if action == "clear":
                conn.execute("UPDATE ai_conversations SET deleted_at=?, updated_at=? WHERE deleted_at IS NULL", (now_iso(), now_iso()))
                conn.commit()
                self.send_json({"ok": True})
                return
            conversation_id = int(body.get("id") or 0)
            if not conversation_id:
                raise ValueError("conversation id is required")
            row = conn.execute("SELECT * FROM ai_conversations WHERE id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
            if not row:
                raise FileNotFoundError("conversation not found")
            if action == "rename":
                title = str(body.get("title") or "").strip()[:120]
                if not title:
                    raise ValueError("title is required")
                conn.execute("UPDATE ai_conversations SET title=?, updated_at=? WHERE id=?", (title, now_iso(), conversation_id))
                conn.commit()
                row = conn.execute("SELECT * FROM ai_conversations WHERE id=?", (conversation_id,)).fetchone()
                self.send_json({"conversation": public_conversation(row)})
                return
            if action == "delete":
                conn.execute("UPDATE ai_conversations SET deleted_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), conversation_id))
                conn.commit()
                self.send_json({"ok": True})
                return
            if action in {"duplicate", "fork"}:
                now = now_iso()
                original = dict(row)
                new_title = str(body.get("title") or f"{original['title']} copy").strip()[:120]
                conn.execute(
                    """
                    INSERT INTO ai_conversations(title, scope, context_json, course_code, week_label, file_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_title,
                        original.get("scope") or "",
                        original.get("context_json") or "{}",
                        original.get("course_code") or "",
                        original.get("week_label") or "",
                        original.get("file_id"),
                        now,
                        now,
                    ),
                )
                new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                messages = conn.execute("SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
                for message in messages:
                    conn.execute(
                        """
                        INSERT INTO ai_messages(conversation_id, role, body, status, sources_json, questions_json, solutions_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id,
                            message["role"],
                            message["body"],
                            message["status"],
                            message["sources_json"],
                            message["questions_json"],
                            message["solutions_json"],
                            now_iso(),
                        ),
                    )
                conn.commit()
                new_row = conn.execute("SELECT * FROM ai_conversations WHERE id=?", (new_id,)).fetchone()
                self.send_json({"conversation": public_conversation(new_row)})
                return
            raise ValueError("unknown conversation action")
        finally:
            conn.close()

    def handle_questions(self, conn: sqlite3.Connection, qs: dict[str, list[str]]) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = """
        SELECT q.*, f.filename, f.rel_path, f.id AS source_file_id
        FROM questions q
        JOIN files f ON f.id=q.file_id
        JOIN courses c ON c.id=f.course_id
        WHERE f.active=1 AND f.removed_at IS NULL AND q.official_source=1
          AND c.archived=0 AND {visibility}
        """
        sql = sql.format(visibility=course_visibility_sql("c"))
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
        rows = rows_to_dicts(conn.execute(sql, params).fetchall())
        for row in rows:
            row["display_course_code"] = display_course_code(row.get("course_code", ""))
        return rows

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
        visibility = course_visibility_sql("c", include_system=True)
        rows = conn.execute(
            f"""
            SELECT ais.status, COUNT(*) AS count
            FROM ai_index_state ais
            JOIN files f ON f.id=ais.file_id
            JOIN courses c ON c.id=f.course_id
            WHERE f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
            GROUP BY ais.status ORDER BY ais.status
            """
        ).fetchall()
        indexed_files = conn.execute(
            f"SELECT COUNT(*) AS c FROM files f JOIN courses c ON c.id=f.course_id WHERE f.active=1 AND f.removed_at IS NULL AND f.ai_index_status='indexed' AND c.archived=0 AND {visibility}"
        ).fetchone()["c"]
        vector_indexed = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM ai_index_state ais
            JOIN files f ON f.id=ais.file_id
            JOIN courses c ON c.id=f.course_id
            WHERE ais.provider='openai' AND ais.status IN ('indexed', 'completed', 'in_progress')
              AND f.active=1 AND f.removed_at IS NULL AND c.archived=0 AND {visibility}
            """
        ).fetchone()["c"]
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
        if path.startswith("/vendor/katex/"):
            self.serve_katex_asset(path)
            return
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

    def serve_katex_asset(self, path: str) -> None:
        rel = path.removeprefix("/vendor/katex/")
        target = (KATEX_DIST_DIR / rel).resolve()
        if not is_inside(target, KATEX_DIST_DIR) or not target.exists() or target.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_escaped_text_preview(self, path: Path, text: str) -> None:
        body = html.escape(text[:20000] or "No text preview available. Use Open Original.").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(path.name + '.txt')}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-StudyHub-Preview-Mode", "text")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; frame-ancestors 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_preview_file(self, path: Path, content_type: str, filename: str, mode: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(filename)}")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("X-StudyHub-Preview-Mode", mode)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def send_preview_notice(self, filename: str, title: str, message: str, mode: str = "unavailable") -> None:
        body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #f7f6f2;
        color: #201f1b;
        font: 16px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        max-width: 640px;
        margin: 32px;
        padding: 24px;
        border: 1px solid #dedbd2;
        background: #fffdf8;
        box-shadow: 0 10px 30px rgba(0,0,0,.06);
      }}
      h1 {{ margin: 0 0 12px; font-size: 1.35rem; }}
      p {{ margin: 0 0 12px; }}
      .file {{ color: #706b61; word-break: break-word; }}
    </style>
  </head>
  <body>
    <main>
      <p class="file">{html.escape(filename)}</p>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
      <p>Use the Readable Text tab for extracted text, or Open original file from More if you need the authoritative document.</p>
    </main>
  </body>
</html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-StudyHub-Preview-Mode", mode)
        self.send_header("X-Content-Type-Options", "nosniff")
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
            if ext in OFFICE_PDF_PREVIEW_EXTS:
                preview = convert_office_document_to_pdf(row, path)
                if preview.path:
                    self.send_preview_file(preview.path, "application/pdf", f"{path.stem}.pdf", "converted_pdf")
                elif preview.status == "missing_converter":
                    self.send_preview_notice(path.name, "PowerPoint/Word visual preview requires LibreOffice", preview.message, "unavailable")
                else:
                    self.send_preview_notice(path.name, "Preview unavailable", preview.message or "StudyHub could not create a visual preview for this Office file.", "unavailable")
                return
            if ext in SPREADSHEET_PREVIEW_EXTS:
                self.send_preview_notice(path.name, "Spreadsheet visual preview is not available yet", "StudyHub keeps the original spreadsheet as the source of truth and can still use readable text when available.", "unavailable")
                return
            if ext in TEXT_PREVIEW_EXTS:
                text = extract_text(path) if not ext.startswith(".ipynb") else extract_text(path)
                self.send_escaped_text_preview(path, text)
                return
            if ext == ".pdf" or ctype == "application/pdf":
                self.send_preview_file(path, "application/pdf", path.name, "native_pdf")
                return
            if ext in IMAGE_PREVIEW_EXTS or ctype.startswith("image/"):
                self.send_preview_file(path, ctype, path.name, "image")
                return
            self.send_preview_notice(path.name, "Preview unavailable", "StudyHub does not have a safe visual preview for this file type.", "unavailable")
        finally:
            conn.close()

    def handle_open(self, file_id: int) -> None:
        conn = connect_db()
        try:
            row = get_file(conn, file_id)
            path = Path(row["original_path"]).resolve()
            if not material_path_allowed(row, path):
                raise PermissionError("Original file is outside the StudyHub material allowlist.")
            if not path.exists():
                raise FileNotFoundError("Original file is missing. Scan Library to refresh your file list.")
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
        try:
            target_type = body.get("targetType", "file")
            target_id = int(body.get("targetId") or 0)
            if target_type != "file" or not target_id:
                raise ValueError("Notes must target a file")
            file_row = get_file(conn, target_id)
            note_body = str(body.get("body", "")).strip()
            if not note_body:
                raise ValueError("Note body is required")
            conn.execute(
                "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    target_type,
                    target_id,
                    body.get("courseId") or file_row["course_id"],
                    body.get("week") or file_row["week_label"],
                    note_body[:10_000],
                    now_iso(),
                    now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.send_json({"ok": True})

    def handle_ask(self) -> None:
        body = self.parse_body_json()
        conn = connect_db()
        prompt = body.get("prompt", "").strip()
        context = normalize_ask_context(conn, body.get("context", {}) or {}, prompt)
        scope = str(body.get("scope") or context.get("scope") or "").strip() or ("question" if context.get("questionId") else "file" if context.get("fileId") else "week" if context.get("week") else "course")
        conversation_id = int(body.get("conversationId") or body.get("conversation_id") or 0) or None
        conversation = ensure_conversation(conn, conversation_id, context, prompt, scope)
        conversation_id = int(conversation["id"])
        update_conversation_context(conn, conversation_id, context, scope)
        append_ai_message(conn, conversation_id, "user", prompt, sources=[], questions=[], solutions=[])
        question_scope = has_specific_teacher_question_scope(context, prompt)
        questions = teacher_questions_for_context(conn, context, prompt, limit=5) if question_scope else []
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
            append_ai_message(conn, conversation_id, "assistant", response, status, sources=[], questions=questions, solutions=solutions)
            conn.execute("UPDATE ai_conversations SET updated_at=? WHERE id=?", (now_iso(), conversation_id))
            conn.commit()
            messages = conn.execute("SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
            convo = conn.execute("SELECT * FROM ai_conversations WHERE id=?", (conversation_id,)).fetchone()
            conn.close()
            self.send_json(
                {
                    "status": status,
                    "response": response,
                    "sources": [],
                    "questions": questions,
                    "solutions": solutions,
                    "conversation": public_conversation(convo),
                    "conversationId": conversation_id,
                    "messages": [public_ai_message(message) for message in messages],
                }
            )
            return
        chunks = search_local_context(conn, prompt, context, limit=8)
        if chunks and not questions and QUESTION_INTENT_RE.search(prompt):
            questions = teacher_questions_for_context(conn, context, prompt, limit=5) if question_scope else []
        status = "local"
        if not chunks and not questions:
            response = "I couldn't find this in the currently indexed official course materials."
            status = "no_source"
            solutions = []
        elif os.environ.get("OPENAI_API_KEY"):
            try:
                response = openai_responses_request(prompt, chunks, context, questions=questions, solutions=solutions)
                status = "openai"
            except Exception as exc:
                intro = f"{openai_error_summary(exc)} Falling back to local indexed excerpts."
                response = (
                    f"{local_bridge_response(prompt, chunks, questions, intro=intro)}\n\n"
                    f"OpenAI error category: {openai_error_kind(exc)}"
                )
                log_event("openai_error", f"{openai_error_kind(exc)}:{type(exc).__name__}")
                status = "openai_error_local_fallback"
        else:
            response = local_bridge_response(prompt, chunks, questions)
        context_json = json.dumps({"context": context, "sources": chunks[:8], "questions": questions, "solutions": solutions}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO ai_interactions(context_json, prompt, response, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (context_json, prompt, response, status, now_iso()),
        )
        append_ai_message(conn, conversation_id, "assistant", response, status, sources=chunks[:8], questions=questions, solutions=solutions)
        conn.execute("UPDATE ai_conversations SET updated_at=? WHERE id=?", (now_iso(), conversation_id))
        conn.commit()
        messages = conn.execute("SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
        convo = conn.execute("SELECT * FROM ai_conversations WHERE id=?", (conversation_id,)).fetchone()
        conn.close()
        self.send_json(
            {
                "status": status,
                "response": response,
                "sources": chunks[:8],
                "questions": questions,
                "solutions": solutions,
                "conversation": public_conversation(convo),
                "conversationId": conversation_id,
                "messages": [public_ai_message(message) for message in messages],
            }
        )

    def handle_upload(self) -> None:
        if RUNTIME_PROFILE == "demo-test":
            raise PermissionError("Demo/Test mode cannot import external files.")
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
    if start_port == 0:
        return 0
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
    try:
        initialize_credential_client()
    except CredentialClientError:
        pass
    ensure_dirs()
    bind_host = validate_loopback_bind_host(DEFAULT_HOST)
    if scan_first:
        scan_library(DEFAULT_STUDY_ROOT)
    chosen_port = find_free_port(port)
    if chosen_port != port:
        print(f"Port {port} is already in use; StudyHub is using localhost port {chosen_port} instead.", flush=True)
    server = ThreadingHTTPServer((bind_host, chosen_port), StudyHubHandler)
    actual_port = int(server.server_address[1])
    display_host = "localhost" if bind_host in {"127.0.0.1", "::1", "localhost"} else bind_host
    url = f"http://{display_host}:{actual_port}"
    print(f"{APP_NAME} running at {url}", flush=True)
    print("Study library: configured local path", flush=True)
    print("SQLite database: local runtime database", flush=True)
    if open_browser:
        webbrowser.open(url)
    previous_sigterm = None
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def stop_server(_signum: int, _frame: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


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


def backup_metadata() -> dict[str, Any]:
    result = export_current_profile_backup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


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
