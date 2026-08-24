#!/usr/bin/env python3
"""Regression checks for first-time-user P2 findings. Uses synthetic fixtures only."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "LibraryA")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_p2_test", server_path)
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    return server


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def call_ask(server, body: dict) -> dict:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: body
    sent: dict = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_ask(handler)
    return sent["data"]


def post_note(server, body: dict) -> dict:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: body
    sent: dict = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_note(handler)
    return sent["data"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        library_a = tmp / "LibraryA"
        library_b = tmp / "LibraryB"

        write_file(
            library_a / "TEST1001 - Synthetic Demo Course" / "Week 01" / "01 Course Materials" / "Lecture" / "Lecture 01.txt",
            "Synthetic demo lecture. The indexed answer is blue.\n",
        )
        server.DEFAULT_STUDY_ROOT = library_a
        server.scan_library(library_a)

        write_file(
            library_b / "DEMO1010 - Custom Synthetic Course" / "Week 01" / "02 Exercises" / "Tutorial" / "Tutorial 01 Questions.txt",
            "\n".join(
                [
                    "DEMO1010 Tutorial 01",
                    "",
                    "Q1. What should a student compare in this synthetic allocation problem?",
                    "",
                    "Q2. Explain why local-first organization reduces filing friction.",
                    "",
                    "Official Teacher Solution",
                    "Q1. Compare marginal benefit with marginal cost.",
                ]
            ),
        )
        server.DEFAULT_STUDY_ROOT = library_b
        stats = server.scan_library(library_b)

        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        visible_courses = conn.execute(
            """
            SELECT c.code, COUNT(f.id) AS file_count
            FROM courses c
            LEFT JOIN files f ON f.course_id=c.id AND f.active=1
            GROUP BY c.id
            HAVING file_count > 0
            ORDER BY c.code
            """
        ).fetchall()
        tutorial = conn.execute("SELECT * FROM files WHERE filename='Tutorial 01 Questions.txt' AND active=1").fetchone()
        question_rows = conn.execute("SELECT question_number, question_text FROM questions ORDER BY question_number").fetchall()
        solution_count = conn.execute("SELECT COUNT(*) AS c FROM solutions").fetchone()["c"]
        conn.close()

        no_match = call_ask(
            server,
            {
                "context": {"course": "DEMO1010", "week": "Week 01"},
                "prompt": "Explain a zzznonexistent retrieval topic.",
            },
        )
        generic_file_summary = call_ask(
            server,
            {
                "context": {"fileId": tutorial["id"]},
                "prompt": "Summarize this file.",
            },
        )
        teacher_question = call_ask(
            server,
            {
                "context": {"course": "DEMO1010", "week": "Week 01", "exerciseType": "Tutorial"},
                "prompt": "Give me a practice question from Tutorial Q1.",
            },
        )
        post_note(
            server,
            {
                "targetType": "file",
                "targetId": tutorial["id"],
                "body": "Synthetic user note: revisit marginal comparison.",
            },
        )
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        note_count_before = conn.execute("SELECT COUNT(*) AS c FROM notes WHERE target_id=?", (tutorial["id"],)).fetchone()["c"]
        conn.close()

        server_reloaded = load_server(tmp)
        server_reloaded.DEFAULT_STUDY_ROOT = library_b
        conn = sqlite3.connect(server_reloaded.DB_PATH)
        conn.row_factory = sqlite3.Row
        note_count_after = conn.execute("SELECT COUNT(*) AS c FROM notes WHERE target_id=?", (tutorial["id"],)).fetchone()["c"]
        conn.close()

        question_texts = {row["question_number"]: row["question_text"] for row in question_rows}
        checks = {
            "stale_empty_courses_hidden": [row["code"] for row in visible_courses] == ["DEMO1010"],
            "custom_library_scan_ok": stats.new_files == 1 and stats.removed_files == 1,
            "askgpt_no_match_scoped": no_match["status"] == "no_source"
            and "currently indexed official course materials" in no_match["response"]
            and not no_match["questions"]
            and not no_match["solutions"],
            "askgpt_generic_file_scope_preserved": generic_file_summary["status"] == "local"
            and generic_file_summary["sources"]
            and generic_file_summary["sources"][0]["source_file_id"] == tutorial["id"],
            "teacher_question_still_allowed": teacher_question["status"] == "teacher_question"
            and teacher_question["questions"]
            and teacher_question["questions"][0]["course_code"] == "DEMO1010",
            "solutions_payload_has_no_cache_path": teacher_question["solutions"]
            and all("text_cache_path" not in solution for solution in teacher_question["solutions"]),
            "notes_persist_after_reload": note_count_before == 1 and note_count_after == 1,
            "solution_heading_not_in_question": "Official Teacher Solution" not in question_texts.get("Q2", ""),
            "plain_text_solution_heading_detected": solution_count == 1,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
