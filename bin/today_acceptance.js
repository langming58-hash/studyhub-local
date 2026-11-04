#!/usr/bin/env node
"use strict";

const ui = require("../static/today-ui.js");

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function t(key, vars) {
  return vars && vars.time ? `${key} ${vars.time}` : key;
}

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}

const base = {
  state: "content",
  sections: { overdue: [], due_today: [], due_tomorrow: [], upcoming: [], review: [] },
  summary: {},
  last_refreshed_at: null,
  refreshing: false,
  error: "",
};

const html = ui.todaySectionHtml(base, t, escapeHtml);
check("today_heading_once", html.split('id="todayHeading"').length - 1 === 1);
check("today_deadline_groups_present", ["today-overdue", "today-due_today", "today-due_tomorrow", "today-upcoming", "today-review"].every((id) => html.includes(`id="${id}"`)));
check("today_refresh_button", html.includes('data-today-refresh="1"'));
check("today_no_secret_text", !/access_token|refresh_token|client_secret/i.test(html));

const withDue = Object.assign({}, base, {
  sections: {
    overdue: [{ bucket: "overdue", title: "Overdue A", course_code: "TEST1001", due_at: "2026-11-04T01:00:00Z", stable_id: "evt-1" }],
    due_today: [{ bucket: "due_today", title: "Today A", course_code: "TEST1001", due_at: "2026-11-04T05:00:00Z", stable_id: "evt-2" }],
    due_tomorrow: [],
    upcoming: [],
    review: [{ id: 7, course_code: "TEST1001", display_name: "Review material" }],
  },
});
const populated = ui.todaySectionHtml(withDue, t, escapeHtml);
check("today_card_semantic_time", populated.includes('<time datetime="2026-11-04T01:00:00Z">'));
check("today_review_action", populated.includes('data-today-review="7"'));
check("today_submitted_absent", !populated.includes("submitted"));
check("today_no_raw_remote_id", !populated.includes("evt-1"));

const withUrl = Object.assign({}, base, {
  sections: { overdue: [], due_today: [{ bucket: "due_today", title: "Open A", course_code: "TEST1001", due_at: "2026-11-04T05:00:00Z", html_url: "https://canvas.example.edu/courses/1/assignments/9" }], due_tomorrow: [], upcoming: [], review: [] },
});
check("today_open_in_canvas_rendered_for_safe_url", ui.todaySectionHtml(withUrl, t, escapeHtml).includes('data-today-open="https://canvas.example.edu/courses/1/assignments/9"'));
const nullUrl = Object.assign({}, base, {
  sections: { overdue: [], due_today: [{ bucket: "due_today", title: "No URL", course_code: "TEST1001", due_at: "2026-11-04T05:00:00Z", html_url: null }], due_tomorrow: [], upcoming: [], review: [] },
});
check("today_open_in_canvas_absent_for_null_url", !ui.todaySectionHtml(nullUrl, t, escapeHtml).includes("data-today-open"));

const scope = ui.todaySectionHtml(Object.assign({}, base, { state: "scope_upgrade_required" }), t, escapeHtml);
check("today_scope_upgrade_state", scope.includes("today.scopeUpgradeRequired") && scope.includes('data-today-reconnect="1"'));

const offline = ui.todaySectionHtml(Object.assign({}, base, { state: "canvas_disconnected" }), t, escapeHtml);
check("today_disconnected_state", offline.includes("today.canvasDisconnected"));

// i18n key parity.
global.window = {};
require("../static/i18n/catalog.js");
const en = window.STUDYHUB_TRANSLATIONS.en;
const zh = window.STUDYHUB_TRANSLATIONS["zh-CN"];
const required = [
  "nav.today", "today.heading", "today.overdue", "today.dueToday", "today.dueTomorrow",
  "today.upcoming", "today.reviewDue", "today.refreshAssignments", "today.openInCanvas",
  "today.scopeUpgradeRequired", "today.enableDeadlineNotifications",
];
check("today_i18n_parity", required.every((key) => en[key] && zh[key]));

if (failures > 0) {
  console.error(`Today acceptance failures: ${failures}`);
  process.exit(1);
}
console.log("Today acceptance: PASS");
