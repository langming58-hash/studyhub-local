#!/usr/bin/env python3
"""Synthetic acceptance checks for the isolated desktop prototype."""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
URL_RE = re.compile(r"^StudyHub Local running at (http://localhost:(\d+))$")


def output_lines(stream: object, results: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        results.put(line.rstrip("\n"))


def start_backend(
    runtime: Path,
    port: int,
    *,
    demo_mode: bool = True,
    study_root: Path | None = None,
) -> tuple[subprocess.Popen[str], str, int]:
    env = os.environ.copy()
    env.pop("STUDY_LIBRARY_PATH", None)
    env.update(
        {
            "DEMO_MODE": "true" if demo_mode else "false",
            "HOST": "127.0.0.1",
            "STUDYHUB_DESKTOP": "true",
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "STUDYHUB_STATIC_DIR": str(ROOT / "static"),
            "STUDYHUB_DEMO_DATA_DIR": str(ROOT / "demo-data"),
            "STUDYHUB_KATEX_DIR": str(ROOT / "node_modules" / "katex" / "dist"),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    if study_root is not None:
        env["STUDY_LIBRARY_PATH"] = str(study_root)
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "serve", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=output_lines, args=(process.stdout, lines), daemon=True).start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise AssertionError(f"desktop backend exited during startup: {stderr[:500]}")
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        match = URL_RE.match(line)
        if match:
            return process, match.group(1), int(match.group(2))
    process.kill()
    process.wait(timeout=5)
    raise AssertionError("desktop backend did not report its assigned localhost port")


def get_json(url: str, path: str) -> object:
    port = int(url.rsplit(":", 1)[1])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Host": f"localhost:{port}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def health(url: str) -> dict[str, object]:
    payload = get_json(url, "/api/health")
    if not isinstance(payload, dict):
        raise AssertionError("desktop health response was not an object")
    return payload


def rescan(url: str) -> dict[str, object]:
    session = get_json(url, "/api/session")
    if not isinstance(session, dict):
        raise AssertionError("desktop session response was not an object")
    port = int(url.rsplit(":", 1)[1])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/scan",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Host": f"localhost:{port}",
            "Origin": f"http://localhost:{port}",
            "X-StudyHub-CSRF": str(session.get("csrfToken") or ""),
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise AssertionError("desktop rescan response was not an object")
    return payload


def stop_backend(process: subprocess.Popen[str]) -> None:
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=5)
    if process.returncode != 0:
        raise AssertionError(f"desktop backend did not stop cleanly: {process.returncode}")


def source_checks() -> dict[str, bool]:
    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    capability = json.loads((ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8"))
    rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    frontend = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    builder = (ROOT / "bin" / "build_desktop.py").read_text(encoding="utf-8")
    permissions = set(capability["permissions"])
    return {
        "bundle_has_no_updater_artifacts": config["bundle"]["createUpdaterArtifacts"] is False,
        "bundle_targets_internal_app_only": config["bundle"]["targets"] == ["app"],
        "remote_capability_is_localhost_only": capability["remote"]["urls"] == ["http://localhost:*"],
        "folder_picker_permission_is_explicit": "allow-choose-study-folder" in permissions,
        "restart_permission_is_explicit": "allow-restart-backend" in permissions,
        "navigation_checks_exact_port": "candidate.port_or_known_default() == Some(allowed_port)" in rust,
        "navigation_checks_localhost_literal": 'candidate.host_str() == Some("localhost")' in rust,
        "backend_uses_process_api_without_shell": "Command::new" in rust and "shell=True" not in rust,
        "desktop_runtime_is_outside_bundle": "app_data_dir()" in rust and "app_config_dir()" in rust,
        "native_picker_frontend_hook_present": 'desktopInvoke("choose_study_folder")' in frontend,
        "maintainer_key_not_present": "OPENAI_API_KEY=" not in rust,
        "public_bind_not_present": "0.0.0.0" not in rust,
        "release_build_remaps_private_home": "--remap-path-prefix=" in builder,
        "release_build_uses_no_shell": "subprocess.run" in builder and "shell=True" not in builder,
    }


def main() -> int:
    results = source_checks()
    with tempfile.TemporaryDirectory(prefix="studyhub-desktop-acceptance-") as raw_tmp:
        temp_root = Path(raw_tmp)
        runtime = temp_root / "Application Support" / "StudyHub Local"

        dynamic_process, dynamic_url, dynamic_port = start_backend(runtime, 0)
        try:
            dynamic_health = health(dynamic_url)
            results.update(
                {
                    "dynamic_loopback_port": dynamic_port > 0,
                    "desktop_health_mode": dynamic_health.get("desktopMode") is True,
                    "demo_mode_zero_config": dynamic_health.get("demoMode") is True,
                    "demo_files_scanned": int(dynamic_health.get("filesIndexed") or 0) > 0,
                    "runtime_database_outside_bundle": (runtime / "data" / "studyhub.sqlite").exists(),
                }
            )
        finally:
            stop_backend(dynamic_process)
        results["clean_sigterm_shutdown"] = dynamic_process.poll() == 0

        synthetic_library = temp_root / "Synthetic StudyLibrary"
        shutil.copytree(ROOT / "demo-data", synthetic_library)
        custom_process, custom_url, _ = start_backend(
            temp_root / "custom-runtime",
            0,
            demo_mode=False,
            study_root=synthetic_library,
        )
        try:
            custom_health = health(custom_url)
            courses = get_json(custom_url, "/api/courses")
            search = get_json(custom_url, "/api/search?q=derivative")
            scan_result = rescan(custom_url)
            results.update(
                {
                    "synthetic_custom_library_mode": custom_health.get("demoMode") is False,
                    "synthetic_custom_library_indexed": int(custom_health.get("filesIndexed") or 0) == 10,
                    "synthetic_courses_discovered": isinstance(courses, list)
                    and len(courses) == 3
                    and all(str(course.get("code") or "").startswith("TEST") for course in courses),
                    "synthetic_library_search": isinstance(search, list)
                    and any("Derivatives" in str(item.get("filename") or "") for item in search),
                    "synthetic_library_rescan": scan_result.get("ok") is True,
                }
            )
        finally:
            stop_backend(custom_process)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            busy_port = int(occupied.getsockname()[1])
            fallback_process, fallback_url, fallback_port = start_backend(temp_root / "fallback-runtime", busy_port)
            try:
                results["occupied_port_fallback"] = fallback_port != busy_port and bool(health(fallback_url))
            finally:
                stop_backend(fallback_process)

    failed = [name for name, passed in results.items() if not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Desktop acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Desktop prototype acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
