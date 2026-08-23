#!/usr/bin/env python3
"""Synthetic Ask GPT acceptance checks. Uses no real course material or OpenAI calls."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "StudyLibrary")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_askgpt_test", server_path)
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
    return server


def call_ask(server, body: dict) -> dict:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: body
    sent: dict = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_ask(handler)
    return sent["data"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        root = server.DEFAULT_STUDY_ROOT
        lecture_dir = root / "TEST1001 - Synthetic Course" / "Week 01" / "01 Course Materials" / "Lecture"
        tutorial_dir = root / "TEST1001 - Synthetic Course" / "Week 01" / "02 Exercises" / "Tutorial"
        lecture_dir.mkdir(parents=True)
        tutorial_dir.mkdir(parents=True)
        lecture = lecture_dir / "Lecture_1.txt"
        tutorial = tutorial_dir / "Tutorial_1.txt"
        solution = tutorial_dir / "Tutorial_1_Solutions.txt"
        lecture.write_text("The synthetic multiplier equals 2 when MPC is 0.5.\n", encoding="utf-8")
        tutorial.write_text("Q1. Explain why the synthetic multiplier equals 2 without changing the teacher question.\n", encoding="utf-8")
        solution.write_text("Official worked answer: multiplier = 1 / (1 - 0.5) = 2.\n", encoding="utf-8")

        stats = server.scan_library(root)
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        lecture_row = conn.execute("SELECT * FROM files WHERE filename='Lecture_1.txt'").fetchone()
        tutorial_row = conn.execute("SELECT * FROM files WHERE filename='Tutorial_1.txt'").fetchone()
        q_count = conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
        conn.close()

        test_a = call_ask(
            server,
            {
                "context": {"fileId": lecture_row["id"]},
                "prompt": "What is the synthetic multiplier when MPC is 0.5?",
            },
        )
        test_b = call_ask(
            server,
            {
                "context": {"fileId": tutorial_row["id"]},
                "prompt": "Explain Q1 meaning without solving it.",
            },
        )
        test_c = call_ask(
            server,
            {
                "context": {"course": "TEST1001", "week": "Week 02", "exerciseType": "Quiz"},
                "prompt": "Generate a new practice question about a missing topic.",
            },
        )

        calls = {"uploads": 0, "attached": 0, "deleted": []}
        old_key = os.environ.get("OPENAI_API_KEY")
        old_vs = os.environ.get("OPENAI_VECTOR_STORE_ID")
        os.environ["OPENAI_API_KEY"] = "mock-openai-key"
        os.environ.pop("OPENAI_VECTOR_STORE_ID", None)

        def fake_json_request(method: str, path: str, payload: dict | None = None) -> dict:
            if method == "POST" and path == "/vector_stores":
                return {"id": "vs_mock"}
            if method == "POST" and path.startswith("/vector_stores/"):
                calls["attached"] += 1
                return {"status": "indexed"}
            raise AssertionError(f"unexpected request {method} {path}")

        def fake_upload_file(path: Path) -> dict:
            calls["uploads"] += 1
            return {"id": f"file_mock_{calls['uploads']}"}

        def fake_delete(path: str) -> dict:
            calls["deleted"].append(path)
            return {"deleted": True}

        server.openai_json_request = fake_json_request
        server.openai_upload_file = fake_upload_file
        server.openai_delete = fake_delete
        sync1 = server.sync_openai_vector_store(limit=20)
        sync2 = server.sync_openai_vector_store(limit=20)
        lecture.write_text("The updated synthetic multiplier equals 3 when MPC is two thirds.\n", encoding="utf-8")
        server.scan_library(root)
        sync3 = server.sync_openai_vector_store(limit=20)
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
        if old_vs is None:
            os.environ.pop("OPENAI_VECTOR_STORE_ID", None)
        else:
            os.environ["OPENAI_VECTOR_STORE_ID"] = old_vs

        checks = {
            "test_a_lecture_answer": "synthetic multiplier equals 2" in test_a["response"] and test_a["sources"][0]["course_code"] == "TEST1001",
            "test_b_real_teacher_question": q_count >= 1 and test_b["questions"][0]["question_number"] == "Q1" and "Teacher-provided question" in test_b["response"],
            "test_c_no_generated_question": test_c["status"] == "no_teacher_question" and "No suitable teacher-provided question" in test_c["response"],
            "test_d_new_files_synced": stats.new_files == 3 and sync1["synced"] == 3,
            "test_d_unchanged_not_resynced": sync2["synced"] == 0 and sync2["unchanged"] == 3,
            "test_d_changed_stale_removed": sync3["synced"] == 1 and sync3["staleRemoved"] == 1 and bool(calls["deleted"]),
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
