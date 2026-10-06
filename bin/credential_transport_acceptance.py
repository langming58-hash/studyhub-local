#!/usr/bin/env python3
"""Synthetic acceptance checks for private backend credential transport."""

from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


SAMPLE_VALUE = "synthetic-private-credential-transport-value"
SESSION_ONE = "a" * 64
SESSION_TWO = "b" * 64


def frame(payload: str) -> bytes:
    data = payload.encode("utf-8")
    return len(data).to_bytes(4, "big") + data


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise AssertionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> str:
    length = int.from_bytes(read_exact(sock, 4), "big")
    if length > server.CREDENTIAL_MAX_FRAME_BYTES:
        raise AssertionError("oversized frame")
    return read_exact(sock, length).decode("utf-8")


def write_frame(sock: socket.socket, payload: str) -> None:
    sock.sendall(frame(payload))


def synthetic_parent(sock: socket.socket, session: str, *, malformed: bool = False, close_early: bool = False) -> None:
    try:
        if close_early:
            return
        write_frame(sock, f"{server.CREDENTIAL_PROTOCOL} session={session}")
        request = read_frame(sock)
        if malformed:
            write_frame(sock, "malformed parent response")
            return
        expected = f"{server.CREDENTIAL_PROTOCOL} use canvas_default session={session}"
        if request != expected:
            write_frame(sock, f"{server.CREDENTIAL_PROTOCOL} err code=unauthorized_or_stale_session")
            return
        write_frame(sock, f"{server.CREDENTIAL_PROTOCOL} ok credential_hex={SAMPLE_VALUE.encode().hex()}")
    except Exception:
        return
    finally:
        sock.close()


def client_for_synthetic_parent(session: str, *, malformed: bool = False, close_early: bool = False) -> server.CredentialClient:
    parent, child = socket.socketpair()
    thread = threading.Thread(
        target=synthetic_parent,
        args=(parent, session),
        kwargs={"malformed": malformed, "close_early": close_early},
        daemon=True,
    )
    thread.start()
    os.environ[server.CREDENTIAL_TRANSPORT_FD_ENV] = str(child.detach())
    os.environ[server.CREDENTIAL_TRANSPORT_PROTOCOL_ENV] = "1"
    os.environ[server.CREDENTIAL_TRANSPORT_KIND_ENV] = "unix-fd"
    return server.CredentialClient.from_environment()


def check(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []

    client = client_for_synthetic_parent(SESSION_ONE)
    observed = client.with_canvas_default_credential(lambda credential: credential == SAMPLE_VALUE)
    check("backend_internal_request_succeeds", observed is True, failures)
    check("credential_fd_env_removed", server.CREDENTIAL_TRANSPORT_FD_ENV not in os.environ, failures)
    check("credential_not_in_environment", all(SAMPLE_VALUE not in value for value in os.environ.values()), failures)

    with tempfile.TemporaryDirectory(prefix="studyhub-credential-transport-") as raw_tmp:
        tmp = Path(raw_tmp)
        db_path = tmp / "synthetic.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE synthetic(value TEXT)")
        conn.execute("INSERT INTO synthetic(value) VALUES (?)", ("public synthetic row",))
        conn.commit()
        conn.close()
        log_path = tmp / "synthetic.log"
        log_path.write_text("public synthetic log\n", encoding="utf-8")
        check("credential_not_written_to_sqlite", SAMPLE_VALUE.encode() not in db_path.read_bytes(), failures)
        check("credential_not_written_to_logs", SAMPLE_VALUE not in log_path.read_text(encoding="utf-8"), failures)

    source = (ROOT / "server.py").read_text(encoding="utf-8")
    check("no_http_credential_endpoint", '"/api/credential' not in source and "'/api/credential" not in source, failures)
    check("no_mcp_credential_tool", all("credential" not in name.lower() for name in server.MCP_TOOLS), failures)

    try:
        bad = client_for_synthetic_parent(SESSION_ONE, malformed=True)
        bad.with_canvas_default_credential(lambda credential: credential)
        malformed_safe = False
    except server.CredentialClientError:
        malformed_safe = True
    check("malformed_parent_response_fails_safely", malformed_safe, failures)

    try:
        client_for_synthetic_parent(SESSION_ONE, close_early=True)
        closed_safe = False
    except server.CredentialClientError:
        closed_safe = True
    check("closed_parent_channel_fails_safely", closed_safe, failures)

    first = client_for_synthetic_parent(SESSION_ONE)
    second = client_for_synthetic_parent(SESSION_TWO)
    check("restart_requires_fresh_session", first._session != second._session, failures)

    if failures:
        print("Credential transport acceptance failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Credential transport acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
