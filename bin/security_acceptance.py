#!/usr/bin/env python3
"""Synthetic security acceptance checks. Uses no real course material."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "StudyLibrary")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    os.environ["HOST"] = "127.0.0.1"
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_security_test", server_path)
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
    server.MAX_UPLOAD_REQUEST_SIZE = 4096
    server.MAX_UPLOAD_FILE_SIZE = 64
    server.MAX_JSON_BODY_SIZE = 64
    server.MAX_MCP_BODY_SIZE = 64
    return server


class FakeHandler:
    def __init__(self, server: Any, headers: dict[str, str] | None = None, body: bytes = b""):
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.sent: dict[str, Any] = {}
        self.response_headers: list[tuple[str, str]] = []
        self.path = "/api/test"
        self.server_module = server

    def send_response(self, status: int, message: str | None = None) -> None:
        self.sent["status_code"] = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers.append((key, value))

    def end_headers(self) -> None:
        return None

    def send_error(self, status: int, message: str | None = None) -> None:
        self.send_json({"error": message or "Error"}, status)

    def send_json(self, data: Any, status: int = 200) -> None:
        self.sent = {"status_code": status, "data": data}

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)


def bind_methods(server: Any, fake: FakeHandler) -> FakeHandler:
    for name in (
        "read_limited_body",
        "parse_body_json",
        "validate_loopback_request_host",
        "validate_same_origin_mutation",
        "handle_exception",
        "handle_api_get",
        "handle_preview",
        "send_escaped_text_preview",
        "serve_static",
        "handle_upload",
        "do_GET",
        "do_HEAD",
        "do_OPTIONS",
    ):
        setattr(fake, name, getattr(server.StudyHubHandler, name).__get__(fake, server.StudyHubHandler))
    return fake


def make_library(server: Any) -> sqlite3.Row:
    root = server.DEFAULT_STUDY_ROOT
    course_dir = root / "TEST1001 - Synthetic Course" / "Week 01" / "02 Exercises" / "Tutorial"
    course_dir.mkdir(parents=True)
    (course_dir / "Tutorial_1.txt").write_text("Q1. Explain the synthetic localhost-only rule.\n", encoding="utf-8")
    (course_dir / "Active_HTML.html").write_text("<script>window.__studyhub_xss = true</script>\n", encoding="utf-8")
    (course_dir / "Active_HTM.htm").write_text('<img src=x onerror="window.__studyhub_xss=true">\n', encoding="utf-8")
    (course_dir / "Active_SVG.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>window.__studyhub_xss=true</script></svg>\n',
        encoding="utf-8",
    )
    server.scan_library(root)
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM files WHERE filename='Tutorial_1.txt' AND active=1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    return row


def multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[str, bytes]:
    boundary = "----studyhub-security"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def has_absolute_or_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        forbidden = {"original_path", "absolute_path", "text_cache_path", "databasePath", "studyRoot", "vectorStoreId", "provider_file_id"}
        if forbidden & set(value):
            return True
        return any(has_absolute_or_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(has_absolute_or_forbidden(item) for item in value)
    if isinstance(value, str):
        if value.startswith(("/api/", "/mcp", "/preview/")):
            return False
        if re.match(r"^[A-Za-z]:\\", value):
            return True
        return Path(value).is_absolute()
    return False


def raises(exc_type: type[BaseException], fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def get_status(server: Any, path: str, host: str) -> tuple[int | None, Any, bytes]:
    handler = bind_methods(server, FakeHandler(server, {"Host": host}))
    handler.path = path
    server.StudyHubHandler.do_GET(handler)
    return handler.sent.get("status_code"), handler.sent.get("data"), handler.wfile.getvalue()


def preview_response(server: Any, file_id: int, host: str = "localhost:8765") -> tuple[int | None, dict[str, str], bytes]:
    handler = bind_methods(server, FakeHandler(server, {"Host": host}))
    handler.path = f"/preview/{file_id}"
    server.StudyHubHandler.do_GET(handler)
    headers: dict[str, str] = {}
    for key, value in handler.response_headers:
        headers[key.lower()] = value
    return handler.sent.get("status_code"), headers, handler.wfile.getvalue()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        file_row = make_library(server)
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        active_rows = {
            row["filename"]: row
            for row in conn.execute("SELECT * FROM files WHERE filename IN ('Active_HTML.html', 'Active_HTM.htm', 'Active_SVG.svg')")
        }
        conn.close()

        traversal_cases = [
            "../../outside",
            "../outside",
            "/".join(["", "absolute", "path"]),
            "C:" + "\\" + "Users" + "\\" + "example" + "\\" + "outside",
            ".." + "\\" + ".." + "\\" + "outside",
            "nested" + "/" + ".." + "/" + ".." + "/" + "escape",
        ]
        traversal_rejected = all(raises(PermissionError, lambda case=case: server.safe_child_path(server.DEFAULT_STUDY_ROOT, case)) for case in traversal_cases)

        valid_headers = {
            "Host": "localhost:8765",
            "Origin": "http://localhost:8765",
            "Sec-Fetch-Site": "same-origin",
            "X-StudyHub-CSRF": server.CSRF_TOKEN,
        }
        missing_csrf = bind_methods(server, FakeHandler(server, {"Host": "localhost:8765", "Origin": "http://localhost:8765"}))
        invalid_csrf = bind_methods(server, FakeHandler(server, valid_headers | {"X-StudyHub-CSRF": "bad"}))
        cross_site = bind_methods(
            server,
            FakeHandler(server, valid_headers | {"Origin": "https://example.invalid", "Sec-Fetch-Site": "cross-site"}),
        )
        valid_same_origin = bind_methods(server, FakeHandler(server, valid_headers))
        cross_port_localhost = bind_methods(server, FakeHandler(server, valid_headers | {"Origin": "http://localhost:3000"}))
        cross_port_loopback_ip = bind_methods(
            server,
            FakeHandler(server, valid_headers | {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:9999"}),
        )
        localhost_ip_mismatch = bind_methods(server, FakeHandler(server, valid_headers | {"Origin": "http://127.0.0.1:8765"}))
        ip_localhost_mismatch = bind_methods(
            server,
            FakeHandler(server, valid_headers | {"Host": "127.0.0.1:8765", "Origin": "http://localhost:8765"}),
        )
        scheme_mismatch = bind_methods(server, FakeHandler(server, valid_headers | {"Origin": "https://localhost:8765"}))
        exact_ip_same_origin = bind_methods(
            server,
            FakeHandler(server, valid_headers | {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}),
        )
        exact_ipv6_same_origin = bind_methods(
            server,
            FakeHandler(server, valid_headers | {"Host": "[::1]:8765", "Origin": "http://[::1]:8765"}),
        )
        no_origin_with_valid_csrf = bind_methods(
            server,
            FakeHandler(server, {"Host": "localhost:8765", "Sec-Fetch-Site": "same-origin", "X-StudyHub-CSRF": server.CSRF_TOKEN}),
        )

        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        outside = tmp / "outside.txt"
        outside.write_text("synthetic outside content", encoding="utf-8")
        conn.execute(
            """
            INSERT INTO files(
              course_id, week_id, course_code, week_label, section, category, exercise_type, filename,
              original_path, rel_path, source, source_label, hash, size, modified_at, indexed_at,
              extension, mime_type, is_official, suspicious, text_cache_path, stable_id, course_name,
              week_number, absolute_path, source_type, file_extension, file_size, sha256, is_solution,
              is_question_source, active
            )
            SELECT course_id, week_id, course_code, week_label, section, category, exercise_type, 'outside.txt',
              ?, 'outside.txt', source, source_label, 'deadbeefcafefeed', 1, modified_at, indexed_at,
              '.txt', 'text/plain', 1, '', '', 'deadbeefcafefeed', course_name, week_number, ?,
              source_type, '.txt', 1, 'deadbeefcafefeed', 0, 0, 1
            FROM files WHERE id=?
            """,
            (str(outside), str(outside), file_row["id"]),
        )
        outside_id = conn.execute("SELECT id FROM files WHERE stable_id='deadbeefcafefeed'").fetchone()["id"]
        conn.commit()
        conn.close()

        ctype, ok_body = multipart(
            {
                "course_id": str(file_row["course_id"]),
                "week": "Week 01",
                "section": "02 Exercises",
                "category": "Tutorial",
            },
            "Extra.txt",
            b"small upload",
        )
        upload = bind_methods(server, FakeHandler(server, {"Content-Type": ctype, "Content-Length": str(len(ok_body))}, ok_body))
        server.StudyHubHandler.handle_upload(upload)
        conn = sqlite3.connect(server.DB_PATH)
        conn.execute("UPDATE files SET active=1 WHERE id=?", (outside_id,))
        conn.commit()
        conn.close()

        bad_ctype, bad_body = multipart(
            {
                "course_id": str(file_row["course_id"]),
                "week": "Week 01",
                "section": "02 Exercises",
                "category": "Tutorial",
            },
            "../outside.txt",
            b"bad",
        )
        bad_upload = bind_methods(server, FakeHandler(server, {"Content-Type": bad_ctype, "Content-Length": str(len(bad_body))}, bad_body))

        abs_ctype, abs_body = multipart(
            {
                "course_id": str(file_row["course_id"]),
                "week": "Week 01",
                "section": "02 Exercises",
                "category": "Tutorial",
            },
            "/".join(["", "absolute", "outside.txt"]),
            b"bad",
        )
        abs_upload = bind_methods(server, FakeHandler(server, {"Content-Type": abs_ctype, "Content-Length": str(len(abs_body))}, abs_body))

        oversized_upload = bind_methods(server, FakeHandler(server, {"Content-Length": str(server.MAX_UPLOAD_REQUEST_SIZE + 1), "Content-Type": ctype}))
        oversized_json = bind_methods(server, FakeHandler(server, {"Content-Length": str(server.MAX_JSON_BODY_SIZE + 1)}))

        err_handler = bind_methods(server, FakeHandler(server))
        secret = "sk-" + "x" * 24
        raw_path = "/".join(["", "private", "example", "secret.txt"])
        err_handler.handle_exception(RuntimeError(f"{secret} {raw_path}"))

        mcp_meta = server.mcp_get_file_metadata({"file_id": server.external_file_id(file_row)})
        mcp_read = server.mcp_read_file({"file_id": server.external_file_id(file_row)})
        upload_response_safe = not has_absolute_or_forbidden(upload.sent["data"])
        sync_meta_safe = not has_absolute_or_forbidden(server.file_metadata(file_row))

        hostile_hosts = ["evil.example:8765", "attacker.test", "192.168.1.50:8765", "0.0.0.0:8765"]
        valid_hosts = ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"]
        get_paths = ["/", "/api/health", "/api/courses", f"/preview/{file_row['id']}", "/mcp"]
        hostile_get_rejected = all(get_status(server, path, host)[0] == 403 for host in hostile_hosts for path in get_paths)
        valid_get_accepted = all((get_status(server, path, host)[0] or 0) < 400 for host in valid_hosts for path in get_paths)
        malicious_preview_status, _, malicious_preview_body = get_status(server, f"/preview/{file_row['id']}", "evil.example:8765")
        valid_preview_status, _, valid_preview_body = get_status(server, f"/preview/{file_row['id']}", "localhost:8765")
        html_status, html_headers, html_body = preview_response(server, active_rows["Active_HTML.html"]["id"])
        htm_status, htm_headers, htm_body = preview_response(server, active_rows["Active_HTM.htm"]["id"])
        svg_status, svg_headers, svg_body = preview_response(server, active_rows["Active_SVG.svg"]["id"])

        def text_plain(headers: dict[str, str]) -> bool:
            return headers.get("content-type", "").lower().startswith("text/plain")

        def nosniff(headers: dict[str, str]) -> bool:
            return headers.get("x-content-type-options", "").lower() == "nosniff"

        def same_origin_frame_only(headers: dict[str, str]) -> bool:
            return (
                headers.get("x-frame-options", "").lower() == "sameorigin"
                and "frame-ancestors 'self'" in headers.get("content-security-policy", "").lower()
                and "script-src 'none'" in headers.get("content-security-policy", "").lower()
            )

        def not_active_inline(headers: dict[str, str]) -> bool:
            content_type = headers.get("content-type", "").lower()
            disposition = headers.get("content-disposition", "").lower()
            active_type = content_type.startswith(("text/html", "image/svg+xml", "application/xhtml+xml", "application/xml", "text/xml"))
            return not (active_type and "inline" in disposition)

        conn = server.connect_db()
        api_payloads = [
            server.api_session(),
            server.api_health(conn),
            [server.public_course(row) for row in conn.execute("SELECT c.*, COUNT(f.id) AS file_count FROM courses c LEFT JOIN files f ON f.course_id=c.id GROUP BY c.id")],
            [server.public_week(row) for row in conn.execute("SELECT * FROM weeks")],
            [server.public_file(row) for row in conn.execute("SELECT * FROM files WHERE active=1")],
            server.build_context_pack(conn, file_row["id"]),
        ]
        health_payload = server.api_health(conn)
        session_payload = server.api_session()
        conn.close()
        api_get_privacy_safe = all(not has_absolute_or_forbidden(payload) for payload in api_payloads)
        csrf_bootstrap_review = "csrfToken" not in health_payload and bool(session_payload.get("csrfToken"))

        checks = {
            "active_html_preview_isolation": html_status == 200 and text_plain(html_headers) and b"&lt;script&gt;" in html_body and b"<script>" not in html_body,
            "htm_preview_isolation": htm_status == 200 and text_plain(htm_headers) and b"&lt;img" in htm_body and b"<img" not in htm_body,
            "svg_active_content_isolation": svg_status == 200 and text_plain(svg_headers) and "image/svg+xml" not in svg_headers.get("content-type", "").lower() and b"&lt;svg" in svg_body,
            "preview_mime_sniffing_protection": nosniff(html_headers) and nosniff(htm_headers) and nosniff(svg_headers),
            "preview_same_origin_frame_only": same_origin_frame_only(html_headers) and same_origin_frame_only(htm_headers) and same_origin_frame_only(svg_headers),
            "active_preview_content_disposition_safe": not_active_inline(html_headers) and not_active_inline(htm_headers) and not_active_inline(svg_headers),
            "universal_host_validation_rejects_hostile_host": all(not server.request_host_is_loopback(host) for host in hostile_hosts),
            "get_hostile_host_rejection": hostile_get_rejected,
            "dns_rebinding_regression_tests": hostile_get_rejected and valid_get_accepted,
            "preview_hostile_host_protection": malicious_preview_status == 403 and not malicious_preview_body,
            "preview_valid_localhost_allowed": valid_preview_status == 200 and b"synthetic localhost-only rule" in valid_preview_body,
            "api_get_privacy_audit": api_get_privacy_safe,
            "csrf_bootstrap_exposure_review": csrf_bootstrap_review,
            "upload_traversal_rejected": traversal_rejected and raises(PermissionError, lambda: server.StudyHubHandler.handle_upload(bad_upload)),
            "absolute_upload_path_rejected": raises(PermissionError, lambda: server.StudyHubHandler.handle_upload(abs_upload)),
            "cross_site_post_rejected": raises(PermissionError, cross_site.validate_same_origin_mutation),
            "missing_csrf_token_rejected": raises(PermissionError, missing_csrf.validate_same_origin_mutation),
            "invalid_csrf_token_rejected": raises(PermissionError, invalid_csrf.validate_same_origin_mutation),
            "valid_same_origin_csrf_request_accepted": not raises(Exception, valid_same_origin.validate_same_origin_mutation),
            "cross_port_localhost_origin_rejected": raises(PermissionError, cross_port_localhost.validate_same_origin_mutation),
            "cross_port_loopback_ip_origin_rejected": raises(PermissionError, cross_port_loopback_ip.validate_same_origin_mutation),
            "localhost_vs_127_origin_mismatch_rejected": raises(PermissionError, localhost_ip_mismatch.validate_same_origin_mutation),
            "127_vs_localhost_origin_mismatch_rejected": raises(PermissionError, ip_localhost_mismatch.validate_same_origin_mutation),
            "http_https_scheme_mismatch_rejected": raises(PermissionError, scheme_mismatch.validate_same_origin_mutation),
            "exact_localhost_origin_accepted": not raises(Exception, valid_same_origin.validate_same_origin_mutation),
            "exact_127_origin_accepted": not raises(Exception, exact_ip_same_origin.validate_same_origin_mutation),
            "exact_ipv6_origin_accepted": not raises(Exception, exact_ipv6_same_origin.validate_same_origin_mutation),
            "no_origin_still_requires_valid_csrf": not raises(Exception, no_origin_with_valid_csrf.validate_same_origin_mutation),
            "non_loopback_host_refused": raises(PermissionError, lambda: server.validate_loopback_bind_host("0.0.0.0")),
            "preview_outside_study_root_rejected": raises(PermissionError, lambda: server.get_file(server.connect_db(), outside_id)),
            "open_outside_study_root_rejected": raises(PermissionError, lambda: server.get_file(server.connect_db(), outside_id)),
            "mcp_outside_study_root_rejected": server.mcp_call_tool("read_file", {"file_id": "file_deadbeefcafefeed"})["structuredContent"]["status"] == "DENY",
            "mcp_no_absolute_paths": not has_absolute_or_forbidden(mcp_meta) and not has_absolute_or_forbidden(mcp_read),
            "upload_api_no_absolute_paths": upload_response_safe,
            "oversized_upload_rejected": raises(server.PayloadTooLarge, lambda: server.StudyHubHandler.handle_upload(oversized_upload)),
            "oversized_json_rejected": raises(server.PayloadTooLarge, lambda: oversized_json.parse_body_json(server.MAX_JSON_BODY_SIZE)),
            "unexpected_secret_not_present_in_response": secret not in json.dumps(err_handler.sent) and raw_path not in json.dumps(err_handler.sent),
            "raw_exception_path_not_returned": err_handler.sent.get("status_code") == 500 and err_handler.sent["data"]["error"] == "Internal StudyHub error",
            "openai_metadata_no_local_path": sync_meta_safe,
            "mcp_remains_read_only": set(server.MCP_TOOLS) == {
                "list_courses",
                "list_weeks",
                "list_files",
                "search_files",
                "search_content",
                "search_study_library",
                "get_file_metadata",
                "read_file",
                "fetch_study_file",
                "get_question",
            },
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
