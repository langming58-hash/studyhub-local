#!/usr/bin/env python3
"""Synthetic acceptance checks for the v0.3 Study Engine."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "product-gallery"


def load_server(tmp: Path):
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["STUDY_LIBRARY_PATH"] = str(FIXTURES)
    os.environ["STUDYHUB_RUNTIME_DIR"] = str(tmp / "runtime")
    os.environ["DATABASE_PATH"] = str(tmp / "runtime" / "data" / "studyhub.sqlite")
    spec = importlib.util.spec_from_file_location("studyhub_study_engine_test", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    return server


def api_get(server, path: str, query: dict[str, list[str]]):
    handler = object.__new__(server.StudyHubHandler)
    sent: dict = {}
    handler.send_json = lambda data, status=200: sent.update({"data": data, "status": status})
    server.StudyHubHandler.handle_api_get(handler, path, query)
    return sent["data"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        stats = server.scan_library(FIXTURES)
        conn = server.connect_db()
        server.init_db(conn)

        files = conn.execute("SELECT * FROM files WHERE active=1 ORDER BY course_code, week_label, filename").fetchall()
        first = files[0]
        first_id = int(first["id"])
        initial = server.study_overview(conn)
        opened = server.update_material_study_state(conn, {"file_id": first_id, "action": "open"})
        in_progress = server.study_overview(conn, int(first["course_id"]), str(first["week_label"]))
        completed = server.update_material_study_state(conn, {"file_id": first_id, "action": "complete"})
        flagged = server.update_material_study_state(conn, {"file_id": first_id, "action": "needs_review"})
        review_queue = server.study_overview(conn)
        reviewed = server.update_material_study_state(conn, {"file_id": first_id, "action": "review"})
        after_review = server.study_overview(conn)
        public_payload = server.public_file(conn.execute("SELECT * FROM files WHERE id=?", (first_id,)).fetchone())
        source_count = conn.execute("SELECT COUNT(*) AS c FROM files WHERE active=1").fetchone()["c"]
        econ_course_id = conn.execute("SELECT id FROM courses WHERE course_code='ECON201'").fetchone()["id"]
        conn.close()

        regression_results = api_get(server, "/api/search", {"q": ["regression"]})
        contextual_results = api_get(
            server,
            "/api/search",
            {"q": ["synthetic"], "context_course_id": [str(econ_course_id)], "context_week": ["Week 02"]},
        )

        conn = server.connect_db()
        server.init_db(conn)
        persisted = conn.execute("SELECT * FROM material_study_state WHERE file_id=?", (first_id,)).fetchone()
        course_progress = server.study_overview(conn, int(first["course_id"]))["summary"]
        conn.close()

        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        package = (ROOT / "package.json").read_text(encoding="utf-8")
        checks = {
            "synthetic_gallery_scans": stats.files >= 9 and source_count >= 9,
            "new_materials_start_unstarted": initial["summary"]["not_started"] == source_count,
            "opening_marks_in_progress": opened["status"] == "in_progress" and in_progress["summary"]["in_progress"] == 1,
            "completion_is_single_file_state": completed["status"] == "completed",
            "review_queue_prioritizes_flagged_material": flagged["needs_review"] == 1
            and review_queue["queue"][0]["id"] == first_id,
            "review_clears_flag_without_losing_completion": reviewed["needs_review"] == 0
            and first_id not in {row["id"] for row in after_review["queue"]},
            "progress_aggregates_by_course_and_week": course_progress["completed"] == 1
            and course_progress["progress_percent"] > 0,
            "study_state_persists_after_restart": persisted["status"] == "completed"
            and persisted["needs_review"] == 0,
            "public_payload_hides_private_paths": "original_path" not in public_payload
            and "absolute_path" not in public_payload
            and str(tmp) not in str(public_payload),
            "search_weights_filename_over_body": regression_results
            and regression_results[0]["filename"] == "Regression Foundations.txt",
            "search_context_boosts_current_course_and_week": contextual_results
            and contextual_results[0]["course_code"] == "ECON201"
            and contextual_results[0]["week_label"] == "Week 02",
            "search_snippets_hide_absolute_paths": all(
                "original_path" not in row and "absolute_path" not in row and str(tmp) not in str(row)
                for row in regression_results + contextual_results
            ),
            "study_engine_ui_is_integrated": 't("study.plan")' in app_js
            and "/api/study/overview" in app_js
            and "/api/study/material" in app_js,
            "acceptance_is_wired_into_normal_tests": "study_engine_acceptance.py" in package,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
