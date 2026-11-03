#!/usr/bin/env python3
"""Production Canvas OAuth connection acceptance for PR #37."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server


CANARIES = [
    "SYN_CANVAS_ACCESS_TOKEN_x9F2",
    "SYN_CANVAS_REFRESH_TOKEN_x9F2",
    "SYN_CANVAS_CLIENT_SECRET_x9F2",
    "SYN_CANVAS_AUTH_CODE_x9F2",
]


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    production = read("src-tauri/capabilities/studyhub-production.json")
    development = read("src-tauri/capabilities/studyhub-development.json")
    demo = read("src-tauri/capabilities/studyhub-demo.json")
    check(
        "production_has_oauth_commands_not_pat_or_provisioning",
        "allow-start-canvas-oauth" in production
        and "allow-complete-canvas-oauth" in production
        and "allow-disconnect-canvas" in production
        and "allow-configure-canvas-development-connection" not in production
        and "allow-provision-canvas-oauth-client" not in production,
        failures,
    )
    check(
        "development_keeps_pat_and_provisioning",
        "allow-configure-canvas-development-connection" in development
        and "allow-provision-canvas-oauth-client" in development,
        failures,
    )
    check(
        "demo_has_no_canvas_credential_access",
        "allow-start-canvas-oauth" not in demo and "allow-disconnect-canvas" not in demo,
        failures,
    )

    rust_lib = read("src-tauri/src/lib.rs")
    check(
        "production_pat_fails_closed_natively",
        "canvas_manual_token_development_only" in rust_lib
        and "profile != RuntimeProfile::Development" in rust_lib,
        failures,
    )

    source = read("server.py")
    connector = source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0]
    check(
        "connector_is_read_only",
        "self.http_get" in connector
        and "method=\"POST\"" not in connector
        and "method=\"PUT\"" not in connector
        and "method=\"DELETE\"" not in connector
        and "/assignments" not in connector,
        failures,
    )
    check(
        "canvas_endpoints_remain_post_only",
        'parsed.path == "/api/canvas/identity"' in source
        and "/api/canvas/identity" not in source.split("def handle_api_get", 1)[1].split("def handle_notes_get", 1)[0],
        failures,
    )

    app_js = read("static/app.js")
    check(
        "canvas_settings_section_renders_once",
        'id="canvasSection"' in app_js and "canvasSectionHtml" in app_js,
        failures,
    )
    check(
        "canvas_status_is_live_region",
        'id="canvasStatus"' in app_js and 'aria-live="polite"' in app_js,
        failures,
    )
    for action in (
        "connect",
        "cancel",
        "complete",
        "disconnect",
        "refresh-courses",
        "preview-selection",
        "apply-selection",
        "sync-preview",
        "sync-apply",
    ):
        check(f"canvas_action_wired_{action}", f'data-canvas-{action}="1"' in app_js, failures)

    i18n_result = subprocess.run(
        [
            "node",
            "-e",
            "global.window={};require('./static/i18n/catalog.js');"
            "console.log(JSON.stringify(window.STUDYHUB_TRANSLATIONS));",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    translations = json.loads(i18n_result.stdout) if i18n_result.returncode == 0 else {}
    for key in (
        "canvas.heading",
        "canvas.unavailable",
        "canvas.connect",
        "canvas.disconnect",
        "canvas.refreshCourses",
        "canvas.syncNow",
        "canvas.reconnectRequired",
    ):
        check(
            f"canvas_i18n_{key.split('.')[1]}",
            key in translations.get("en", {}) and key in translations.get("zh-CN", {}),
            failures,
        )

    for target in ("static/app.js", "static/i18n/catalog.js", "server.py", "reader_extractors.py"):
        text = read(target)
        check(
            f"no_secret_canaries_{target.replace('/', '_')}",
            all(canary not in text for canary in CANARIES),
            failures,
        )

    record = server.parse_canvas_connection_record(
        "studyhub-canvas-connection-v1\n"
        "origin_hex=68747470733a2f2f63616e7661732e6578616d706c652e656475\n"
        "token_hex=73796e746f6b656e"
    )
    check(
        "v1_backend_record_parsing_preserved",
        record.origin == "https://canvas.example.edu" and record.access_token == "syntoken",
        failures,
    )

    check(
        "backend_error_mapping_distinguishes_failures",
        all(
            code in source
            for code in (
                '"authentication_failed": 401',
                '"permission_denied": 403',
                '"rate_limited": 429',
                '"canvas_unavailable": 503',
                '"transport_failure": 503',
                '"credential_missing": 409',
            )
        ),
        failures,
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
