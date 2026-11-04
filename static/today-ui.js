(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.StudyHubTodayUI = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Open-in-Canvas security boundary: the renderer only renders `html_url` that
  // the backend already normalized to a same-origin HTTPS URL. Any unsafe URL
  // is stored as NULL by `normalize_canvas_assignment_html_url`, and the native
  // `open_canvas_url` command re-validates exact origin (scheme/host/port,
  // no userinfo/fragment, bounded length) before opening the system browser.

  const DEADLINE_SECTIONS = [
    ["overdue", "today.overdue"],
    ["due_today", "today.dueToday"],
    ["due_tomorrow", "today.dueTomorrow"],
    ["upcoming", "today.upcoming"],
  ];

  function assignmentCard(item, t, escapeHtml) {
    const due = item.due_at || "";
    const status = t("today.status." + item.bucket, { default: item.bucket });
    const open = item.html_url
      ? '<a class="button secondary" data-today-open="' + escapeHtml(item.html_url) + '">' + escapeHtml(t("today.openInCanvas")) + "</a>"
      : "";
    return (
      '<div class="today-card">' +
      '<div class="today-card-main">' +
      '<span class="muted">' + escapeHtml(item.course_code || item.course_name || "") + "</span>" +
      "<strong>" + escapeHtml(item.title || "") + "</strong>" +
      (due ? '<time datetime="' + escapeHtml(due) + '">' + escapeHtml(t("today.dueAt", { time: due })) + "</time>" : "") +
      '<span class="chip">' + escapeHtml(status) + "</span>" +
      "</div>" +
      open +
      "</div>"
    );
  }

  function reviewCard(item, t, escapeHtml) {
    return (
      '<div class="today-card">' +
      '<span class="muted">' + escapeHtml(item.course_code || "") + "</span>" +
      "<strong>" + escapeHtml(item.display_name || item.filename || "") + "</strong>" +
      '<button class="button secondary" type="button" data-today-review="' + escapeHtml(item.id) + '">' + escapeHtml(t("today.startReview")) + "</button>" +
      "</div>"
    );
  }

  function todaySectionHtml(today, t, escapeHtml) {
    const c = today || {};
    const heading = '<h1 id="todayHeading">' + escapeHtml(t("today.heading")) + "</h1>";
    let body = "";
    if (c.state === "scope_upgrade_required") {
      body =
        '<div id="todayStatus" role="status" aria-live="polite">' + escapeHtml(t("today.scopeUpgradeRequired")) + "</div>" +
        '<button class="button primary" type="button" data-today-reconnect="1">' + escapeHtml(t("today.reconnectForAssignments")) + "</button>";
    } else if (c.state === "canvas_disconnected") {
      body = '<div id="todayStatus" role="status" aria-live="polite">' + escapeHtml(t("today.canvasDisconnected")) + "</div>";
    } else if (c.state === "loading") {
      body = '<div id="todayStatus" role="status" aria-live="polite" aria-busy="true">' + escapeHtml(t("today.loading")) + "</div>";
    } else {
      const sections = c.sections || {};
      body =
        '<div class="today-toolbar">' +
        '<button class="button primary" type="button" data-today-refresh="1"' + (c.refreshing ? ' disabled aria-busy="true"' : "") + ">" + escapeHtml(t("today.refreshAssignments")) + "</button>" +
        (c.last_refreshed_at ? '<span class="muted">' + escapeHtml(t("today.lastRefreshed", { time: c.last_refreshed_at })) + "</span>" : "") +
        "</div>" +
        DEADLINE_SECTIONS.map(function (pair) {
          const items = Array.isArray(sections[pair[0]]) ? sections[pair[0]] : [];
          return (
            '<section class="today-group" aria-labelledby="today-' + pair[0] + '">' +
            '<h2 id="today-' + pair[0] + '">' + escapeHtml(t(pair[1])) + "</h2>" +
            (items.length
              ? items.map(function (item) { return assignmentCard(item, t, escapeHtml); }).join("")
              : '<p class="muted">' + escapeHtml(t("today.empty")) + "</p>") +
            "</section>"
          );
        }).join("") +
        '<section class="today-group" aria-labelledby="today-review">' +
        '<h2 id="today-review">' + escapeHtml(t("today.reviewDue")) + "</h2>" +
        ((Array.isArray(sections.review) && sections.review.length)
          ? sections.review.map(function (item) { return reviewCard(item, t, escapeHtml); }).join("")
          : '<p class="muted">' + escapeHtml(t("today.empty")) + "</p>") +
        "</section>";
    }
    return (
      '<section class="view today-view" id="todayView" aria-labelledby="todayHeading">' +
      heading +
      (c.error ? '<div class="notice error" role="alert">' + escapeHtml(c.error) + "</div>" : "") +
      body +
      "</section>"
    );
  }

  return {
    assignmentCard: assignmentCard,
    reviewCard: reviewCard,
    todaySectionHtml: todaySectionHtml,
  };
});
