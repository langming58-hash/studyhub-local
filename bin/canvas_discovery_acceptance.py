#!/usr/bin/env python3
"""Synthetic acceptance checks for Canvas authenticated course discovery."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

TOKEN = "syn-token"
ORIGIN = "https://canvas.example.edu"
BIG_ID = "9007199254740995"


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def record(origin: str = ORIGIN, token: str = TOKEN) -> server.CanvasConnectionRecord:
    return server.CanvasConnectionRecord(origin=origin, access_token=token)


class FakeCanvasHTTP:
    def __init__(self, responses: list[server.CanvasHTTPResponse] | None = None):
        self.responses = list(responses or [])
        self.requests: list[tuple[str, dict[str, str], int]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: int) -> server.CanvasHTTPResponse:
        self.requests.append((url, dict(headers), timeout))
        if not self.responses:
            raise server.CanvasConnectorError("transport_failure")
        return self.responses.pop(0)


def response(status: int, payload: Any, headers: dict[str, str] | None = None) -> server.CanvasHTTPResponse:
    body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    return server.CanvasHTTPResponse(status=status, headers=headers or {}, body=body)


def connector_with(responses: list[server.CanvasHTTPResponse], *, origin: str = ORIGIN, max_pages: int = 10, max_items: int = 500) -> tuple[server.CanvasConnector, FakeCanvasHTTP]:
    fake = FakeCanvasHTTP(responses)
    return server.CanvasConnector(record(origin), http_get=fake, max_pages=max_pages, max_items=max_items), fake


def raises_code(expected: str, action) -> bool:
    try:
        action()
    except (server.CanvasConnectorError, server.CanvasConnectionError) as exc:
        code = getattr(exc, "code", str(exc))
        if isinstance(exc, server.CanvasConnectionError):
            code = str(exc)
        return code == expected and TOKEN not in str(exc) and TOKEN not in repr(exc)
    return False


def raises_connection_code(expected: str, action) -> bool:
    try:
        action()
    except server.CanvasConnectionError as exc:
        return exc.code == expected and TOKEN not in str(exc) and TOKEN not in repr(exc)
    return False


def main() -> int:
    failures: list[str] = []

    identity_connector, identity_http = connector_with([response(200, {"id": BIG_ID, "name": "Synthetic Learner"})])
    identity = identity_connector.identity()
    check("valid_current_user_response", identity.remote_user_id == BIG_ID and identity.display_name == "Synthetic Learner", failures)

    courses_payload = [
        {
            "id": BIG_ID,
            "name": "Synthetic Calculus",
            "course_code": "TEST1001",
            "workflow_state": "available",
            "start_at": "2026-02-01T00:00:00Z",
            "end_at": None,
            "term": {"id": BIG_ID, "name": "Synthetic Term"},
        }
    ]
    courses_connector, courses_http = connector_with([response(200, courses_payload)])
    courses = courses_connector.courses()
    check("valid_course_list_response", len(courses) == 1 and courses[0].name == "Synthetic Calculus", failures)
    check("canvas_id_greater_than_js_safe_integer_preserved", courses[0].remote_course_id == BIG_ID and courses[0].term_id == BIG_ID, failures)

    first_request = courses_http.requests[0]
    check("bearer_token_in_authorization_header", first_request[1].get("Authorization") == f"Bearer {TOKEN}", failures)
    check("canvas_string_id_accept_header", first_request[1].get("Accept") == server.CANVAS_ACCEPT_HEADER, failures)
    check("token_absent_from_url_query", TOKEN not in first_request[0] and "access_token" not in first_request[0], failures)
    check("request_timeout_is_bounded", first_request[2] == server.CANVAS_TIMEOUT_SECONDS, failures)

    status_expectations = [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (503, "canvas_unavailable"),
    ]
    for status, code in status_expectations:
        connector, _ = connector_with([response(status, {"private": TOKEN})])
        check(f"http_{status}_maps_to_{code}", raises_code(code, connector.identity), failures)

    malformed_connector, _ = connector_with([server.CanvasHTTPResponse(200, {}, b"not-json")])
    check("malformed_json_fails_safely", raises_code("malformed_canvas_response", malformed_connector.identity), failures)

    oversized_connector, _ = connector_with([server.CanvasHTTPResponse(200, {}, b"x" * (server.CANVAS_MAX_RESPONSE_BYTES + 1))])
    check("oversized_response_rejected", raises_code("response_too_large", oversized_connector.identity), failures)

    timeout_http = FakeCanvasHTTP([])
    timeout_connector = server.CanvasConnector(record(), http_get=timeout_http)
    check("request_timeout_fails_safely", raises_code("transport_failure", timeout_connector.identity), failures)

    paginated_connector, paginated_http = connector_with(
        [
            response(
                200,
                [{"id": "1", "name": "Page One"}],
                {"LINK": f"<{ORIGIN}/api/v1/courses?page=opaque-next>; rel=\"next\""},
            ),
            response(200, [{"id": "2", "name": "Page Two"}]),
        ]
    )
    paginated = paginated_connector.courses()
    check("pagination_follows_rel_next", [course.remote_course_id for course in paginated] == ["1", "2"], failures)
    check("link_header_capitalization_is_case_insensitive", len(paginated_http.requests) == 2, failures)
    check("pagination_url_treated_as_opaque", paginated_http.requests[1][0].endswith("page=opaque-next"), failures)

    cross_origin_connector, _ = connector_with(
        [
            response(
                200,
                [{"id": "1", "name": "Page One"}],
                {"Link": "<https://evil.example/api/v1/courses?page=2>; rel=\"next\""},
            )
        ]
    )
    check("cross_origin_next_link_rejected", raises_code("cross_origin_pagination", cross_origin_connector.courses), failures)

    loop_connector, _ = connector_with(
        [
            response(
                200,
                [{"id": "1", "name": "Page One"}],
                {"Link": f"<{ORIGIN}/api/v1/courses?enrollment_state=active&per_page=50>; rel=\"next\""},
            )
        ]
    )
    check("pagination_loop_rejected", raises_code("pagination_loop", loop_connector.courses), failures)

    limited_connector, _ = connector_with(
        [
            response(200, [{"id": "1", "name": "One"}], {"Link": f"<{ORIGIN}/api/v1/courses?page=2>; rel=\"next\""}),
            response(200, [{"id": "2", "name": "Two"}]),
        ],
        max_items=1,
    )
    check("maximum_items_enforced", raises_code("pagination_limit_exceeded", limited_connector.courses), failures)

    redirect_connector, _ = connector_with([response(302, b"", {"Location": "https://evil.example/api/v1/courses"})])
    check("cross_origin_redirect_rejected", raises_code("cross_origin_redirect", redirect_connector.identity), failures)

    same_origin_connector = server.CanvasConnector(record("https://canvas.example.edu"), http_get=FakeCanvasHTTP([response(200, {"id": "1", "name": "Ok"})]))
    check("credential_origin_cannot_be_overridden", raises_code("origin_mismatch", lambda: same_origin_connector._request_url("https://evil.example/api/v1/users/self")), failures)

    mutating_blocked = all(
        raises_code("unsupported_canvas_endpoint", lambda path=path: server.CanvasConnector(record())._build_url(path, {}))
        for path in ["/api/v1/courses/1/files", "/api/v1/users/self/files", "/api/v1/courses/1/assignments"]
    )
    check("content_download_endpoints_not_constructed", mutating_blocked, failures)
    check("canvas_mutating_methods_not_supported", "method=\"GET\"" in Path(ROOT / "server.py").read_text(encoding="utf-8") and "method=\"POST\"" not in Path(ROOT / "server.py").read_text(encoding="utf-8").split("class CanvasConnector", 1)[1].split("def header_value", 1)[0], failures)

    public_course = server.public_canvas_course(courses[0])
    public_identity = server.public_canvas_identity(identity)
    check("token_never_returned_in_public_payloads", TOKEN not in json.dumps(public_course) and TOKEN not in json.dumps(public_identity), failures)

    parsed = server.parse_canvas_connection_record(
        "studyhub-canvas-connection-v1\n"
        f"origin_hex={ORIGIN.encode().hex()}\n"
        f"token_hex={TOKEN.encode().hex()}"
    )
    check("connection_record_parses_origin_bound_token", parsed.origin == ORIGIN and parsed.access_token == TOKEN, failures)
    check("http_origin_rejected", raises_connection_code("invalid_canvas_origin", lambda: server.normalize_canvas_origin("http://canvas.example.edu")), failures)
    check("access_token_url_rejected", raises_connection_code("invalid_canvas_origin", lambda: server.normalize_canvas_origin("https://canvas.example.edu?access_token=x")), failures)

    with tempfile.TemporaryDirectory(prefix="studyhub-canvas-discovery-") as raw_tmp:
        db = Path(raw_tmp) / "studyhub.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE canary (value TEXT)")
        conn.execute("INSERT INTO canary VALUES ('synthetic')")
        conn.commit()
        conn.close()
        check("credential_never_written_to_sqlite", TOKEN.encode() not in db.read_bytes(), failures)

    check("credential_never_written_to_environment", all(TOKEN not in value for value in os.environ.values()), failures)
    check("credential_never_returned_through_mcp", all("credential" not in name.lower() and "canvas" not in name.lower() for name in server.MCP_TOOLS), failures)
    source = Path(ROOT / "server.py").read_text(encoding="utf-8")
    get_section = source.split("def handle_api_get", 1)[1].split("def handle_notes_get", 1)[0]
    check("canvas_api_routes_are_post_only", 'parsed.path == "/api/canvas/identity"' in source and "/api/canvas/identity" not in get_section and "/api/canvas/courses" not in get_section, failures)
    check("demo_test_never_touches_native_storage", "RuntimeProfile::DemoTest => CredentialStoreBackend::DemoDenied" in Path(ROOT / "src-tauri/src/credential_store.rs").read_text(encoding="utf-8"), failures)

    check("canvas_connector_does_not_persist_domain_rows", all(term not in source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0] for term in ["INSERT INTO", "import_course_folder", "register_material", "scan_library"]), failures)

    if failures:
        print("Canvas discovery acceptance failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Canvas discovery acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
