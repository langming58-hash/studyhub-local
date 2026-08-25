#!/usr/bin/env python3
"""Static IA/UI regression checks for the product redesign."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def segment(source: str, start: str, end: str) -> str:
    pattern = re.compile(rf"{re.escape(start)}([\s\S]*?){re.escape(end)}")
    match = pattern.search(source)
    return match.group(1) if match else ""


def main() -> int:
    html = read("static/index.html")
    js = read("static/app.js")
    css = read("static/styles.css")

    nav_items = re.findall(r'class="nav-item[^"]*"\s+data-view="([^"]+)"', html)
    home = segment(js, "async function renderHome()", "function focusCourseCard")
    courses = segment(js, "async function renderCourses()", "async function renderThisWeek")
    search = segment(js, "async function renderSearch()", "async function runSearch")
    settings = segment(js, "async function renderSettings()", "function empty")

    checks = {
        "primary_nav_has_six_destinations": nav_items == ["home", "courses", "search", "study", "ai", "settings"],
        "sidebar_course_list_removed": 'id="courseList"' not in html and "renderCourseList() {}" in js,
        "global_scan_upload_hidden_by_default": 'id="uploadBtn" class="button secondary" hidden' in html
        and 'id="scanBtn" class="button secondary" hidden' in html,
        "sidebar_collapse_persisted": "studyhub.sidebarCollapsed" in js and ".sidebar-collapsed" in css,
        "home_is_study_first": "continue-panel" in home and "metric-card" not in home and "progress-track" not in home,
        "home_scan_not_primary": "Scan Library" not in home and "Add Material" not in home,
        "courses_own_library_actions": "Scan Library" in courses and "Add Material" in courses and "Courses" in courses,
        "stale_empty_courses_hidden_from_courses": "activeCourses" in courses and "with no files from this library view" in courses,
        "search_single_primary_input": "primary-search" in search and "Search filenames or readable text" in search,
        "study_consolidates_modes": "function studyTabs()" in js
        and "Practice" in js
        and "Wrong Questions" in js
        and "Exam Review" in js,
        "teacher_question_safety_visible": "No generated practice questions" in js
        and "No suitable teacher-provided question was found" in js,
        "file_rows_preserve_preview_ai_open": "file-row" in js and "data-preview" in js and "data-ask-file" in js and "Open original file" in js,
        "file_preview_has_recoverable_route": '#/file/${encodeURIComponent(routeState.fileId)}' in js
        and 'return { view: "file", fileId:' in js
        and 'route("file"' in js,
        "settings_library_form_bound": "function bindLibrarySetupForm()" in js and segment(js, "async function renderSettings()", "function empty").strip().endswith("bindLibrarySetupForm();\n}"),
        "healthy_library_state_hidden": 'id="libraryState" hidden' in html and "Library ready" not in js,
        "home_button_wall_reduced": "Open latest" not in home and "Latest week" not in home and "Ask AI" not in segment(js, "function courseSummary", "async function renderCourses"),
        "course_zero_file_noise_reduced": "0 files" not in segment(js, "async function renderCourse", "async function renderWeek") and "All courses" not in segment(js, "async function renderCourse", "async function renderWeek"),
        "upload_internal_labels_hidden": '<option value="01 Course Materials">Course materials</option>' in html
        and '<option value="02 Exercises">Exercises</option>' in html
        and '<option value="My_Work">My work</option>' in html,
        "settings_has_health_privacy_advanced": all(token in settings for token in ["Library Health", "Privacy", "Advanced", "AI History"]),
        "ai_workspace_preserved": "AI Study Workspace" in js and "source-card" in js and "availableScopes" in js,
        "ai_async_response_respects_current_route": 'if (state.view === "ai") renderAskGpt();' in js,
        "responsive_rules_present": "@media (max-width: 920px)" in css and ".settings-layout" in css and ".primary-search" in css,
    }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
