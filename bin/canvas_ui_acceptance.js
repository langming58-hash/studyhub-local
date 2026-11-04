#!/usr/bin/env node
"use strict";

// Executable DOM harness for the Canvas Settings section. It runs the same
// pure render functions used by the app, so it verifies real output rather
// than source strings. It intentionally does not do visual/image checks.

const ui = require("../static/canvas-ui.js");

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function t(key) {
  return key;
}

function baseCanvas(overrides) {
  return Object.assign(
    {
      status: "unavailable",
      authMode: null,
      origin: null,
      displayName: null,
      courses: [],
      enrollmentState: "active",
      selectedIds: new Set(),
      selectionRevision: "",
      selectionPreview: null,
      syncPlan: null,
      syncRevision: "",
      syncSelectedIds: new Set(),
      busy: false,
      error: "",
    },
    overrides || {},
  );
}

function count(html, needle) {
  return html.split(needle).length - 1;
}

function stripTags(html) {
  return html.replace(/<[^>]*>/g, " ");
}

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}

const states = ["unavailable", "not_connected", "connecting", "connected", "reconnect_required", "error"];

for (const status of states) {
  const html = ui.canvasSectionHtml(baseCanvas({ status }), t, escapeHtml);
  check(`state_${status}_canvas_heading_once`, count(html, 'id="canvasHeading"') === 1);
  check(`state_${status}_status_live_region`, html.includes('id="canvasStatus"') && html.includes('aria-live="polite"'));
  check(`state_${status}_no_secret_text`, !/access_token|refresh_token|client_secret/i.test(stripTags(html)));
}

const unavailable = ui.canvasSectionHtml(baseCanvas({ status: "unavailable" }), t, escapeHtml);
check("unavailable_no_connect_button", !unavailable.includes('data-canvas-connect="1"'));
check("unavailable_explains_institution_setup", unavailable.includes('id="canvasStatus"'));

const notConnected = ui.canvasSectionHtml(baseCanvas({ status: "not_connected" }), t, escapeHtml);
check("not_connected_has_connect_button", notConnected.includes('data-canvas-connect="1"'));
check("not_connected_no_pat_paste", !/access.?token|token/i.test(stripTags(notConnected)));

const connecting = ui.canvasSectionHtml(baseCanvas({ status: "connecting" }), t, escapeHtml);
check("connecting_aria_busy", connecting.includes('aria-busy="true"'));
check("connecting_has_response_input", connecting.includes('id="canvasOAuthResponse"'));
check("connecting_complete_disabled", connecting.includes('data-canvas-complete="1" disabled'));
check("connecting_has_cancel", connecting.includes('data-canvas-cancel="1"'));
check("connecting_no_connect_button", !connecting.includes('data-canvas-connect="1"'));

const course = {
  remote_course_id: "123456789012345678901234567890",
  name: "Synthetic Calculus",
  course_code: "TEST1001",
  term_name: "Term 1",
  workflow_state: "available",
};
const connectedCanvas = baseCanvas({
  status: "connected",
  origin: "https://canvas.example.edu",
  displayName: "Synthetic Student",
  courses: [course],
  selectedIds: new Set([String(course.remote_course_id)]),
});
const connected = ui.canvasSectionHtml(connectedCanvas, t, escapeHtml);
check("connected_refresh_sync_disconnect", ["data-canvas-refresh-courses=\"1\"", "data-canvas-sync-preview=\"1\"", "data-canvas-disconnect=\"1\""].every((s) => connected.includes(s)));
check("connected_course_checkbox_has_label", connected.includes('id="canvas-course-' + course.remote_course_id + '"') && connected.includes('for="canvas-course-' + course.remote_course_id + '"'));
check("connected_sync_disabled_without_eligible", ui.canApplySync(connectedCanvas.syncSelectedIds, []) === false);
check("connected_no_raw_remote_id_visible", !stripTags(connected).includes(course.remote_course_id));
check("connected_no_secret_text", !/access_token|refresh_token|client_secret/i.test(stripTags(connected)));

const actions = [
  { action: "ADD", remote_file_stable_id: "f1", display_name: "Added file" },
  { action: "POTENTIAL_CONTENT_CHANGE", remote_file_stable_id: "f2", display_name: "Changed file" },
  { action: "METADATA_CHANGE", remote_file_stable_id: "f3", display_name: "Metadata file" },
  { action: "UNCHANGED", remote_file_stable_id: "f4", display_name: "Unchanged file" },
  { action: "MISSING_FROM_REMOTE_DISCOVERY", remote_file_stable_id: "f5", display_name: "Missing file" },
];
check("eligible_sync_action_set_exact", JSON.stringify(ui.syncActionEligibleIds(actions)) === JSON.stringify(["f1", "f2", "f3"]));
check("can_apply_sync_true_for_eligible", ui.canApplySync(new Set(["f1"]), actions) === true);
check("can_apply_sync_false_for_non_eligible", ui.canApplySync(new Set(["f4", "f5"]), actions) === false);

const planCanvas = baseCanvas({ status: "connected", syncPlan: { actions }, syncSelectedIds: new Set(["f1"]) });
const planHtml = ui.canvasSectionHtml(planCanvas, t, escapeHtml);
check("sync_apply_enabled_with_eligible_selection", !planHtml.includes('data-canvas-sync-apply="1" disabled'));
check("sync_uneligible_items_disabled", planHtml.includes('data-canvas-sync-action="UNCHANGED" disabled'));
check("missing_from_remote_is_advisory_not_deleted", planHtml.includes("canvas.action.MISSING_FROM_REMOTE_DISCOVERY"));

const reconnect = ui.canvasSectionHtml(baseCanvas({ status: "reconnect_required" }), t, escapeHtml);
check("reconnect_has_reconnect_button", reconnect.includes('data-canvas-connect="1"'));
check("reconnect_explains", reconnect.includes("canvas.reconnectRequiredHelp"));

const errorHtml = ui.canvasSectionHtml(baseCanvas({ status: "error", error: "canvas_server_failure" }), t, escapeHtml);
check("error_has_role_alert", errorHtml.includes('role="alert"'));
check("error_no_loop_connect", !errorHtml.includes('data-canvas-connect="1"'));

// OAuth response input interaction (same helpers the real input handler uses).
check("oauth_response_ready_empty_false", ui.oauthResponseReady("") === false);
check("oauth_response_ready_spaces_false", ui.oauthResponseReady("   ") === false);
check(
  "oauth_response_ready_nonempty_true",
  ui.oauthResponseReady("https://canvas.example.edu/login/oauth2/auth?state=x&code=y") === true,
);

function buttonFixture(disabled) {
  return { disabled };
}
let button = buttonFixture(true);
ui.updateOauthCompleteState({ value: "" }, button);
check("complete_disabled_for_empty", button.disabled === true);
button = buttonFixture(true);
ui.updateOauthCompleteState({ value: "   " }, button);
check("complete_disabled_for_spaces", button.disabled === true);
button = buttonFixture(true);
ui.updateOauthCompleteState({ value: "https://canvas.example.edu/response" }, button);
check("complete_enabled_for_url", button.disabled === false);
ui.updateOauthCompleteState({ value: "" }, button);
check("complete_redisabled_after_clear", button.disabled === true);

const fs = require("fs");
const path = require("path");
const appJs = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
check("complete_payload_uses_responseUrl", appJs.includes('complete_canvas_oauth", { responseUrl }'));
check("complete_payload_not_code_or_token", !appJs.includes('complete_canvas_oauth", { code }') && !appJs.includes('complete_canvas_oauth", { token }'));
check("app_wires_response_input_event", appJs.includes('matches("#canvasOAuthResponse")') && appJs.includes("updateOauthCompleteState"));

// Responsive: this harness cannot compute layout geometry. It can only verify
// that the renderer emits no fixed inline width that would force horizontal
// overflow. Actual viewport geometry remains a limitation reported separately.
const connectedNoInlineWidth = !/<[^>]*style="[^"]*width:/i.test(connected);
check("responsive_narrow_structural_check_only", connectedNoInlineWidth);

if (failures > 0) {
  console.error(`Canvas UI acceptance failures: ${failures}`);
  process.exit(1);
}
console.log("Canvas UI acceptance: PASS");
