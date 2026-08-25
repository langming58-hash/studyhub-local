#!/usr/bin/env python3
"""First-run and recovery UX checks. Uses synthetic fixtures only."""

from __future__ import annotations

import importlib.util
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path


def load_server(tmp: Path):
    for key in ("DEMO_MODE", "STUDY_LIBRARY_PATH", "OPENAI_API_KEY", "OPENAI_VECTOR_STORE_ID"):
        os.environ.pop(key, None)
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_first_run_test", server_path)
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    server.ENV_LOCAL_PATH = tmp / ".env.local"
    server.ENV_LOCAL_EXISTS = False
    server.DEMO_MODE = True
    server.DEFAULT_STUDY_ROOT = server.APP_ROOT / "demo-data"
    return server


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        server.scan_library(server.DEFAULT_STUDY_ROOT)
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        preflight = server.api_preflight(conn)
        conn.close()

        custom_library = tmp / "My Synthetic StudyLibrary"
        write_file(
            custom_library / "DEMO1234 - First Run Course" / "Week 01" / "01 Course Materials" / "Lecture" / "Lecture.txt",
            "Synthetic first-run lecture content.",
        )
        config_result = server.save_study_library_config(str(custom_library))
        env_text = server.ENV_LOCAL_PATH.read_text(encoding="utf-8")
        missing_error = ""
        try:
            server.save_study_library_config(str(tmp / "Missing Library"))
        except FileNotFoundError as exc:
            missing_error = str(exc)

        empty_library = tmp / "Empty StudyLibrary"
        empty_library.mkdir()
        server.DEMO_MODE = False
        server.DEFAULT_STUDY_ROOT = empty_library
        server.DB_PATH = tmp / "empty.sqlite"
        conn = server.connect_db()
        server.init_db(conn)
        empty_preflight = server.api_preflight(conn)
        conn.close()

        original_pdf_error = server.pdf_extraction_dependency_error
        server.pdf_extraction_dependency_error = lambda: "PDF text extraction unavailable: pdftotext was not found. Install Poppler and rescan the library."
        conn = server.connect_db()
        pdf_preflight = server.api_preflight(conn)
        conn.close()
        server.pdf_extraction_dependency_error = original_pdf_error

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            busy_port = sock.getsockname()[1]
            fallback_port = server.find_free_port(busy_port)

        source_file = tmp / "Open Test" / "TEST1001 - Open Test" / "Week 01" / "01 Course Materials" / "Lecture" / "Open.txt"
        write_file(source_file, "Synthetic file that will be removed.")
        server.DEFAULT_STUDY_ROOT = tmp / "Open Test"
        server.DB_PATH = tmp / "open.sqlite"
        server.scan_library(server.DEFAULT_STUDY_ROOT)
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id FROM files WHERE filename='Open.txt'").fetchone()
        conn.close()
        source_file.unlink()
        missing_original = ""
        try:
            handler = object.__new__(server.StudyHubHandler)
            handler.handle_open(row["id"])
        except FileNotFoundError as exc:
            missing_original = str(exc)

        checks = {
            "fresh_launch_defaults_to_demo": preflight["demoMode"] is True and preflight["firstLaunch"] is True,
            "demo_mode_is_explained": any(item["code"] == "demo_mode" and "synthetic" in item["whatHappened"].lower() for item in preflight["items"]),
            "openai_optional_explained": any(item["code"] == "openai_optional" and "optional" in item["title"].lower() for item in preflight["items"]),
            "own_library_config_saved_locally": config_result["restartRequired"] is True and "STUDY_LIBRARY_PATH" in env_text and "DEMO_MODE=\"false\"" in env_text,
            "own_library_config_response_no_path": str(custom_library) not in str(config_result),
            "wrong_study_library_path_actionable": "folder was not found" in missing_error.lower(),
            "empty_library_recovery": any(item["code"] == "study_library_empty" and "add" in item["nextStep"].lower() for item in empty_preflight["items"]),
            "missing_pdftotext_recovery": any(item["code"] == "pdf_text_missing" and "poppler" in item["nextStep"].lower() for item in pdf_preflight["items"]),
            "port_conflict_uses_next_port": fallback_port != busy_port,
            "missing_original_file_actionable": "scan library" in missing_original.lower(),
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
