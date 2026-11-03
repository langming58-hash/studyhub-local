(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.StudyHubCanvasUI = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ELIGIBLE_SYNC_ACTIONS = ["ADD", "POTENTIAL_CONTENT_CHANGE", "METADATA_CHANGE"];

  function eligibleSyncAction(action) {
    return ELIGIBLE_SYNC_ACTIONS.includes(action);
  }

  function syncActionEligibleIds(actions) {
    return (Array.isArray(actions) ? actions : [])
      .filter((action) => eligibleSyncAction(action.action))
      .map((action) => String(action.remote_file_stable_id));
  }

  function canApplySync(selectedIds, actions) {
    const selected = new Set(selectedIds);
    return syncActionEligibleIds(actions).some((id) => selected.has(id));
  }

  function oauthResponseReady(value) {
    return typeof value === "string" && value.trim().length > 0;
  }

  function updateOauthCompleteState(input, button) {
    if (input && button) button.disabled = !oauthResponseReady(input.value);
  }

  function canvasCourseCard(course, selectedIds, escapeHtml) {
    const id = "canvas-course-" + String(course.remote_course_id).replace(/[^a-zA-Z0-9_-]/g, "");
    const selected = selectedIds.has(String(course.remote_course_id));
    const label = [course.name, course.course_code, course.term_name].filter(Boolean).join(" · ");
    return [
      '<label class="canvas-course-row" for="' + id + '">',
      '  <input type="checkbox" id="' + id + '" data-canvas-course="' + escapeHtml(course.remote_course_id) + '"' + (selected ? " checked" : "") + " />",
      "  <span>" + escapeHtml(label || course.name) + "</span>",
      '  <span class="muted">' + escapeHtml(course.workflow_state || "") + "</span>",
      "</label>",
    ].join("");
  }

  function canvasSyncPlanHtml(plan, selectedIds, t, escapeHtml) {
    const actions = Array.isArray(plan.actions) ? plan.actions : [];
    return actions
      .map(function (action) {
        const id =
          "canvas-sync-" +
          String(action.remote_file_stable_id || action.remote_file_id || "").replace(
            /[^a-zA-Z0-9_-]/g,
            "",
          );
        const eligible = eligibleSyncAction(action.action);
        const checked = selectedIds.has(String(action.remote_file_stable_id));
        return [
          '<label class="canvas-course-row" for="' + id + '">',
          '  <input type="checkbox" id="' + id + '" data-canvas-sync-item="' + escapeHtml(action.remote_file_stable_id) + '" data-canvas-sync-action="' + escapeHtml(action.action) + '"' + (eligible && checked ? " checked" : "") + (eligible ? "" : " disabled") + " />",
          "  <span>" + escapeHtml(action.display_name || action.filename || "") + "</span>",
          '  <span class="muted">' + escapeHtml(t("canvas.action." + action.action)) + "</span>",
          "</label>",
        ].join("");
      })
      .join("") || '<p class="muted">' + escapeHtml(t("canvas.noSyncActions")) + "</p>";
  }

  // Internal: render the whole Canvas section from a pure canvas-state object.
  function canvasSectionHtml(canvas, t, escapeHtml) {
    const c = canvas || {};
    const heading =
      '<p class="eyebrow">' + escapeHtml(t("canvas.eyebrow")) + "</p>" +
      '<h2 id="canvasHeading">' + escapeHtml(t("canvas.heading")) + "</h2>";
    let body = "";

    if (c.status === "unavailable") {
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite">' + escapeHtml(t("canvas.unavailable")) + "</div>" +
        '<p class="muted">' + escapeHtml(t("canvas.unavailableHelp")) + "</p>";
    } else if (c.status === "not_connected") {
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite">' + escapeHtml(t("canvas.notConnected")) + "</div>" +
        '<div class="settings-actions"><button class="button primary" type="button" data-canvas-connect="1">' + escapeHtml(t("canvas.connect")) + "</button></div>" +
        '<p class="muted">' + escapeHtml(t("canvas.privacyHelp")) + "</p>";
    } else if (c.status === "connecting") {
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite" aria-busy="true">' + escapeHtml(t("canvas.connecting")) + "</div>" +
        '<div id="canvasOAuthResponseStep">' +
        '<label class="settings-field">' + escapeHtml(t("canvas.responseLabel")) +
        '<input id="canvasOAuthResponse" type="text" autocomplete="off" placeholder="' + escapeHtml(t("canvas.responsePlaceholder")) + '" /></label>' +
        '<div class="settings-actions">' +
        '<button class="button primary" type="button" id="canvasOAuthComplete" data-canvas-complete="1" disabled>' + escapeHtml(t("canvas.complete")) + "</button>" +
        '<button class="button secondary" type="button" data-canvas-cancel="1">' + escapeHtml(t("common.cancel")) + "</button>" +
        "</div></div>";
    } else if (c.status === "connected") {
      const display = c.displayName
        ? '<span class="canvas-identity">' + escapeHtml(c.displayName) + "</span>"
        : "";
      const busy = c.busy ? ' disabled aria-busy="true"' : "";
      const courses = Array.isArray(c.courses)
        ? c.courses.map(function (course) {
            return canvasCourseCard(course, c.selectedIds || new Set(), escapeHtml);
          }).join("")
        : "";
      const selectionPreview = c.selectionPreview
        ? '<div id="canvasSelectionPreview" class="notice info" role="status" aria-live="polite">' +
          escapeHtml(
            t("canvas.selectionPreview", {
              added: (c.selectionPreview.select || []).length,
              removed: (c.selectionPreview.remove || []).length,
            }),
          ) +
          "</div>"
        : "";
      const syncPlan = c.syncPlan
        ? '<div id="canvasSyncPlan" class="settings-list compact" role="group" aria-label="' +
          escapeHtml(t("canvas.syncPlan")) +
          '">' +
          canvasSyncPlanHtml(c.syncPlan, c.syncSelectedIds || new Set(), t, escapeHtml) +
          "</div>"
        : "";
      const applySyncButton = c.syncPlan
        ? '<button class="button primary" type="button" data-canvas-sync-apply="1"' +
          (canApplySync(c.syncSelectedIds || new Set(), c.syncPlan.actions || []) ? "" : " disabled") +
          ">" +
          escapeHtml(t("canvas.applySync")) +
          "</button>"
        : "";
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite">' +
        escapeHtml(t("status.connected")) +
        display +
        "</div>" +
        (c.origin ? '<p class="muted">' + escapeHtml(c.origin) + "</p>" : "") +
        '<div class="settings-actions">' +
        '<button class="button secondary" type="button" data-canvas-refresh-courses="1"' + busy + ">" + escapeHtml(t("canvas.refreshCourses")) + "</button>" +
        '<button class="button secondary" type="button" data-canvas-sync-preview="1"' + busy + ">" + escapeHtml(t("canvas.syncNow")) + "</button>" +
        '<button class="button danger" type="button" data-canvas-disconnect="1">' + escapeHtml(t("canvas.disconnect")) + "</button>" +
        "</div>" +
        '<div id="canvasDiscovery">' +
        '<div class="canvas-toolbar"><label class="settings-field inline">' +
        escapeHtml(t("canvas.enrollment")) +
        '<select id="canvasEnrollment" data-canvas-enrollment="1">' +
        '<option value="active"' + (c.enrollmentState === "active" ? " selected" : "") + ">" + escapeHtml(t("canvas.active")) + "</option>" +
        '<option value="completed"' + (c.enrollmentState === "completed" ? " selected" : "") + ">" + escapeHtml(t("canvas.completed")) + "</option>" +
        '<option value="invited_or_pending"' + (c.enrollmentState === "invited_or_pending" ? " selected" : "") + ">" + escapeHtml(t("canvas.pending")) + "</option>" +
        "</select></label></div>" +
        '<div id="canvasCourseList" class="settings-list compact" role="group" aria-label="' + escapeHtml(t("canvas.courses")) + '">' +
        (courses || '<p class="muted">' + escapeHtml(t("canvas.noCourses")) + "</p>") +
        "</div>" +
        selectionPreview +
        '<div class="settings-actions">' +
        '<button class="button secondary" type="button" data-canvas-preview-selection="1"' + busy + ">" + escapeHtml(t("canvas.previewSelection")) + "</button>" +
        '<button class="button primary" type="button" data-canvas-apply-selection="1"' + (c.selectionPreview ? "" : " disabled") + ">" + escapeHtml(t("canvas.applySelection")) + "</button>" +
        "</div>" +
        syncPlan +
        (applySyncButton ? '<div class="settings-actions">' + applySyncButton + "</div>" : "") +
        "</div>";
    } else if (c.status === "reconnect_required") {
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite">' + escapeHtml(t("canvas.reconnectRequired")) + "</div>" +
        '<p class="muted">' + escapeHtml(t("canvas.reconnectRequiredHelp")) + "</p>" +
        '<div class="settings-actions"><button class="button primary" type="button" data-canvas-connect="1">' + escapeHtml(t("canvas.reconnect")) + "</button></div>";
    } else {
      body =
        '<div id="canvasStatus" class="settings-status" role="status" aria-live="polite">' + escapeHtml(t("canvas.error")) + "</div>" +
        '<div class="settings-actions"><button class="button secondary" type="button" data-canvas-refresh-status="1">' + escapeHtml(t("common.retry")) + "</button></div>";
    }

    return (
      '<section class="settings-card" id="canvasSection" aria-labelledby="canvasHeading">' +
      heading +
      (c.error ? '<div class="notice error" role="alert">' + escapeHtml(c.error) + "</div>" : "") +
      body +
      "</section>"
    );
  }

  return {
    eligibleSyncAction: eligibleSyncAction,
    syncActionEligibleIds: syncActionEligibleIds,
    canApplySync: canApplySync,
    oauthResponseReady: oauthResponseReady,
    updateOauthCompleteState: updateOauthCompleteState,
    canvasCourseCard: canvasCourseCard,
    canvasSyncPlanHtml: canvasSyncPlanHtml,
    canvasSectionHtml: canvasSectionHtml,
  };
});
