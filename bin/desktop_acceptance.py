#!/usr/bin/env python3
"""Synthetic acceptance checks for the isolated desktop prototype."""

from __future__ import annotations

import argparse
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
APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"
APP_RESOURCES = APP / "Contents" / "Resources"
PACKAGED_BACKEND = APP_RESOURCES / "backend" / "studyhub-backend"
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
    packaged: bool = False,
) -> tuple[subprocess.Popen[str], str, int]:
    if packaged:
        empty_path = runtime.parent / "empty-path"
        test_home = runtime.parent / "home"
        empty_path.mkdir(parents=True, exist_ok=True)
        test_home.mkdir(parents=True, exist_ok=True)
        env = {
            "HOME": str(test_home),
            "LANG": "en_US.UTF-8",
            "PATH": str(empty_path),
            "TMPDIR": tempfile.gettempdir(),
        }
        command = [str(PACKAGED_BACKEND), "serve", "--port", str(port)]
        static_dir = APP_RESOURCES / "static"
        demo_dir = APP_RESOURCES / "demo-data"
        katex_dir = APP_RESOURCES / "katex"
        cwd = runtime
    else:
        env = os.environ.copy()
        command = [sys.executable, str(ROOT / "server.py"), "serve", "--port", str(port)]
        static_dir = ROOT / "static"
        demo_dir = ROOT / "demo-data"
        katex_dir = ROOT / "node_modules" / "katex" / "dist"
        cwd = ROOT
    env.pop("STUDY_LIBRARY_PATH", None)
    env.update(
        {
            "DEMO_MODE": "true" if demo_mode else "false",
            "HOST": "127.0.0.1",
            "STUDYHUB_DESKTOP": "true",
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "STUDYHUB_STATIC_DIR": str(static_dir),
            "STUDYHUB_DEMO_DATA_DIR": str(demo_dir),
            "STUDYHUB_KATEX_DIR": str(katex_dir),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    if study_root is not None:
        env["STUDY_LIBRARY_PATH"] = str(study_root)
    runtime.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=cwd,
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


def get_bytes(url: str, path: str) -> tuple[int, dict[str, str], bytes]:
    port = int(url.rsplit(":", 1)[1])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Host": f"localhost:{port}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, dict(response.headers.items()), response.read()


def post_json(url: str, path: str, body: dict[str, object]) -> object:
    session = get_json(url, "/api/session")
    if not isinstance(session, dict):
        raise AssertionError("desktop session response was not an object")
    port = int(url.rsplit(":", 1)[1])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Host": f"localhost:{port}",
            "Origin": f"http://localhost:{port}",
            "X-StudyHub-CSRF": str(session.get("csrfToken") or ""),
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def health(url: str) -> dict[str, object]:
    payload = get_json(url, "/api/health")
    if not isinstance(payload, dict):
        raise AssertionError("desktop health response was not an object")
    return payload


def rescan(url: str) -> dict[str, object]:
    payload = post_json(url, "/api/scan", {})
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
    backend_builder = (ROOT / "bin" / "build_desktop_backend.py").read_text(encoding="utf-8")
    setup = (ROOT / "bin" / "setup_desktop.py").read_text(encoding="utf-8")
    permissions = set(capability["permissions"])
    return {
        "bundle_has_no_updater_artifacts": config["bundle"]["createUpdaterArtifacts"] is False,
        "bundle_targets_internal_app_only": config["bundle"]["targets"] == ["app"],
        "remote_capability_is_localhost_only": capability["remote"]["urls"] == ["http://localhost:*"],
        "folder_picker_permission_is_explicit": "allow-choose-study-folder" in permissions,
        "restart_permission_is_explicit": "allow-restart-backend" in permissions,
        "retry_permission_is_explicit": "allow-retry-backend" in permissions,
        "diagnostics_permission_is_explicit": "allow-startup-diagnostics" in permissions,
        "navigation_checks_exact_port": "candidate.port_or_known_default()" in rust
        and "allowed_port.load(Ordering::SeqCst)" in rust,
        "navigation_checks_localhost_literal": 'candidate.host_str() == Some("localhost")' in rust,
        "backend_uses_process_api_without_shell": "Command::new" in rust and "shell=True" not in rust,
        "desktop_runtime_is_outside_bundle": "app_data_dir()" in rust and "app_config_dir()" in rust,
        "native_picker_frontend_hook_present": 'desktopInvoke("choose_study_folder")' in frontend,
        "maintainer_key_not_present": "OPENAI_API_KEY=" not in rust,
        "public_bind_not_present": "0.0.0.0" not in rust,
        "release_build_remaps_private_home": "--remap-path-prefix=" in builder,
        "release_build_uses_no_shell": "subprocess.run" in builder and "shell=True" not in builder,
        "release_has_no_system_python_fallback": 'resolve("backend/studyhub-backend"' in rust
        and "cfg(not(debug_assertions))" in rust,
        "packaged_backend_uses_one_folder": '"--onedir"' in backend_builder,
        "packaged_backend_includes_certifi": '"certifi"' in backend_builder,
        "desktop_python_is_version_pinned": 'PYTHON_VERSION = "3.13.15"' in setup,
        "desktop_python_uses_uv_managed_runtime": '"python",\n            "install"' in setup,
        "server_source_not_bundled": "../server.py" not in json.dumps(config["bundle"]["resources"]),
    }


def packaged_checks(results: dict[str, bool]) -> None:
    results.update(
        {
            "packaged_app_exists": APP.is_dir(),
            "packaged_sidecar_exists": PACKAGED_BACKEND.is_file(),
            "packaged_static_assets_exist": (APP_RESOURCES / "static" / "index.html").is_file(),
            "packaged_demo_assets_exist": (APP_RESOURCES / "demo-data").is_dir(),
            "packaged_katex_assets_exist": (APP_RESOURCES / "katex" / "katex.min.js").is_file(),
            "packaged_server_source_absent": not (APP_RESOURCES / "server.py").exists(),
        }
    )
    if not all(results[name] for name in ("packaged_app_exists", "packaged_sidecar_exists")):
        return

    with tempfile.TemporaryDirectory(prefix="studyhub-packaged-acceptance-") as raw_tmp:
        temp_root = Path(raw_tmp)
        demo_runtime = temp_root / "demo-runtime"
        demo_process, demo_url, demo_port = start_backend(demo_runtime, 0, packaged=True)
        try:
            demo_health = health(demo_url)
            demo_courses = get_json(demo_url, "/api/courses")
            demo_search = get_json(demo_url, "/api/search?q=derivative")
            results.update(
                {
                    "packaged_backend_health": bool(demo_health),
                    "packaged_backend_reports_frozen": demo_health.get("packagedBackend") is True,
                    "packaged_backend_loopback": demo_url == f"http://localhost:{demo_port}",
                    "packaged_demo_mode": demo_health.get("demoMode") is True,
                    "packaged_demo_files": int(demo_health.get("filesIndexed") or 0) == 10,
                    "packaged_demo_courses": isinstance(demo_courses, list)
                    and len(demo_courses) == 3
                    and all(str(course.get("code") or "").startswith("TEST") for course in demo_courses),
                    "packaged_demo_search": isinstance(demo_search, list)
                    and any("Derivatives" in str(item.get("filename") or "") for item in demo_search),
                    "packaged_verified_https_bundle": demo_health.get("verifiedHttps") == "Available",
                    "packaged_openai_optional": demo_health.get("openAI") == "Not configured",
                    "packaged_missing_poppler_is_nonfatal": demo_health.get("pdfTextExtraction") == "Unavailable",
                    "packaged_missing_libreoffice_is_nonfatal": demo_health.get("officeVisualPreview") == "Unavailable",
                    "packaged_runtime_database_writable": (demo_runtime / "data" / "studyhub.sqlite").is_file(),
                }
            )
        finally:
            stop_backend(demo_process)
        results["packaged_clean_shutdown"] = demo_process.poll() == 0

        synthetic_library = temp_root / "Synthetic StudyLibrary"
        shutil.copytree(APP_RESOURCES / "demo-data", synthetic_library)
        custom_runtime = temp_root / "custom-runtime"
        custom_process, custom_url, _ = start_backend(
            custom_runtime,
            0,
            demo_mode=False,
            study_root=synthetic_library,
            packaged=True,
        )
        note_text = "Synthetic packaged desktop persistence note."
        note_file_id = 0
        try:
            custom_health = health(custom_url)
            courses = get_json(custom_url, "/api/courses")
            search = get_json(custom_url, "/api/search?q=derivative")
            matching = [item for item in search if isinstance(item, dict) and "Derivatives" in str(item.get("filename") or "")]
            note_file_id = int(matching[0]["id"]) if matching else 0
            preview_status, preview_headers, preview_body = get_bytes(custom_url, f"/preview/{note_file_id}")
            normalized_preview_headers = {key.lower(): value for key, value in preview_headers.items()}
            note_result = post_json(
                custom_url,
                "/api/notes",
                {"targetType": "file", "targetId": note_file_id, "body": note_text},
            )
            results.update(
                {
                    "packaged_custom_library_mode": custom_health.get("demoMode") is False,
                    "packaged_custom_library_indexed": int(custom_health.get("filesIndexed") or 0) == 10,
                    "packaged_custom_courses": isinstance(courses, list)
                    and len(courses) == 3
                    and all(str(course.get("code") or "").startswith("TEST") for course in courses),
                    "packaged_custom_search": bool(matching),
                    "packaged_custom_rescan": rescan(custom_url).get("ok") is True,
                    "packaged_text_preview": preview_status == 200
                    and normalized_preview_headers.get("x-studyhub-preview-mode") == "text"
                    and b"derivative" in preview_body.lower(),
                    "packaged_note_created": isinstance(note_result, dict) and note_result.get("ok") is True,
                }
            )
        finally:
            stop_backend(custom_process)

        reopened_process, reopened_url, _ = start_backend(
            custom_runtime,
            0,
            demo_mode=False,
            study_root=synthetic_library,
            packaged=True,
        )
        try:
            notes = get_json(reopened_url, f"/api/notes?targetType=file&targetId={note_file_id}")
            results["packaged_note_persistence"] = isinstance(notes, list) and any(
                item.get("body") == note_text for item in notes if isinstance(item, dict)
            )
        finally:
            stop_backend(reopened_process)
        results["packaged_reopen_shutdown"] = reopened_process.poll() == 0

        listeners: list[socket.socket] = []
        try:
            for port in (8765, 8766, 8767):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
                listener.listen(1)
                listeners.append(listener)
            collision_process, collision_url, collision_port = start_backend(
                temp_root / "collision-runtime", 8765, packaged=True
            )
            try:
                results["packaged_multiple_port_collision"] = collision_port >= 8768 and bool(health(collision_url))
            finally:
                stop_backend(collision_process)
        finally:
            for listener in listeners:
                listener.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packaged", action="store_true")
    args = parser.parse_args()
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

    if args.packaged:
        packaged_checks(results)

    failed = [name for name, passed in results.items() if not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Desktop acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Packaged desktop acceptance: PASS" if args.packaged else "Desktop prototype acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
