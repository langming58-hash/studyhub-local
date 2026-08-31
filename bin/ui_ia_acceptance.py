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
        "courses_own_library_actions": all(key in courses for key in ('t("courses.scan")', 't("courses.addInbox")', 't("courses.new")')),
        "managed_empty_courses_remain_visible": "activeCourses" in courses
        and "with no files from this library view" not in courses
        and 't("courses.new")' in courses,
        "search_single_primary_input": "primary-search" in search and 't("search.placeholder")' in search,
        "study_consolidates_modes": "function studyTabs()" in js
        and 't("study.practice")' in js
        and 't("study.wrong")' in js
        and 't("study.exam")' in js,
        "teacher_question_safety_visible": 't("study.noGenerated")' in js
        and 't("study.noTeacherQuestion")' in js,
        "file_rows_preserve_preview_ai_open": "file-row" in js and "data-preview" in js and "data-ask-file" in js and 't("file.openOriginal")' in js,
        "file_preview_has_recoverable_route": '#/file/${encodeURIComponent(routeState.fileId)}' in js
        and 'return { view: "file", fileId:' in js
        and 'route("file"' in js,
        "settings_library_form_bound": "function bindLibrarySetupForm()" in js and "bindLibrarySetupForm();" in settings,
        "healthy_library_state_hidden": 'id="libraryState" hidden' in html and "Library ready" not in js,
        "home_button_wall_reduced": "Open latest" not in home and "Latest week" not in home and "Ask AI" not in segment(js, "function courseSummary", "async function renderCourses"),
        "course_zero_file_noise_reduced": "0 files" not in segment(js, "async function renderCourse", "async function renderWeek") and "All courses" not in segment(js, "async function renderCourse", "async function renderWeek"),
        "upload_uses_structured_material_type": 'id="uploadMaterialType"' in html
        and 'id="uploadSection"' not in html
        and 'id="uploadCategory"' not in html,
        "settings_has_health_privacy_advanced": all(key in settings for key in [
            't("settings.libraryHealth")', 't("settings.privacy")', 't("settings.advanced")', 't("settings.aiHistory")'
        ]),
        "ai_workspace_preserved": 't("ai.workspace")' in js and "source-card" in js and "availableScopes" in js,
        "ai_async_response_respects_current_route": 'if (state.view === "ai") renderAskGpt();' in js,
        "responsive_rules_present": "@media (max-width: 920px)" in css and ".settings-layout" in css and ".primary-search" in css,
        "skip_link_moves_focus_without_changing_route": 'class="skip-link"' in html
        and '$(".skip-link")?.addEventListener("click"' in js
        and 'main?.focus({ preventScroll: true })' in js,
        "responsive_navigation_is_compact": "grid-template-columns: repeat(6, minmax(0, 1fr))" in css
        and "grid-template-columns: repeat(3, minmax(0, 1fr))" in css,
        "focus_and_motion_preferences_present": ":focus-visible" in css
        and "@media (prefers-reduced-motion: reduce)" in css,
    }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
