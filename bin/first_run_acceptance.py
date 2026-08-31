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
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("OPENAI_VECTOR_STORE_ID", None)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    server.ENV_LOCAL_PATH = tmp / ".env.local"
    server.ENV_LOCAL_EXISTS = False
    server.DEFAULT_STUDY_ROOT = server.DATA_DIR / "managed-library"
    server.ensure_dirs()
    return server


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        conn = server.connect_db()
        server.init_db(conn)
        preflight = server.api_preflight(conn)
        course_count = conn.execute("SELECT COUNT(*) AS count FROM courses").fetchone()["count"]
        file_count = conn.execute("SELECT COUNT(*) AS count FROM files").fetchone()["count"]
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
        server.DEFAULT_STUDY_ROOT = empty_library
        server.DB_PATH = tmp / "empty.sqlite"
        conn = server.connect_db()
        server.init_db(conn)
        empty_preflight = server.api_preflight(conn)
        conn.close()

        pdf_library = tmp / "PDF StudyLibrary"
        write_file(
            pdf_library / "DEMO1234 - PDF Course" / "Week 01" / "01 Course Materials" / "Lecture" / "Lecture.pdf",
            "%PDF-1.4\n% Synthetic placeholder PDF for preflight only.\n",
        )
        original_pdf_error = server.pdf_extraction_dependency_error
        server.pdf_extraction_dependency_error = lambda: "PDF text extraction unavailable: pdftotext was not found. Install Poppler and rescan the library."
        server.DEFAULT_STUDY_ROOT = pdf_library
        server.DB_PATH = tmp / "pdf.sqlite"
        server.scan_library(server.DEFAULT_STUDY_ROOT)
        conn = server.connect_db()
        pdf_preflight = server.api_preflight(conn)
        conn.close()
        server.pdf_extraction_dependency_error = original_pdf_error

        original_katex_dir = server.KATEX_DIST_DIR
        server.KATEX_DIST_DIR = tmp / "missing-katex-dist"
        conn = server.connect_db()
        math_preflight = server.api_preflight(conn)
        conn.close()
        server.KATEX_DIST_DIR = original_katex_dir

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

        server.DB_PATH = tmp / "provenance-migration.sqlite"
        migration_conn = server.connect_db()
        server.init_db(migration_conn)
        term_id = migration_conn.execute("SELECT id FROM terms WHERE stable_id='term_imported'").fetchone()["id"]
        now = server.now_iso()
        for stable_id, source_kind, folder_name in (
            ("legacy_fixture", "demo", "Synthetic Fixture Course"),
            ("user_named_demo", "folder", "Demo Course TEST1001"),
        ):
            migration_conn.execute(
                """
                INSERT INTO courses(
                  stable_id, code, course_code, name, display_name, folder_name, path,
                  source_folder, source_kind, term_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id, "TEST1001", "TEST1001", folder_name, folder_name,
                    folder_name, str(tmp / folder_name), str(tmp / folder_name),
                    source_kind, term_id, now, now,
                ),
            )
        migration_conn.commit()
        server.init_db(migration_conn)
        provenance_rows = {
            row["stable_id"] for row in migration_conn.execute("SELECT stable_id FROM courses").fetchall()
        }
        migration_conn.close()

        checks = {
            "fresh_launch_is_clean_and_empty": preflight["firstLaunch"] is True and course_count == 0 and file_count == 0,
            "fresh_launch_has_actionable_onboarding": any(
                item["code"] == "first_launch" and "create a course" in item["nextStep"].lower()
                for item in preflight["items"]
            ),
            "fresh_launch_has_no_demo_contract": "demoMode" not in preflight,
            "empty_workspace_has_no_pdf_warning": not any(item["code"] == "pdf_text_missing" for item in preflight["items"]),
            "openai_optional_explained": any(item["code"] == "openai_optional" and "optional" in item["title"].lower() for item in preflight["items"]),
            "own_library_config_saved_locally": config_result["restartRequired"] is True and "STUDY_LIBRARY_PATH" in env_text and "DEMO_MODE" not in env_text,
            "own_library_config_response_no_path": str(custom_library) not in str(config_result),
            "wrong_study_library_path_actionable": "folder was not found" in missing_error.lower(),
            "empty_library_recovery": any(item["code"] == "study_library_empty" and "add" in item["nextStep"].lower() for item in empty_preflight["items"]),
            "missing_pdftotext_recovery": any(item["code"] == "pdf_text_missing" and "poppler" in item["nextStep"].lower() for item in pdf_preflight["items"]),
            "missing_math_renderer_recovery": any(item["code"] == "math_renderer_missing" and "npm install" in item["nextStep"].lower() for item in math_preflight["items"]),
            "port_conflict_uses_next_port": fallback_port != busy_port,
            "missing_original_file_actionable": "scan library" in missing_original.lower(),
            "legacy_demo_removed_by_provenance_only": "legacy_fixture" not in provenance_rows
            and "user_named_demo" in provenance_rows,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
