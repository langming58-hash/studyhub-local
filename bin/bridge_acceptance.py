#!/usr/bin/env python3
"""Synthetic AI Bridge acceptance checks. Uses no real course material."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "StudyLibrary"
        db_path = Path(tmp) / "studyhub.sqlite"
        os.environ["STUDY_LIBRARY_PATH"] = str(root)
        os.environ["DATABASE_PATH"] = str(db_path)
        course_dir = root / "TEST1001 - Synthetic Course" / "Week 01" / "02 Exercises" / "Tutorial"
        course_dir.mkdir(parents=True)
        source = course_dir / "Tutorial_1.txt"
        source.write_text("Q1. Explain the synthetic multiplier using only TEST1001 notes.\n", encoding="utf-8")

        import importlib.util

        server_path = Path(__file__).resolve().parents[1] / "server.py"
        spec = importlib.util.spec_from_file_location("studyhub_server_for_test", server_path)
        server = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = server
        spec.loader.exec_module(server)
        server.DATA_DIR = Path(tmp) / "data"
        server.CACHE_DIR = Path(tmp) / "cache"
        server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
        server.LOG_DIR = Path(tmp) / "logs"
        server.DB_PATH = db_path
        server.DEFAULT_STUDY_ROOT = root

        stats1 = server.scan_library(root)
        stats2 = server.scan_library(root)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        q_count = conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
        f_count = conn.execute("SELECT COUNT(*) AS c FROM files WHERE active=1").fetchone()["c"]
        conn.close()
        source.write_text("Q1. Explain the updated synthetic multiplier using only TEST1001 notes.\n", encoding="utf-8")
        stats3 = server.scan_library(root)
        shutil.rmtree(root)

        checks = {
            "first_scan_new_file": stats1.new_files == 1 and f_count == 1,
            "question_detected": q_count == 1,
            "second_scan_incremental": stats2.unchanged_files == 1 and stats2.updated_files == 0,
            "updated_file_detected": stats3.updated_files == 1,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
