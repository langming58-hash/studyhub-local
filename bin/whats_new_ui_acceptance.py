#!/usr/bin/env python3
"""Static product-contract checks for the offline What's New experience."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def segment(source: str, start: str, end: str) -> str:
    pattern = re.compile(rf"{re.escape(start)}([\s\S]*?){re.escape(end)}")
    match = pattern.search(source)
    return match.group(1) if match else ""


def catalogs() -> dict[str, dict[str, str]]:
    source = read("static/i18n/catalog.js")
    english_start = source.index("en: {") + len("en: ")
    english_end = source.index('\n  },\n  "zh-CN": {') + len("\n  }")
    chinese_start = source.index('"zh-CN": {') + len('"zh-CN": ')
    chinese_end = source.rindex("\n  }") + len("\n  }")
    return {
        "en": json.loads(source[english_start:english_end]),
        "zh-CN": json.loads(source[chinese_start:chinese_end]),
    }


def main() -> int:
    html = read("static/index.html")
    js = read("static/app.js")
    css = read("static/styles.css")
    server = read("server.py")
    translations = catalogs()
    home = segment(js, "async function renderHome()", "function whatsNewKindLabel")
    dedicated = segment(js, "async function renderWhatsNew", "async function markWhatsNewSeen")
    event_row = segment(js, "function whatsNewEventRow", "function renderHomeWhatsNew")
    click_handlers = segment(js, 'document.addEventListener("click"', 'document.addEventListener("change"')
    nav_items = re.findall(r'class="nav-item[^"]*"\s+data-view="([^"]+)"', html)
    required_keys = {
        "whatsNew.title",
        "whatsNew.viewAll",
        "whatsNew.viewHistory",
        "whatsNew.unread",
        "whatsNew.all",
        "whatsNew.newMaterial",
        "whatsNew.materialUpdated",
        "whatsNew.detailsChanged",
        "whatsNew.markRead",
        "whatsNew.markShownRead",
        "whatsNew.openMaterial",
        "whatsNew.materialUnavailable",
        "whatsNew.caughtUp",
        "whatsNew.noUpdates",
        "whatsNew.fileName",
        "whatsNew.displayName",
        "whatsNew.fileType",
        "whatsNew.fileSize",
        "whatsNew.updatedTime",
        "whatsNew.availability",
        "whatsNew.showingLatest",
    }
    checks = {
        "home_renders_whats_new_section": "renderHomeWhatsNew(changes)" in home,
        "home_requests_bounded_unseen_list": "fetchWhatsNew(true, 5)" in home,
        "view_all_routes_to_recoverable_hash": 'data-view="whats-new"' in js
        and 'if (parts[0] === "whats-new")' in js
        and 'viewName === "whats-new"' in js,
        "primary_nav_remains_exactly_six": nav_items == ["home", "courses", "search", "study", "ai", "settings"],
        "whats_new_keeps_home_nav_active": 'viewName === "whats-new" ? "home" : viewName' in js,
        "render_and_prefetch_never_mark_seen": "/api/whats-new/seen" not in home
        and "/api/whats-new/seen" not in dedicated,
        "mark_seen_is_explicit_event_handler_only": 'target.dataset.whatsNewRead' in click_handlers
        and 'target.hasAttribute("data-whats-new-read-shown")' in click_handlers
        and 'postJson("/api/whats-new/seen"' in js,
        "open_material_uses_safe_file_id_route": 'route("file", { fileId: Number(target.dataset.whatsNewOpen) })' in click_handlers,
        "unsafe_navigation_metadata_not_rendered": all(
            token not in event_row
            for token in ("original_path", "remote_item_stable_id", "authority_id", "resulting_sha256", "Canvas URL")
        ),
        "provider_labels_are_escaped": all(
            token in event_row
            for token in ("escapeHtml(item.material_label", "escapeHtml(item.course_label", "escapeHtml(item.applied_at")
        ),
        "raw_event_enums_are_humanized": all(
            token in js
            for token in (
                'CANVAS_FILE_ADDED: "whatsNew.newMaterial"',
                'CANVAS_FILE_CONTENT_UPDATED: "whatsNew.materialUpdated"',
                'CANVAS_FILE_METADATA_UPDATED: "whatsNew.detailsChanged"',
            )
        ),
        "unread_state_is_not_color_only": "unread-dot" in event_row
        and "sr-only" in event_row
        and ".whats-new-row.is-unread .whats-new-copy" in css,
        "empty_and_caught_up_states_exist": 't("whatsNew.noUpdates")' in js and 't("whatsNew.caughtUp")' in js,
        "mark_shown_uses_only_rendered_unseen_ids": "state.whatsNew.events.filter((item) => !item.seen).map((item) => item.event_id)" in click_handlers,
        "null_file_id_is_safe": "item.file_id === null || item.file_id === undefined" in event_row
        and 't("whatsNew.materialUnavailable")' in event_row,
        "responsive_event_layout_exists": "@media (max-width: 920px)" in css
        and ".whats-new-row" in css
        and "grid-column: 2" in css,
        "english_and_chinese_keys_complete": all(
            required_keys <= set(translations[locale])
            for locale in ("en", "zh-CN")
        ),
        "count_api_is_backwards_compatible": '"events": list_academic_changes(' in server
        and '"unseen_count"' in server
        and '"total_count"' in server,
        "count_query_is_not_page_limited": "def academic_change_counts(" in server
        and "COUNT(*) AS total_count" in server
        and "LIMIT" not in segment(server, "def academic_change_counts(", "def canonical_backup_json"),
        "no_sync_or_notification_controls_added": all(
            token not in dedicated
            for token in ("sync button", "background sync", "Notification", "OAuth")
        ),
    }
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
