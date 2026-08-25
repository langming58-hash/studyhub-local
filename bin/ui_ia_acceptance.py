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
        "courses_own_library_actions": "Scan Library" in courses and "Add Material" in courses and "Active Courses" in courses,
        "stale_empty_courses_hidden_from_courses": "activeCourses" in courses and "inactive empty course" in courses,
        "search_single_primary_input": "primary-search" in search and "Search filenames or readable text" in search,
        "study_consolidates_modes": "function studyTabs()" in js
        and "Practice" in js
        and "Wrong Questions" in js
        and "Exam Review" in js,
        "teacher_question_safety_visible": "No generated practice questions" in js
        and "No suitable teacher-provided question was found" in js,
        "file_rows_preserve_preview_ai_open": "file-row" in js and "data-preview" in js and "data-ask-file" in js and "Open Original" in js,
        "settings_has_health_privacy_advanced": all(token in settings for token in ["Library Health", "Privacy", "Advanced", "AI History"]),
        "ai_workspace_preserved": "AI Study Workspace" in js and "source-card" in js and "availableScopes" in js,
        "responsive_rules_present": "@media (max-width: 920px)" in css and ".settings-layout" in css and ".primary-search" in css,
    }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
