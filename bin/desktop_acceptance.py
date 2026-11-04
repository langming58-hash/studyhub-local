#!/usr/bin/env python3
"""Synthetic acceptance checks for the isolated StudyHub desktop app."""

from __future__ import annotations

import argparse
import hashlib
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
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"
APP_RESOURCES = APP / "Contents" / "Resources"
PACKAGED_BACKEND = APP_RESOURCES / "backend" / "studyhub-backend"
PACKAGED_MARKITDOWN = APP_RESOURCES / "reader-tools" / "studyhub-markitdown"
PACKAGED_MAC_OCR = APP_RESOURCES / "reader-tools" / "mac-ocr"
URL_RE = re.compile(r"^StudyHub Local running at (http://localhost:(\d+))$")
OCR_FIXTURE_TEXT = "STUDYHUB OCR 12345"
OCR_FIXTURE_TOKEN = "12345"


def write_synthetic_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic packaged MarkItDown smoke</w:t></w:r></w:p></w:body></w:document>',
        )


def write_synthetic_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(bytes(body))


def write_synthetic_ocr_fixture(path: Path) -> None:
    """Write a high-contrast, deterministic image for the packaged mac-ocr smoke."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=48)
    probe = Image.new("L", (8, 8), 255)
    left, top, right, bottom = ImageDraw.Draw(probe).textbbox(
        (0, 0), OCR_FIXTURE_TEXT, font=font, spacing=24
    )
    padding = 48
    width = (right - left) + 2 * padding
    height = (bottom - top) + 2 * padding
    logical = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(logical)
    draw.text((padding - left, padding - top), OCR_FIXTURE_TEXT, font=font, fill=0, spacing=24)
    scaled = logical.resize((width * 4, height * 4), Image.NEAREST)
    scaled.save(path)


def output_lines(stream: object, results: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        results.put(line.rstrip("\n"))


def start_backend(
    runtime: Path,
    port: int,
    *,
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
        katex_dir = APP_RESOURCES / "katex"
        cwd = runtime
    else:
        env = os.environ.copy()
        command = [sys.executable, str(ROOT / "server.py"), "serve", "--port", str(port)]
        static_dir = ROOT / "static"
        katex_dir = ROOT / "node_modules" / "katex" / "dist"
        cwd = ROOT
    env.pop("STUDY_LIBRARY_PATH", None)
    env.pop("STUDYHUB_MAC_OCR", None)
    env.pop("STUDYHUB_MARKITDOWN_HELPER", None)
    env.update(
        {
            "HOST": "127.0.0.1",
            "STUDYHUB_DESKTOP": "true",
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "STUDYHUB_STATIC_DIR": str(static_dir),
            "STUDYHUB_KATEX_DIR": str(katex_dir),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    if study_root is not None:
        env["STUDY_LIBRARY_PATH"] = str(study_root)
    if packaged:
        env["STUDYHUB_MAC_OCR"] = str(PACKAGED_MAC_OCR)
        env["STUDYHUB_MARKITDOWN_HELPER"] = str(PACKAGED_MARKITDOWN)
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
    deadline = time.monotonic() + 60
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
    stderr = process.stderr.read() if process.stderr else ""
    detail = f": {stderr[:500]}" if stderr.strip() else ""
    raise AssertionError(f"desktop backend did not report its assigned localhost port{detail}")


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
    capability = json.loads(
        (ROOT / "src-tauri" / "capabilities" / "studyhub-production.json").read_text(encoding="utf-8")
    )
    rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    frontend = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    builder = (ROOT / "bin" / "build_desktop.py").read_text(encoding="utf-8")
    backend_builder = (ROOT / "bin" / "build_desktop_backend.py").read_text(encoding="utf-8")
    setup = (ROOT / "bin" / "setup_desktop.py").read_text(encoding="utf-8")
    desktop_check = (ROOT / "bin" / "check_desktop.py").read_text(encoding="utf-8")
    permissions = {permission for permission in capability["permissions"] if isinstance(permission, str)}
    return {
        "bundle_has_no_updater_artifacts": config["bundle"]["createUpdaterArtifacts"] is False,
        "bundle_targets_public_app_and_dmg": config["bundle"]["targets"] == ["app", "dmg"],
        "bundle_has_macos_icon": "icons/icon.icns" in config["bundle"].get("icon", []),
        "bundle_uses_ad_hoc_signature_only": config["bundle"]["macOS"].get("signingIdentity") == "-",
        "remote_capability_is_localhost_only": capability["remote"]["urls"] == ["http://localhost:*"],
        "folder_picker_permission_is_explicit": "allow-choose-study-folder" in permissions,
        "public_url_permission_is_scoped": any(
            isinstance(permission, dict)
            and permission.get("identifier") == "opener:allow-open-url"
            and all(
                str(item.get("url") or "").startswith("https://github.com/langming58-hash/studyhub-local")
                for item in permission.get("allow", [])
            )
            for permission in capability["permissions"]
        ),
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
        "release_build_uses_project_rust_toolchain": 'ROOT / ".tools" / "cargo"' in builder
        and 'ROOT / ".tools" / "rustup"' in builder,
        "clean_clone_check_prepares_resource_root": 'backend_root / "studyhub-backend"' in desktop_check
        and 'backend_root / "studyhub-markitdown"' in desktop_check,
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
            "packaged_test_fixtures_absent": not (APP_RESOURCES / "tests").exists()
            and not (APP_RESOURCES / "demo-data").exists(),
            "packaged_katex_assets_exist": (APP_RESOURCES / "katex" / "katex.min.js").is_file(),
            "packaged_server_source_absent": not (APP_RESOURCES / "server.py").exists(),
            "packaged_backend_placeholder_absent": not (PACKAGED_BACKEND / ".gitkeep").exists(),
            "packaged_markitdown_resource_executable": PACKAGED_MARKITDOWN.is_file()
            and os.access(PACKAGED_MARKITDOWN, os.X_OK),
            "packaged_mac_ocr_resource_executable": PACKAGED_MAC_OCR.is_file()
            and os.access(PACKAGED_MAC_OCR, os.X_OK),
        }
    )
    if not all(results[name] for name in ("packaged_app_exists", "packaged_sidecar_exists")):
        return

    with tempfile.TemporaryDirectory(prefix="studyhub-packaged-acceptance-") as raw_tmp:
        temp_root = Path(raw_tmp)
        smoke_docx = temp_root / "synthetic-smoke.docx"
        write_synthetic_docx(smoke_docx)
        smoke_docx_hash = hashlib.sha256(smoke_docx.read_bytes()).hexdigest()
        try:
            helper = subprocess.run(
                [str(PACKAGED_MARKITDOWN)],
                input=json.dumps({"operation": "convert", "path": str(smoke_docx)}) + "\n",
                text=True,
                capture_output=True,
                timeout=90,
                check=False,
                cwd=temp_root,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(temp_root)},
            )
            helper_payload = json.loads(helper.stdout.splitlines()[0]) if helper.stdout.splitlines() else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            helper = None
            helper_payload = {}
        if not isinstance(helper_payload, dict):
            helper_payload = {}
        results["packaged_markitdown_real_smoke"] = bool(
            helper
            and helper.returncode == 0
            and helper_payload.get("ok") is True
            and "Synthetic packaged MarkItDown smoke" in str(helper_payload.get("text") or "")
        )
        results["packaged_markitdown_smoke_source_unchanged"] = (
            smoke_docx_hash == hashlib.sha256(smoke_docx.read_bytes()).hexdigest()
        )
        smoke_pdf = temp_root / "synthetic-smoke.pdf"
        write_synthetic_pdf(smoke_pdf, "Synthetic packaged PDF page")
        smoke_pdf_hash = hashlib.sha256(smoke_pdf.read_bytes()).hexdigest()
        try:
            pdf_helper = subprocess.run(
                [str(PACKAGED_MARKITDOWN)],
                input=json.dumps({"operation": "pdf_pages", "path": str(smoke_pdf)}) + "\n",
                text=True,
                capture_output=True,
                timeout=90,
                check=False,
                cwd=temp_root,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(temp_root)},
            )
            pdf_payload = json.loads(pdf_helper.stdout.splitlines()[0]) if pdf_helper.stdout.splitlines() else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pdf_helper = None
            pdf_payload = {}
        pdf_pages = pdf_payload.get("pages", []) if isinstance(pdf_payload, dict) else []
        results["packaged_pdf_pages_real_smoke"] = bool(
            pdf_helper
            and pdf_helper.returncode == 0
            and pdf_payload.get("ok") is True
            and len(pdf_pages) == 1
            and pdf_pages[0].get("page") == 1
            and "Synthetic packaged PDF page" in str(pdf_pages[0].get("text") or "")
        )
        results["packaged_pdf_smoke_source_unchanged"] = (
            smoke_pdf_hash == hashlib.sha256(smoke_pdf.read_bytes()).hexdigest()
        )

        ocr_fixture = temp_root / "synthetic-ocr.png"
        write_synthetic_ocr_fixture(ocr_fixture)
        ocr_fixture_hash = hashlib.sha256(ocr_fixture.read_bytes()).hexdigest()
        if sys.platform == "darwin":
            try:
                ocr = subprocess.run(
                    [str(PACKAGED_MAC_OCR), str(ocr_fixture), "--format", "jsonl", "--pdf-dpi", "180"],
                    text=True,
                    capture_output=True,
                    timeout=90,
                    check=False,
                    cwd=temp_root,
                    env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(temp_root)},
                )
                ocr_returncode = ocr.returncode
                ocr_stdout = ocr.stdout or ""
                ocr_stderr = ocr.stderr or ""
                ocr_items = [json.loads(line) for line in ocr_stdout.splitlines() if line.strip()]
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                ocr_returncode = None
                ocr_stdout = ""
                ocr_stderr = ""
                ocr_items = []
            recognized_parts: list[str] = []
            for item in ocr_items:
                if not isinstance(item, dict):
                    continue
                recognized_parts.append(str(item.get("text") or ""))
                for observation in item.get("observations") or []:
                    if isinstance(observation, dict):
                        recognized_parts.append(str(observation.get("text") or ""))
            recognized_text = "\n".join(part for part in recognized_parts if part)
            passed = bool(
                ocr_returncode == 0
                and ocr_items
                and any(isinstance(item, dict) and item.get("page") == 1 for item in ocr_items)
                and recognized_text
                and OCR_FIXTURE_TOKEN in recognized_text
            )
            if not passed:
                safe_stderr = ocr_stderr.replace(str(temp_root), "<tmp>")
                print(
                    "mac-ocr packaged smoke diagnostics: "
                    f"returncode={ocr_returncode} "
                    f"recognized_text={recognized_text[:200]!r} "
                    f"stderr={safe_stderr[:200]!r}",
                    file=sys.stderr,
                )
            results["packaged_mac_ocr_real_smoke"] = passed
        else:
            results["packaged_mac_ocr_real_smoke"] = True
        results["packaged_mac_ocr_smoke_source_unchanged"] = (
            ocr_fixture_hash == hashlib.sha256(ocr_fixture.read_bytes()).hexdigest()
        )

        clean_runtime = temp_root / "clean-runtime"
        clean_process, clean_url, clean_port = start_backend(clean_runtime, 0, packaged=True)
        try:
            clean_health = health(clean_url)
            clean_courses = get_json(clean_url, "/api/courses")
            clean_search = get_json(clean_url, "/api/search?q=derivative")
            results.update(
                {
                    "packaged_backend_health": bool(clean_health),
                    "packaged_backend_reports_frozen": clean_health.get("packagedBackend") is True,
                    "packaged_backend_loopback": clean_url == f"http://localhost:{clean_port}",
                    "packaged_clean_first_run_has_no_demo_contract": "demoMode" not in clean_health,
                    "packaged_clean_first_run_has_zero_files": int(clean_health.get("filesIndexed") or 0) == 0,
                    "packaged_clean_first_run_has_zero_courses": clean_courses == [],
                    "packaged_clean_first_run_search_empty": clean_search == [],
                    "packaged_verified_https_bundle": clean_health.get("verifiedHttps") == "Available",
                    "packaged_openai_optional": clean_health.get("openAI") == "Not configured",
                    "packaged_pdf_fallback_available_without_poppler": clean_health.get("pdfTextExtraction") == "Available",
                    "packaged_missing_libreoffice_is_nonfatal": clean_health.get("officeVisualPreview") == "Unavailable",
                    "packaged_runtime_database_writable": (clean_runtime / "data" / "studyhub.sqlite").is_file(),
                }
            )
        finally:
            stop_backend(clean_process)
        results["packaged_clean_shutdown"] = clean_process.poll() == 0

        synthetic_library = temp_root / "Synthetic StudyLibrary"
        shutil.copytree(ROOT / "tests" / "fixtures" / "synthetic-courses", synthetic_library)
        incoming_dir = temp_root / "Incoming Synthetic Materials"
        incoming_dir.mkdir()
        incoming_paths = [
            incoming_dir / "TEST4099 Lecture A.txt",
            incoming_dir / "TEST4099 Tutorial B.txt",
        ]
        for index, path in enumerate(incoming_paths, start=1):
            path.write_text(f"TEST4099 packaged material {index}. Unique grounded text {index}.\n", encoding="utf-8")
        incoming_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in incoming_paths}
        custom_runtime = temp_root / "custom-runtime"
        custom_process, custom_url, _ = start_backend(
            custom_runtime,
            0,
            study_root=synthetic_library,
            packaged=True,
        )
        note_text = "Synthetic packaged desktop persistence note."
        note_file_id = 0
        managed_course_id = 0
        managed_material_ids: list[int] = []
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
            term = post_json(custom_url, "/api/terms/manage", {"action": "create", "name": "Packaged QA Term"})
            managed_course = post_json(
                custom_url,
                "/api/courses/manage",
                {
                    "action": "create",
                    "course_code": "TEST4099",
                    "display_name": "Packaged Product QA",
                    "term_id": int(term["id"]),
                },
            )
            managed_course_id = int(managed_course["id"])
            first_week = post_json(
                custom_url,
                "/api/weeks/manage",
                {"action": "create", "course_id": managed_course_id, "label": "Module Alpha", "kind": "module"},
            )
            second_week = post_json(
                custom_url,
                "/api/weeks/manage",
                {"action": "create", "course_id": managed_course_id, "label": "Review Block", "kind": "module"},
            )
            imported = post_json(
                custom_url,
                "/api/materials/import-paths",
                {
                    "paths": [str(path) for path in incoming_paths],
                    "course_id": managed_course_id,
                    "week_id": int(first_week["id"]),
                    "material_type": "Lecture",
                },
            )
            managed_material_ids = [int(item["id"]) for item in imported["items"] if item.get("status") == "added"]
            classified = post_json(
                custom_url,
                "/api/materials/manage",
                {
                    "action": "classify",
                    "ids": managed_material_ids,
                    "course_id": managed_course_id,
                    "week_id": int(second_week["id"]),
                    "material_type": "Reading",
                },
            )
            renamed_course = post_json(
                custom_url,
                "/api/courses/manage",
                {
                    "action": "update",
                    "id": managed_course_id,
                    "course_code": "TEST4199",
                    "display_name": "Renamed Packaged QA",
                    "term_id": int(term["id"]),
                },
            )
            archived_course = post_json(custom_url, "/api/courses/manage", {"action": "archive", "id": managed_course_id})
            restored_course = post_json(custom_url, "/api/courses/manage", {"action": "restore", "id": managed_course_id})
            managed_files = get_json(custom_url, f"/api/files?course_id={managed_course_id}")
            cache_result = post_json(custom_url, "/api/cache/clear", {})
            results.update(
                {
                    "packaged_custom_library_has_no_demo_contract": "demoMode" not in custom_health,
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
                    "packaged_term_course_week_crud": managed_course_id > 0
                    and first_week.get("kind") == "module"
                    and second_week.get("week_label") == "Review Block"
                    and renamed_course.get("code") == "TEST4199",
                    "packaged_native_path_batch_import": imported.get("added") == 2 and len(managed_material_ids) == 2,
                    "packaged_batch_reclassification": classified.get("ok") is True
                    and isinstance(managed_files, list)
                    and len(managed_files) == 2
                    and all(item.get("material_type") == "reading" and item.get("week_label") == "Review Block" for item in managed_files),
                    "packaged_course_archive_restore": archived_course.get("archived") == 1
                    and restored_course.get("archived") == 0,
                    "packaged_cache_rebuild": cache_result.get("ok") is True and int(cache_result.get("reindexed") or 0) >= 2,
                    "packaged_import_preserves_sources": all(
                        path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == incoming_hashes[path]
                        for path in incoming_paths
                    ),
                }
            )
        finally:
            stop_backend(custom_process)

        reopened_process, reopened_url, _ = start_backend(
            custom_runtime,
            0,
            study_root=synthetic_library,
            packaged=True,
        )
        try:
            notes = get_json(reopened_url, f"/api/notes?targetType=file&targetId={note_file_id}")
            reopened_courses = get_json(reopened_url, "/api/courses")
            reopened_files = get_json(reopened_url, f"/api/files?course_id={managed_course_id}")
            results["packaged_note_persistence"] = isinstance(notes, list) and any(
                item.get("body") == note_text for item in notes if isinstance(item, dict)
            )
            results["packaged_management_restart_persistence"] = isinstance(reopened_courses, list) and any(
                item.get("id") == managed_course_id and item.get("code") == "TEST4199"
                for item in reopened_courses if isinstance(item, dict)
            ) and isinstance(reopened_files, list) and len(reopened_files) == 2
            removed = post_json(
                reopened_url,
                "/api/materials/manage",
                {"action": "remove", "ids": managed_material_ids},
            )
            reset = post_json(reopened_url, "/api/reset", {"confirmation": "RESET STUDYHUB"})
            results["packaged_remove_and_reset_preserve_sources"] = removed.get("ok") is True and reset.get("ok") is True and all(
                path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == incoming_hashes[path]
                for path in incoming_paths
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
                    "clean_zero_config_has_no_demo_contract": "demoMode" not in dynamic_health,
                    "clean_zero_config_has_zero_files": int(dynamic_health.get("filesIndexed") or 0) == 0,
                    "runtime_database_outside_bundle": (runtime / "data" / "studyhub.sqlite").exists(),
                }
            )
        finally:
            stop_backend(dynamic_process)
        results["clean_sigterm_shutdown"] = dynamic_process.poll() == 0

        synthetic_library = temp_root / "Synthetic StudyLibrary"
        shutil.copytree(ROOT / "tests" / "fixtures" / "synthetic-courses", synthetic_library)
        custom_process, custom_url, _ = start_backend(
            temp_root / "custom-runtime",
            0,
            study_root=synthetic_library,
        )
        try:
            custom_health = health(custom_url)
            courses = get_json(custom_url, "/api/courses")
            search = get_json(custom_url, "/api/search?q=derivative")
            matching = [
                item
                for item in search
                if isinstance(item, dict) and "Derivatives" in str(item.get("filename") or "")
            ]
            starred_file_id = int(matching[0]["id"]) if matching else 0
            star_result = post_json(custom_url, f"/api/star/{starred_file_id}", {}) if starred_file_id else {}
            search_after_star = get_json(custom_url, "/api/search?q=derivative")
            recent_after_star = get_json(custom_url, "/api/recent")
            detail_after_star = get_json(custom_url, f"/api/file?id={starred_file_id}") if starred_file_id else {}
            starred_after_star = get_json(custom_url, "/api/starred")
            scan_result = rescan(custom_url)
            results.update(
                {
                    "synthetic_custom_library_has_no_demo_contract": "demoMode" not in custom_health,
                    "synthetic_custom_library_indexed": int(custom_health.get("filesIndexed") or 0) == 10,
                    "synthetic_courses_discovered": isinstance(courses, list)
                    and len(courses) == 3
                    and all(str(course.get("code") or "").startswith("TEST") for course in courses),
                    "synthetic_library_search": isinstance(search, list)
                    and any("Derivatives" in str(item.get("filename") or "") for item in search),
                    "star_state_consistent_across_file_views": bool(star_result.get("starred"))
                    and any(item.get("id") == starred_file_id and item.get("star_id") for item in search_after_star)
                    and any(item.get("id") == starred_file_id and item.get("star_id") for item in recent_after_star)
                    and detail_after_star.get("id") == starred_file_id
                    and bool(detail_after_star.get("star_id"))
                    and any(item.get("id") == starred_file_id and item.get("star_id") for item in starred_after_star),
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
