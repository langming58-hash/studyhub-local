#!/usr/bin/env python3
"""Acceptance checks for the native-only CredentialStore foundation."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def contains_raw_secret_command(rust: str) -> bool:
    handler = re.search(r"generate_handler!\s*\[(?P<body>[^\]]*)\]", rust, re.S)
    if not handler:
        return True
    commands = handler.group("body").lower()
    forbidden = ("credential", "secret", "token", "password", "keychain")
    return any(term in commands for term in forbidden)


def main() -> int:
    lib = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    credential_store = (ROOT / "src-tauri" / "src" / "credential_store.rs").read_text(encoding="utf-8")
    credential_handoff_path = ROOT / "src-tauri" / "src" / "credential_handoff.rs"
    credential_handoff = credential_handoff_path.read_text(encoding="utf-8")
    credential_transport_path = ROOT / "src-tauri" / "src" / "credential_transport.rs"
    credential_transport = credential_transport_path.read_text(encoding="utf-8")
    capability = json.loads((ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8"))
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    desktop_arch = (ROOT / "docs" / "DESKTOP_ARCHITECTURE.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    public_route_terms = (
        "credential",
        "secret",
        "keychain",
    )
    public_api_leak = any(f'"/api/{term}' in server or f"'/api/{term}" in server for term in public_route_terms)
    mcp_section = re.search(r"MCP_TOOLS\s*:\s*dict\[.*?(?=\ndef mcp_tool_descriptors)", server, re.S)
    mcp_text = mcp_section.group(0) if mcp_section else ""
    mcp_leak = any(f'"{term}_' in mcp_text or f"'{term}_" in mcp_text for term in public_route_terms)
    capability_text = json.dumps(capability).lower()

    results = {
        "keyring_dependency_declared": 'keyring = "4.2.0"' in cargo,
        "native_keyring_adapter_present": "keyring::Entry::new" in credential_store,
        "credential_store_contract_present": all(
            token in credential_store
            for token in (
                "store_replace",
                "exists",
                "get_for_trusted_native_use",
                "delete",
                "CredentialAvailability",
                "CredentialStoreFailure",
            )
        ),
        "profile_namespaces_distinct": all(
            token in credential_store
            for token in (
                "io.studyhublocal.desktop.credentials",
                "io.studyhublocal.desktop.dev.credentials",
                "io.studyhublocal.desktop.demo.credentials",
            )
        ),
        "demo_test_denies_native_storage": "CredentialStoreBackend::DemoDenied" in credential_store
        and "RuntimeProfile::DemoTest => CredentialStoreBackend::DemoDenied" in credential_store,
        "no_raw_secret_tauri_command": not contains_raw_secret_command(lib),
        "no_credential_capability": all(
            term not in capability_text for term in ("credential", "secret", "keychain", "password", "token")
        ),
        "no_public_http_credential_api": not public_api_leak,
        "no_mcp_credential_tool": not mcp_leak,
        "openai_legacy_env_still_present": "OPENAI_API_KEY=" in env_example
        and "os.environ.get(\"OPENAI_API_KEY\")" in server,
        "openai_not_migrated_to_keyring": "OPENAI_API_KEY" not in credential_store
        and "OPENAI_VECTOR_STORE_ID" not in credential_store,
        "docs_legacy_openai_boundary": "does not automatically protect or migrate" in desktop_arch
        and "existing OpenAI configuration" in desktop_arch,
        "docs_trust_boundary": all(
            term in privacy for term in ("No frontend", "localhost HTTP", "MCP", "browser", "SQLite")
        ),
        "handoff_module_present": credential_handoff_path.exists()
        and "CredentialHandoffBroker" in credential_handoff
        and "BackendChildAuthorization" in credential_handoff,
        "handoff_uses_typed_canvas_slot_only": "HandoffRequest::UseCanvasDefault" in credential_handoff
        and "CredentialSlot::CanvasDefault" in credential_handoff
        and "UnsupportedSlot" in credential_handoff,
        "handoff_rejects_stale_and_unauthorized_children": all(
            token in credential_handoff
            for token in (
                "UnauthorizedChild",
                "StaleAuthorization",
                "restart_backend_child",
                "verify_authorization",
            )
        ),
        "handoff_errors_are_sanitized": "impl fmt::Debug for HandoffError" in credential_handoff
        and "credential_handoff_operation_failed" in credential_handoff
        and "SecretValue(<redacted>)" in credential_store,
        "handoff_not_registered_as_tauri_command": "credential_handoff" not in re.search(
            r"generate_handler!\s*\[(?P<body>[^\]]*)\]", lib, re.S
        ).group("body").lower(),
        "development_keychain_smoke_is_opt_in": "development_keychain_smoke_test_opt_in" in credential_store
        and '#[ignore = "development-only opt-in smoke test; touches the Development Keychain namespace"]'
        in credential_store
        and "STUDYHUB_RUN_DEV_KEYCHAIN_SMOKE" in credential_store
        and "RuntimeProfile::Development" in credential_store,
        "live_transport_module_present": credential_transport_path.exists()
        and "UnixStream::pair" in credential_transport
        and "STUDYHUB_CREDENTIAL_TRANSPORT_FD" in credential_transport,
        "live_transport_uses_random_session_authority": "new_os_random" in credential_transport
        and "/dev/urandom" in credential_transport
        and "LiveSessionAuthority(<redacted>)" in credential_transport,
        "live_transport_has_strict_frames": "MAX_FRAME_BYTES" in credential_transport
        and "OversizedFrame" in credential_transport
        and "TruncatedFrame" in credential_transport,
        "live_transport_not_registered_as_tauri_command": "credential_transport" not in re.search(
            r"generate_handler!\s*\[(?P<body>[^\]]*)\]", lib, re.S
        ).group("body").lower(),
        "launch_env_has_no_secret_values": '".env("CANVAS' not in lib
        and '".env("ACCESS_TOKEN' not in lib
        and "TOKEN" not in lib
        and "OPENAI_API_KEY" in lib
        and "env_remove(inherited)" in lib,
        "python_internal_client_present": "class CredentialClient" in server
        and "with_canvas_default_credential" in server
        and "CredentialClient.from_environment" in server,
        "python_client_not_http_exposed": "/api/credentials" not in server
        and "/api/keychain" not in server
        and "/api/canvas-token" not in server,
    }

    failed = [name for name, passed in results.items() if not passed]
    for name, passed in results.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    if failed:
        print("CredentialStore acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("CredentialStore acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
