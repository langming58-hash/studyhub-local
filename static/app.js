const state = {
  courses: [],
  archivedCourses: [],
  terms: [],
  materialTypes: [],
  weeksByCourse: new Map(),
  health: null,
  preflight: null,
  view: "home",
  selectedCourseId: null,
  selectedWeek: "",
  selectedFile: null,
  csrfToken: "",
  weekFiles: [],
  weekFilter: "all",
  weekSort: "section",
  selectedMaterialIds: new Set(),
  nativeImportPaths: [],
  duplicateImport: null,
  studyMode: "plan",
  searchFiltersOpen: false,
  sidebarCollapsed: localStorage.getItem("studyhub.sidebarCollapsed") === "true",
  ask: {
    context: {},
    scope: "course",
    messages: [],
    conversationId: Number(localStorage.getItem("studyhub.currentConversationId") || 0) || null,
    conversationTitle: "",
    history: [],
    historyOpen: false,
    mode: localStorage.getItem("studyhub.aiMode") || "compact",
    railTab: "context",
    lastContextKey: "",
    draft: localStorage.getItem("studyhub.askDraft") || "",
  },
};

const $ = (selector) => document.querySelector(selector);
const view = $("#view");
const i18n = window.StudyHubI18n;
const t = (key, vars = {}) => i18n.t(key, vars);
const materialTypeLabel = (value) => i18n.materialType(value);
const materialTypeValue = (value) => i18n.catalogs.en[`material.${String(value || "other").toLowerCase()}`] || String(value || "Other");
const learningUnitLabel = (value, kind = "week") => i18n.learningUnit(value, kind);
let restoringRoute = false;
let appLoaded = false;

function desktopInvoke(command, args = {}) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!state.health?.desktopMode || typeof invoke !== "function") return null;
  return invoke(command, args);
}

function routeHash(routeState = {}) {
  const viewName = routeState.view || "home";
  if (viewName === "course" && routeState.courseId) return `#/course/${encodeURIComponent(routeState.courseId)}`;
  if (viewName === "week" && routeState.courseId && routeState.week) {
    return `#/week/${encodeURIComponent(routeState.courseId)}/${encodeURIComponent(routeState.week)}`;
  }
  if (viewName === "file" && routeState.fileId) {
    const page = Number(routeState.page || 0);
    return page ? `#/file/${encodeURIComponent(routeState.fileId)}/${encodeURIComponent(page)}` : `#/file/${encodeURIComponent(routeState.fileId)}`;
  }
  if (viewName === "study" && routeState.mode) return `#/study/${encodeURIComponent(routeState.mode)}`;
  return `#/${encodeURIComponent(viewName)}`;
}

function recordRoute(routeState = {}, replace = false) {
  if (restoringRoute || !appLoaded) return;
  const hash = routeHash(routeState);
  if (window.location.hash === hash) return;
  history[replace ? "replaceState" : "pushState"]({ studyhub: true, ...routeState }, "", hash);
}

function routeFromLocation() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").map((part) => decodeURIComponent(part || ""));
  if (parts[0] === "course" && parts[1]) return { view: "course", courseId: Number(parts[1]) };
  if (parts[0] === "week" && parts[1] && parts[2]) return { view: "week", courseId: Number(parts[1]), week: parts[2] };
  if (parts[0] === "file" && parts[1]) return { view: "file", fileId: Number(parts[1]), page: Number(parts[2] || 0) };
  if (parts[0] === "study") return { view: "study", mode: parts[1] || "plan" };
  if (["home", "courses", "search", "ai", "settings"].includes(parts[0])) return { view: parts[0] };
  return { view: "home" };
}

async function restoreRouteFromLocation() {
  const next = routeFromLocation();
  restoringRoute = true;
  try {
    if (next.view === "course" && next.courseId) return await renderCourse(next.courseId);
    if (next.view === "week" && next.courseId && next.week) return await renderWeek(next.courseId, next.week);
    if (next.view === "file" && next.fileId) return await route("file", { fileId: next.fileId, page: next.page, history: false });
    if (next.view === "study") return await renderStudy(next.mode || "plan");
    return await route(next.view || "home", { history: false });
  } finally {
    restoringRoute = false;
  }
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (method !== "GET" && state.csrfToken) {
    headers.set("X-StudyHub-CSRF", state.csrfToken);
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function postJson(path, body = {}) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setTitle(title, eyebrow = "") {
  $("#pageTitle").textContent = title;
  const label = $("#pageEyebrow");
  if (label) {
    label.textContent = eyebrow;
    label.hidden = !eyebrow;
  }
  setPageActions(false);
}

function setPageActions(visible) {
  const upload = $("#uploadBtn");
  const scan = $("#scanBtn");
  if (upload) upload.hidden = !visible;
  if (scan) scan.hidden = !visible;
}

function applySidebarState() {
  $("#appShell")?.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  const toggle = $("#sidebarToggle");
  if (toggle) {
    toggle.textContent = state.sidebarCollapsed ? "›" : "‹";
    const label = t(state.sidebarCollapsed ? "sidebar.expand" : "sidebar.collapse");
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  }
}

function setSidebarCollapsed(collapsed) {
  state.sidebarCollapsed = collapsed;
  localStorage.setItem("studyhub.sidebarCollapsed", collapsed ? "true" : "false");
  applySidebarState();
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.hidden = true;
  }, 2200);
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function statusLabel(value) {
  const key = {
    Available: "status.available",
    Connected: "status.connected",
    Configured: "status.configured",
    Missing: "status.missing",
    "Not configured": "status.notConfigured",
    "Not ready": "status.notReady",
    Ready: "status.ready",
    Unavailable: "status.unavailable",
  }[String(value || "")];
  return key ? t(key) : String(value || t("common.unknown"));
}

function detectQuestionNumber(text = "") {
  const match = String(text).match(/\bQ(?:uestion)?\.?\s*(\d{1,3}|[A-Z])\b|题\s*(\d{1,3})/i);
  const value = match?.[1] || match?.[2] || "";
  return value ? `Q${value.toUpperCase()}` : "";
}

function defaultScopeForContext(context = {}) {
  if (context.questionId || context.questionNumber) return "question";
  if (context.fileId) return "file";
  if (context.week) return "week";
  return "course";
}

function contextKey(context = {}) {
  return [
    context.course || "",
    context.week || "",
    context.fileId || context.file || "",
    context.exerciseType || "",
    context.questionId || context.questionNumber || "",
  ].join("|");
}

function setAskContext(context = {}, options = {}) {
  const nextContext = { ...context };
  const nextKey = contextKey(nextContext);
  if (state.ask.lastContextKey && state.ask.lastContextKey !== nextKey) {
    state.ask.messages.push({
      role: "system",
      text: t("ai.contextChanged"),
      context: { ...nextContext },
    });
    if (!options.keepConversation) {
      state.ask.conversationId = null;
      state.ask.conversationTitle = "";
      localStorage.removeItem("studyhub.currentConversationId");
    }
  }
  state.ask.context = nextContext;
  state.ask.scope = options.scope || defaultScopeForContext(nextContext);
  state.ask.lastContextKey = nextKey;
  state.ask.draft = options.prompt || "";
}

function currentCourseContext(courseId) {
  const course = courseById(courseId);
  return course ? { course: course.code, courseId: course.id, displayCourse: courseLabel(course) } : {};
}

function currentWeekContext(courseId, weekLabel) {
  const course = courseById(courseId);
  const weekNumber = Number(String(weekLabel || "").match(/\d+/)?.[0] || 0) || undefined;
  return { course: course?.code, courseId, displayCourse: courseLabel(course), week: weekLabel, weekNumber };
}

function fileAskContext(file) {
  return {
    fileId: file.id,
    course: file.course_code,
    displayCourse: courseLabel(file),
    week: file.week_label,
    weekNumber: file.week_number,
    file: file.filename,
    materialType: file.category,
    category: file.category,
    exerciseType: file.exercise_type || file.category,
  };
}

function questionAskContext(row) {
  return {
    questionId: row.id,
    fileId: row.source_file_id,
    course: row.course_code,
    displayCourse: courseLabel(row),
    week: row.week_label,
    exerciseType: row.exercise_type,
    file: row.filename,
    questionNumber: row.question_number,
  };
}

function askContextLines(context = state.ask.context) {
  const lines = [];
  if (context.course || context.displayCourse) lines.push(context.displayCourse || context.course);
  if (context.week) lines.push(context.week);
  if (context.exerciseType && context.exerciseType !== context.materialType) lines.push(context.exerciseType);
  if (context.materialType) lines.push(context.materialType);
  if (context.file) lines.push(context.file);
  if (context.questionNumber) lines.push(context.questionNumber);
  return lines.length ? lines : [t("ai.noCourse")];
}

function contextForScope(context = state.ask.context, scope = state.ask.scope) {
  const base = { ...context };
  if (scope === "course") {
    return { course: base.course, courseId: base.courseId, displayCourse: base.displayCourse };
  }
  if (scope === "week") {
    return { course: base.course, courseId: base.courseId, displayCourse: base.displayCourse, week: base.week, weekNumber: base.weekNumber };
  }
  if (scope === "file") {
    return {
      course: base.course,
      courseId: base.courseId,
      displayCourse: base.displayCourse,
      week: base.week,
      weekNumber: base.weekNumber,
      fileId: base.fileId,
      file: base.file,
      materialType: base.materialType,
      category: base.category,
      exerciseType: base.exerciseType,
    };
  }
  return base;
}

function availableScopes(context = state.ask.context) {
  return [
    { value: "question", label: t("ai.currentQuestion"), enabled: Boolean(context.questionId || context.questionNumber) },
    { value: "file", label: t("ai.currentFile"), enabled: Boolean(context.fileId) },
    { value: "week", label: t("ai.currentWeek"), enabled: Boolean(context.week) },
    { value: "course", label: t("ai.currentCourse"), enabled: Boolean(context.course) },
  ];
}

function quickActionsForContext(context = state.ask.context) {
  if (!context.course) return [];
  if (context.questionId || context.questionNumber) {
    return [t("ai.explainQuestion"), t("ai.whatAsking"), t("ai.checkAnswer")];
  }
  if (context.fileId) {
    const actions = [t("ai.explainFile"), t("ai.summarize")];
    if ((context.materialType || "").toLowerCase().includes("slide") || /\.pptx?$/i.test(context.file || "")) actions.push(t("ai.explainPage"));
    return actions;
  }
  if (context.week) {
    return [t("ai.explainWeek"), t("ai.keyConcepts"), t("ai.terminology"), t("ai.prepareTutorial")];
  }
  return [t("ai.keyConcepts"), t("ai.terminology")];
}

function courseById(id) {
  return state.courses.find((course) => Number(course.id) === Number(id));
}

function courseLabel(item = {}) {
  return item.display_code || item.display_course_code || item.code || item.course_code || "";
}

function scopeLabel(scope = state.ask.scope) {
  return {
    question: t("ai.thisQuestion"),
    file: t("ai.thisFile"),
    week: t("ai.thisWeek"),
    course: t("ai.thisCourse"),
    selected: t("ai.selectedSources"),
  }[scope] || t("ai.thisCourse");
}

function saveAskDraft(value) {
  state.ask.draft = value;
  localStorage.setItem("studyhub.askDraft", value);
}

function setAiMode(mode) {
  state.ask.mode = mode;
  localStorage.setItem("studyhub.aiMode", mode);
}

function renderMarkdown(text = "") {
  return window.StudyHubAIRenderer?.renderMarkdown
    ? window.StudyHubAIRenderer.renderMarkdown(text)
    : `<p>${escapeHtml(text)}</p>`;
}

function hydrateMessages(messages = []) {
  state.ask.messages = messages.map((message) => ({
    role: message.role,
    text: message.body || message.text || "",
    status: message.status || "",
    sources: message.sources || [],
    questions: message.questions || [],
    solutions: message.solutions || [],
    createdAt: message.created_at || "",
  }));
}

async function loadConversationList(query = "") {
  state.ask.history = await api(`/api/conversations?q=${encodeURIComponent(query)}`);
  return state.ask.history;
}

async function reopenConversation(id) {
  const data = await api(`/api/conversation?id=${id}`);
  state.ask.conversationId = data.conversation.id;
  state.ask.conversationTitle = data.conversation.title;
  state.ask.context = data.conversation.context || {};
  state.ask.scope = data.conversation.scope || defaultScopeForContext(state.ask.context);
  state.ask.lastContextKey = contextKey(state.ask.context);
  localStorage.setItem("studyhub.currentConversationId", String(data.conversation.id));
  hydrateMessages(data.messages || []);
  state.ask.historyOpen = false;
  route("ai");
}

async function createConversation(context = state.ask.context, scope = state.ask.scope) {
  const data = await api("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "create", context, scope, title: t("ai.newConversationTitle") }),
  });
  state.ask.conversationId = data.conversation.id;
  state.ask.conversationTitle = data.conversation.title;
  hydrateMessages([]);
  localStorage.setItem("studyhub.currentConversationId", String(data.conversation.id));
  await loadConversationList();
}

function groupConversations(rows = []) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 60 * 60 * 1000;
  return rows.reduce((groups, row) => {
    const time = new Date(row.updated_at || row.created_at).getTime();
    const label = time >= startOfToday ? "Today" : time >= startOfToday - day ? "Yesterday" : time >= startOfToday - 7 * day ? "Previous 7 days" : "Older";
    groups[label] ||= [];
    groups[label].push(row);
    return groups;
  }, {});
}

function weeksFor(courseId) {
  return state.weeksByCourse.get(Number(courseId)) || [];
}

function latestWeek(course) {
  const weeks = weeksFor(course.id).filter((week) => week.has_materials);
  return weeks.at(-1) || weeksFor(course.id)[0];
}

function fileMetaLine(file = {}) {
  return [courseLabel(file), learningUnitLabel(file.week_label), materialTypeLabel(file.material_type || file.category || file.exercise_type), file.source_label || file.source, formatSize(file.size)]
    .filter(Boolean)
    .join(" · ");
}

function weeksWithMaterialLabel(course) {
  const weeks = weeksFor(course.id);
  const count = weeks.filter((week) => week.has_materials).length;
  return t(count === 1 ? "week.materialCount" : "week.materialCountPlural", { count });
}

function rememberFile(file) {
  if (!file?.id) return;
  localStorage.setItem("studyhub.lastFileId", String(file.id));
  localStorage.setItem("studyhub.lastFileName", file.filename || "");
  localStorage.setItem("studyhub.lastFileMeta", fileMetaLine(file));
}

function actionPreflightItems() {
  return (state.preflight?.items || []).filter((item) => item.severity !== "info");
}

function firstRunPanel() {
  const dismissed = localStorage.getItem("studyhub.onboardingDismissed") === "true";
  const preflight = state.preflight || {};
  const needsAttention = actionPreflightItems().length > 0;
  if (dismissed && !needsAttention) return "";
  const desktopMode = Boolean(state.health?.desktopMode);
  return `
    <section class="onboarding-card" aria-labelledby="firstRunTitle">
      <div>
        <p class="eyebrow">${escapeHtml(t("onboarding.mode"))}</p>
        <h2 id="firstRunTitle">${escapeHtml(t("onboarding.title"))}</h2>
        <p class="muted">${escapeHtml(t("onboarding.body"))}</p>
      </div>
      <div class="onboarding-actions">
        <button class="button primary" data-create-first-course="1">${escapeHtml(t("onboarding.createCourse"))}</button>
        <button class="button secondary" ${desktopMode ? "data-import-first-folder=\"1\"" : "data-open-library-setup=\"1\""}>${escapeHtml(t("onboarding.importFolder"))}</button>
      </div>
    </section>
  `;
}

function recoveryCards(limit = 3, expandProblems = false) {
  const items = state.preflight?.items || [];
  const visible = limit === "all" ? items : items.filter((item) => item.severity !== "info").slice(0, limit);
  if (!visible.length) return "";
  return `
    <section class="recovery-list" aria-label="${escapeHtml(t("preflight.region"))}">
      ${visible.map((item) => recoveryCard(item, expandProblems)).join("")}
    </section>
  `;
}

function recoveryCard(item, expandProblems = false) {
  const tone = item.severity || "info";
  const key = (field, fallback) => {
    const name = `preflight.${item.code}.${field}`;
    return Object.hasOwn(i18n.catalogs.en, name) ? t(name) : fallback;
  };
  return `
    <details class="recovery-card ${escapeHtml(tone)}" ${expandProblems && (tone === "error" || tone === "warning") ? "open" : ""}>
      <summary>
        <span>${escapeHtml(key("title", item.title))}</span>
        <strong>${escapeHtml(t(`status.${tone}`))}</strong>
      </summary>
      <p><b>${escapeHtml(t("preflight.what"))}</b> ${escapeHtml(key("what", item.whatHappened))}</p>
      <p><b>${escapeHtml(t("preflight.impact"))}</b> ${escapeHtml(key("impact", item.impact))}</p>
      <p><b>${escapeHtml(t("preflight.next"))}</b> ${escapeHtml(key("next", item.nextStep))}</p>
      ${item.details ? `<pre>${escapeHtml(item.details)}</pre>` : ""}
    </details>
  `;
}

function librarySetupForm() {
  const desktopMode = Boolean(state.health?.desktopMode);
  return `
    <form class="library-setup-form" id="librarySetupForm">
      <label>${escapeHtml(t("settings.studyFolder"))}
        <input id="studyLibraryPathInput" placeholder="${desktopMode ? escapeHtml(t("settings.folderPlaceholder")) : "~/StudyLibrary"}" autocomplete="off" ${desktopMode ? "readonly" : ""} />
      </label>
      ${desktopMode ? `<button class="button primary" type="button" data-choose-study-folder="1">${escapeHtml(t("settings.chooseFolder"))}</button>` : `<button class="button primary" type="submit">${escapeHtml(t("settings.useFolder"))}</button>`}
      <p class="muted">${escapeHtml(t("settings.folderPrivacy"))}</p>
      <div id="librarySetupResult" class="notice compact" hidden></div>
    </form>
  `;
}

function extensionIcon(ext = "") {
  const value = ext.toLowerCase();
  if (value === ".pdf") return "PDF";
  if ([".doc", ".docx"].includes(value)) return "DOC";
  if ([".ppt", ".pptx"].includes(value)) return "PPT";
  if ([".xls", ".xlsx", ".csv", ".tsv"].includes(value)) return "XLS";
  if ([".py", ".r", ".ipynb"].includes(value)) return "CODE";
  return "FILE";
}

function studyStatusLabel(file) {
  if (Number(file.needs_review || 0)) return t("study.needsReview");
  return t(`study.status.${file.study_status || "not_started"}`);
}

function studyStatusBadge(file) {
  const status = Number(file.needs_review || 0) ? "review" : (file.study_status || "not_started");
  if (status === "not_started") return "";
  return `<span class="chip study-status ${escapeHtml(status)}">${escapeHtml(studyStatusLabel(file))}</span>`;
}

function fileCard(file, selectable = false) {
  const suspicious = file.suspicious ? `<span class="chip warn">${escapeHtml(file.suspicious)}</span>` : "";
  const missing = file.source_missing ? `<span class="chip warn">${escapeHtml(t("file.originalMissing"))}</span>` : "";
  const star = file.star_id ? "★" : "☆";
  return `
    <article class="file-row">
      ${selectable ? `<input class="material-check" type="checkbox" data-material-select="${file.id}" aria-label="${escapeHtml(t("common.select", { name: file.display_name || file.filename }))}" ${state.selectedMaterialIds.has(Number(file.id)) ? "checked" : ""} />` : ""}
      <button class="file-row-main" data-preview="${file.id}" title="${escapeHtml(file.filename)}">
        <span class="file-badge">${extensionIcon(file.extension)}</span>
        <span class="file-row-text">
          <strong class="file-name">${escapeHtml(file.display_name || file.filename)}</strong>
          <span class="muted">${escapeHtml(fileMetaLine(file))}</span>
          ${file.snippet ? `<span class="search-snippet">${escapeHtml(file.snippet)}</span>` : ""}
          <span class="chips inline-chips">${studyStatusBadge(file)}${missing}${suspicious}</span>
        </span>
      </button>
      <div class="file-row-actions">
        <button class="icon-button" data-star="${file.id}" aria-label="${escapeHtml(t("file.starLabel"))}" title="${escapeHtml(t("file.star"))}">${star}</button>
        <details class="row-more">
          <summary>${escapeHtml(t("common.more"))}</summary>
          <div>
            <button class="button secondary" data-ask-file="${file.id}">${escapeHtml(t("courses.askAi"))}</button>
            ${file.source_missing ? `<button class="button secondary" data-relink-material="${file.id}">${escapeHtml(t("file.locate"))}</button>` : `<button class="button secondary" data-open="${file.id}">${escapeHtml(t("file.openOriginal"))}</button>`}
            <button class="button secondary" data-rename-material="${file.id}" data-material-name="${escapeHtml(file.display_name || file.filename)}">${escapeHtml(t("file.rename"))}</button>
            <button class="button secondary" data-remove-material="${file.id}">${escapeHtml(t("file.remove"))}</button>
          </div>
        </details>
      </div>
    </article>
  `;
}

function legacyFileCard(file) {
  const officialClass = file.is_official ? "official" : "";
  const suspicious = file.suspicious ? `<span class="chip warn">${escapeHtml(file.suspicious)}</span>` : "";
  const star = file.star_id ? "★" : "☆";
  return `
    <article class="file-card">
      <header>
        <div class="file-title-row">
          <span class="file-badge">${extensionIcon(file.extension)}</span>
          <div>
            <div class="file-name">${escapeHtml(file.filename)}</div>
            <p class="muted path-text">${escapeHtml(file.rel_path || "")}</p>
          </div>
        </div>
        <button class="icon-button" data-star="${file.id}" aria-label="${escapeHtml(t("file.starLabel"))}">${star}</button>
      </header>
      <div class="chips">
        <span class="chip ${officialClass}">${escapeHtml(file.source_label || file.source || t("file.local"))}</span>
        <span class="chip">${escapeHtml(file.extension || "")}</span>
        <span class="chip">${formatSize(file.size)}</span>
        ${file.week_label ? `<span class="chip">${escapeHtml(file.week_label)}</span>` : ""}
        ${file.category ? `<span class="chip">${escapeHtml(file.category)}</span>` : ""}
        ${suspicious}
      </div>
      <div class="file-actions">
        <button class="button secondary" data-preview="${file.id}">Preview</button>
        <button class="button secondary" data-ask-file="${file.id}">Ask AI</button>
        <button class="button secondary" data-open="${file.id}">Open Original</button>
        <button class="button secondary" data-context="${file.id}">Context</button>
      </div>
    </article>
  `;
}

async function loadBase() {
  const session = await api("/api/session");
  state.csrfToken = session.csrfToken || state.csrfToken || "";
  state.health = await api("/api/health");
  state.preflight = await api("/api/preflight");
  state.terms = await api("/api/terms");
  state.materialTypes = (await api("/api/material-types")).types || [];
  const allCourses = await api("/api/courses?include_archived=1");
  state.courses = allCourses.filter((course) => !course.archived);
  state.archivedCourses = allCourses.filter((course) => course.archived);
  await Promise.all(
    state.courses.map(async (course) => {
      state.weeksByCourse.set(Number(course.id), await api(`/api/weeks?course_id=${course.id}`));
    }),
  );
  const libraryState = $("#libraryState");
  if (libraryState) {
    const needsAttention = actionPreflightItems().length > 0 || !state.health.studyLibraryConnected;
    libraryState.hidden = !needsAttention;
    libraryState.textContent = t(state.health.studyLibraryConnected ? "settings.libraryNeedsAttention" : "settings.libraryMissing");
  }
  if (Number(state.health.filesIndexed || 0) > 0 && actionPreflightItems().length === 0) {
    localStorage.setItem("studyhub.onboardingDismissed", "true");
  }
  applySidebarState();
  populateUploadCourses();
  populateCourseTerms();
  await resumePendingFirstRunAction();
  if (state.ask.conversationId && !state.ask.messages.length) {
    try {
      const data = await api(`/api/conversation?id=${state.ask.conversationId}`);
      state.ask.conversationTitle = data.conversation.title;
      state.ask.context = data.conversation.context || state.ask.context;
      state.ask.scope = data.conversation.scope || state.ask.scope;
      hydrateMessages(data.messages || []);
    } catch (_error) {
      state.ask.conversationId = null;
      localStorage.removeItem("studyhub.currentConversationId");
    }
  }
}

function renderCourseList() {}

function populateUploadCourses() {
  $("#uploadCourse").innerHTML = `<option value="0">${escapeHtml(t("courses.inbox"))} / ${escapeHtml(t("courses.unclassified"))}</option>${state.courses
    .map((course) => `<option value="${course.id}">${escapeHtml(courseLabel(course))}</option>`)
    .join("")}`;
  $("#uploadMaterialType").innerHTML = state.materialTypes.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(materialTypeLabel(type))}</option>`).join("");
  populateUploadWeeks();
}

function populateUploadWeeks() {
  const courseId = Number($("#uploadCourse").value || state.selectedCourseId || 0);
  const weeks = weeksFor(courseId);
  $("#uploadWeek").innerHTML = weeks.length
    ? weeks.map((week) => `<option value="${week.id}">${escapeHtml(learningUnitLabel(week.week_label, week.kind))}</option>`).join("")
    : `<option value="">${escapeHtml(t("courses.unclassified"))}</option>`;
}

function populateCourseTerms() {
  $("#courseTerm").innerHTML = state.terms.map((term) => `<option value="${term.id}">${escapeHtml(term.stable_id === "term_imported" ? t("settings.importedCourses") : term.name)}</option>`).join("");
}

async function resumePendingFirstRunAction() {
  if (!state.health?.desktopMode) return;
  const folder = localStorage.getItem("studyhub.pendingImportFolder");
  if (folder) {
    localStorage.removeItem("studyhub.pendingImportFolder");
    await postJson("/api/materials/import-folder", { path: folder });
    localStorage.setItem("studyhub.onboardingDismissed", "true");
    window.location.reload();
    return;
  }
  if (localStorage.getItem("studyhub.pendingCreateCourse") === "true") {
    localStorage.removeItem("studyhub.pendingCreateCourse");
    setTimeout(() => openCourseDialog(), 0);
  }
}

async function renderHome() {
  setTitle(t("nav.home"));
  const [recent, overview] = await Promise.all([api("/api/recent"), api("/api/study/overview")]);
  const lastFileId = Number(localStorage.getItem("studyhub.lastFileId") || 0);
  const continueFile = overview.queue.find((file) => Number(file.id) === lastFileId) || overview.queue[0] || recent[0];
  const activeCourses = state.courses.filter((course) => Number(course.file_count || 0) > 0);
  const progressByCourse = new Map(overview.courses.map((item) => [Number(item.course_id), item]));
  view.innerHTML = `
    ${firstRunPanel()}
    ${recoveryCards(2)}
    <section class="continue-panel">
      <div>
        <p class="eyebrow">${escapeHtml(t("home.continue"))}</p>
        <h2>${escapeHtml(continueFile?.filename || t("home.chooseMaterial"))}</h2>
        <p class="muted">${escapeHtml(continueFile ? fileMetaLine(continueFile) : t("home.openRecent"))}</p>
      </div>
      <div class="continue-actions">
        ${
          continueFile
            ? `<button class="button primary" data-preview="${continueFile.id}">${escapeHtml(t("home.continue"))}</button>`
            : `<button class="button primary" data-view="courses">${escapeHtml(t("home.browseCourses"))}</button>`
        }
      </div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head">
        <div>
          <h2>${escapeHtml(t("nav.courses"))}</h2>
        </div>
        <button class="button secondary" data-view="courses">${escapeHtml(t("common.viewAll"))}</button>
      </div>
      <div class="course-list-main">${activeCourses.length ? activeCourses.slice(0, 8).map((course) => courseSummary(course, progressByCourse.get(Number(course.id)))).join("") : empty(t("home.noCourses"))}</div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("home.recent"))}</p>
          <h2>${escapeHtml(t("home.files"))}</h2>
        </div>
        <button class="button secondary" data-view="search">${escapeHtml(t("nav.search"))}</button>
      </div>
      <div class="file-list">${recent.length ? recent.slice(0, 8).map((file) => fileCard(file)).join("") : empty(t("home.noFiles"))}</div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("home.review"))}</p>
          <h2>${escapeHtml(t("home.studyQueue"))}</h2>
        </div>
        <button class="button secondary" data-view="study" data-study-mode="plan">${escapeHtml(t("home.openStudy"))}</button>
      </div>
      ${overview.queue.length ? `<div class="file-list">${overview.queue.slice(0, 4).map((file) => fileCard(file)).join("")}</div>` : empty(t("study.queueEmpty"))}
    </section>
  `;
  bindLibrarySetupForm();
}

function bindLibrarySetupForm() {
  const form = $("#librarySetupForm");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = $("#librarySetupResult");
    const path = $("#studyLibraryPathInput").value.trim();
    result.hidden = false;
    result.textContent = t("toast.checkingFolder");
    try {
      const data = await api("/api/config/study-library", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      result.textContent = state.health?.desktopMode ? "Opening this study folder..." : (data.message || "Saved. Restart StudyHub to use this folder.");
      localStorage.setItem("studyhub.onboardingDismissed", "true");
      toast(t("toast.studyFolderSaved"));
      const restart = desktopInvoke("restart_backend");
      if (restart) await restart;
    } catch (error) {
      result.textContent = error.message;
    }
  });
}

function focusCourseCard(course) {
  const weeks = weeksFor(course.id);
  const done = weeks.filter((week) => week.has_materials).length;
  const pct = Math.round((done / Math.max(weeks.length, 1)) * 100);
  const week = latestWeek(course);
  return `
    <article class="focus-card" data-course="${course.id}">
      <div>
        <p class="muted">${escapeHtml(courseLabel(course))}</p>
        <h2>${escapeHtml(week ? learningUnitLabel(week.week_label, week.kind) : t("course.noWeek"))}</h2>
      </div>
      <div class="progress-track"><span style="width:${pct}%"></span></div>
      <div class="focus-meta">
        <span>${escapeHtml(t(done === 1 ? "week.materialCount" : "week.materialCountPlural", { count: done }))}</span>
        <button class="button secondary" data-course="${course.id}" data-week="${escapeHtml(week?.week_label || "")}">${escapeHtml(t("common.open"))}</button>
      </div>
    </article>
  `;
}

function courseSummary(course, progress = null) {
  const week = latestWeek(course);
  const pct = Number(progress?.progress_percent || 0);
  return `
    <article class="course-row">
      <button class="course-row-main" data-course="${course.id}">
        <strong>${escapeHtml(courseLabel(course))}</strong>
        <span>${escapeHtml(course.name || "")}</span>
        <span class="course-row-sub">${week ? escapeHtml(t("course.latestMaterial", { week: learningUnitLabel(week.week_label, week.kind) })) : escapeHtml(t("course.noFilesYet"))}</span>
        ${progress ? `<span class="mini-progress"><span style="width:${pct}%"></span></span>` : ""}
      </button>
      <div class="course-row-meta">
        <span>${progress ? escapeHtml(t("study.progressPercent", { percent: pct })) : escapeHtml(weeksWithMaterialLabel(course))}</span>
        <details class="row-more">
          <summary aria-label="${escapeHtml(t("courses.actions"))}">${escapeHtml(t("common.more"))}</summary>
          <div>
            <button class="button secondary" data-edit-course="${course.id}">${escapeHtml(t("courses.edit"))}</button>
            <button class="button secondary" data-archive-course="${course.id}">${escapeHtml(t("common.archive"))}</button>
            <button class="button secondary" data-remove-course="${course.id}">${escapeHtml(t("courses.remove"))}</button>
          </div>
        </details>
      </div>
    </article>
  `;
}

async function renderCourses() {
  setTitle(t("nav.courses"));
  setPageActions(true);
  const activeCourses = state.courses.filter((course) => !course.archived);
  const starred = await api("/api/starred");
  const inbox = await api("/api/inbox");
  state.selectedMaterialIds.clear();
  view.innerHTML = `
    <section class="library-hero">
      <div>
        <h2>${escapeHtml(t("courses.heroTitle"))}</h2>
        <p class="muted">${escapeHtml(t("courses.heroBody"))}</p>
      </div>
      <div class="library-actions">
        <button class="button secondary" data-view="search">${escapeHtml(t("courses.searchLibrary"))}</button>
        <button class="button secondary" data-import-course-folder="1">${escapeHtml(t("courses.importFolder"))}</button>
        <button class="button primary" data-new-course="1">${escapeHtml(t("courses.new"))}</button>
        <button class="button secondary" data-open-upload="1" data-course-id="0">${escapeHtml(t("courses.addInbox"))}</button>
        <button class="button primary" data-run-scan="1">${escapeHtml(t("courses.scan"))}</button>
      </div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head">
        <h2>${escapeHtml(t("nav.courses"))}</h2>
        <span class="muted">${escapeHtml(t(activeCourses.length === 1 ? "course.count" : "course.countPlural", { count: activeCourses.length }))}</span>
      </div>
      <div class="course-list-main">${activeCourses.length ? activeCourses.map(courseSummary).join("") : empty(t("home.noCourses"))}</div>
    </section>
    ${state.archivedCourses.length ? `
      <details class="section-block quiet-section">
        <summary><strong>${escapeHtml(t("courses.archived"))}</strong> <span class="muted">${state.archivedCourses.length}</span></summary>
        <div class="course-list-main">
          ${state.archivedCourses.map((course) => `
            <article class="course-row">
              <div class="course-row-main"><strong>${escapeHtml(courseLabel(course))}</strong><span>${escapeHtml(course.name || "")}</span></div>
              <button class="button secondary" data-restore-course="${course.id}">${escapeHtml(t("common.restore"))}</button>
            </article>
          `).join("")}
        </div>
      </details>
    ` : ""}
    <section class="section-block quiet-section">
      <div class="section-head">
        <h2>${escapeHtml(t("courses.starred"))}</h2>
        <button class="button secondary" data-view="search" data-show-starred="1">${escapeHtml(t("courses.findMore"))}</button>
      </div>
      <div class="file-list">${starred.length ? starred.slice(0, 8).map((file) => fileCard(file)).join("") : empty(t("courses.noStars"))}</div>
    </section>
    ${inbox.length ? `
      <section class="section-block quiet-section">
        <div class="section-head"><div><p class="eyebrow">${escapeHtml(t("courses.unclassified"))}</p><h2>${escapeHtml(t("courses.inbox"))}</h2></div><span class="muted">${inbox.length}</span></div>
        <div class="toolbar">
          <select id="inboxCourse" aria-label="${escapeHtml(t("aria.inboxCourse"))}">${state.courses.map((course) => `<option value="${course.id}">${escapeHtml(courseLabel(course))}</option>`).join("")}</select>
          <select id="inboxWeek" aria-label="${escapeHtml(t("aria.inboxWeek"))}"></select>
          <select id="inboxMaterialType">${state.materialTypes.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(materialTypeLabel(type))}</option>`).join("")}</select>
          <button class="button secondary" id="inboxAssign" disabled>${escapeHtml(t("courses.assignSelected"))}</button>
        </div>
        <div class="file-list">${inbox.map((file) => fileCard(file, true)).join("")}</div>
      </section>
    ` : ""}
  `;
  if (inbox.length) {
    populateInboxWeeks();
    $("#inboxCourse").addEventListener("change", populateInboxWeeks);
    $("#inboxAssign").addEventListener("click", classifyInbox);
  }
}

function populateInboxWeeks() {
  const courseId = Number($("#inboxCourse")?.value || 0);
  const select = $("#inboxWeek");
  if (!select) return;
  select.innerHTML = weeksFor(courseId).map((week) => `<option value="${week.id}">${escapeHtml(learningUnitLabel(week.week_label, week.kind))}</option>`).join("");
}

async function classifyInbox() {
  const ids = [...state.selectedMaterialIds];
  if (!ids.length) return;
  await postJson("/api/materials/manage", {
    action: "classify",
    ids,
    course_id: Number($("#inboxCourse").value),
    week_id: Number($("#inboxWeek").value),
    material_type: $("#inboxMaterialType").value,
  });
  await loadBase();
  await renderCourses();
  toast(t("toast.inboxAssigned"));
}

async function renderThisWeek() {
  setTitle("This Week");
  const blocks = await Promise.all(
    state.courses.map(async (course) => {
      const week = latestWeek(course);
      if (!week) return "";
      const files = await api(`/api/files?course_id=${course.id}&week=${encodeURIComponent(week.week_label)}`);
      return `
        <section class="section-block">
          <div class="section-head">
            <div>
              <p class="muted">${escapeHtml(courseLabel(course))}</p>
              <h2>${escapeHtml(week.week_label)}</h2>
            </div>
            <button class="button secondary" data-course="${course.id}" data-week="${escapeHtml(week.week_label)}">Open Week</button>
          </div>
          <div class="grid">${files.length ? files.slice(0, 6).map((file) => fileCard(file)).join("") : empty("No files ready for this week.")}</div>
        </section>
      `;
    }),
  );
  view.innerHTML = blocks.join("");
}

async function renderCourse(courseId) {
  const course = courseById(courseId);
  state.view = "course";
  state.selectedCourseId = courseId;
  recordRoute({ view: "course", courseId });
  setTitle(course?.name || courseLabel(course) || t("dialog.course"));
  const weeks = weeksFor(courseId);
  const [files, overview] = await Promise.all([
    api(`/api/files?course_id=${courseId}`),
    api(`/api/study/overview?course_id=${courseId}`),
  ]);
  const activeWeeks = weeks.filter((week) => week.has_materials);
  const progressByWeek = new Map(overview.weeks.map((item) => [item.week_label, item]));
  view.innerHTML = `
    <section class="course-header">
      <div>
        <p class="eyebrow">${escapeHtml(courseLabel(course))}</p>
        <p class="muted">${escapeHtml(t("study.courseProgress", { completed: overview.summary.completed, total: overview.summary.total }))}</p>
        <div class="progress-track course-progress" aria-label="${escapeHtml(t("study.progressPercent", { percent: overview.summary.progress_percent }))}"><span style="width:${overview.summary.progress_percent}%"></span></div>
      </div>
      <div class="course-actions">
        <button class="button secondary" data-add-week="${courseId}">${escapeHtml(t("courses.addWeek"))}</button>
        <button class="button secondary" data-open-upload="1" data-course-id="${courseId}">${escapeHtml(t("courses.addMaterial"))}</button>
        <button class="button secondary" data-edit-course="${courseId}">${escapeHtml(t("courses.editCourse"))}</button>
        <button class="button secondary" data-ask-course="${courseId}">${escapeHtml(t("courses.askAi"))}</button>
      </div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head">
        <h2>${escapeHtml(t("courses.weeks"))}</h2>
        <span class="muted">${activeWeeks.length} week${activeWeeks.length === 1 ? "" : "s"} with material</span>
      </div>
      <div class="week-list">
        ${weeks.map((week) => {
          const progress = progressByWeek.get(week.week_label);
          return `
          <article class="week-row ${week.has_materials ? "has-materials" : "empty-week"}">
            <button class="week-row-main" data-week="${week.week_label}" data-course="${courseId}">
              <strong>${escapeHtml(learningUnitLabel(week.week_label, week.kind))}</strong>
              <span class="muted">${progress ? escapeHtml(t("study.weekProgress", { completed: progress.completed, total: progress.total })) : escapeHtml(t(Number(week.file_count || 0) === 1 ? "week.fileCount" : "week.fileCountPlural", { count: week.file_count || 0 }))}</span>
              ${progress ? `<span class="mini-progress"><span style="width:${progress.progress_percent}%"></span></span>` : ""}
            </button>
            <details class="row-more">
              <summary aria-label="${escapeHtml(t("week.actions"))}">${escapeHtml(t("common.more"))}</summary>
              <div>
                <button class="button secondary" data-edit-week="${week.id}" data-course-id="${courseId}">${escapeHtml(t("common.rename"))}</button>
                ${week.has_materials ? "" : `<button class="button secondary" data-remove-week="${week.id}" data-course-id="${courseId}">${escapeHtml(t("common.remove"))}</button>`}
              </div>
            </details>
          </article>`;
        }).join("")}
      </div>
    </section>
    <section class="section-block quiet-section">
      <h2>${escapeHtml(t("courses.allFiles"))}</h2>
      <div class="file-list">${files.length ? files.slice(0, 24).map((file) => fileCard(file)).join("") : empty(t("courses.noFiles"))}</div>
    </section>
  `;
}

async function renderWeek(courseId, weekLabel) {
  const course = courseById(courseId);
  state.view = "week";
  state.selectedCourseId = courseId;
  state.selectedWeek = weekLabel;
  recordRoute({ view: "week", courseId, week: weekLabel });
  const [weekFiles, overview] = await Promise.all([
    api(`/api/files?course_id=${courseId}&week=${encodeURIComponent(weekLabel)}`),
    api(`/api/study/overview?course_id=${courseId}&week=${encodeURIComponent(weekLabel)}`),
  ]);
  state.weekFiles = weekFiles;
  state.weekFilter = "all";
  state.weekSort = "section";
  state.selectedMaterialIds.clear();
  setTitle(`${courseLabel(course) || t("dialog.course")} · ${learningUnitLabel(weekLabel)}`, t("week.workspace"));
  view.innerHTML = `
    <section class="week-header">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(course?.name || "")}</p>
          <h2>${escapeHtml(learningUnitLabel(weekLabel))}</h2>
          <p class="muted">${escapeHtml(t("study.weekProgress", { completed: overview.summary.completed, total: overview.summary.total }))}</p>
        </div>
        <div class="top-actions">
          <button class="button secondary" id="weekPreviewGpt">${escapeHtml(t("week.prepareAi"))}</button>
          <button class="button secondary" data-open-upload="1" data-course-id="${courseId}" data-week-label="${escapeHtml(weekLabel)}">${escapeHtml(t("courses.addMaterial"))}</button>
          <button class="button secondary" data-ask-week="${courseId}" data-week-label="${escapeHtml(weekLabel)}">${escapeHtml(t("courses.askAi"))}</button>
          <button class="button secondary" data-course="${courseId}">${escapeHtml(t("week.backCourse"))}</button>
        </div>
      </div>
      <div class="toolbar">
        <select id="weekFilter">
          <option value="all">${escapeHtml(t("week.allFiles"))}</option>
          <option value="01 Course Materials">${escapeHtml(t("week.courseMaterials"))}</option>
          <option value="02 Exercises">${escapeHtml(t("week.exercises"))}</option>
          ${["lecture", "tutorial", "workshop", "lab", "quiz"].map((type) => `<option value="${escapeHtml(materialTypeValue(type))}">${escapeHtml(materialTypeLabel(type))}</option>`).join("")}
          <option value="Practice">${escapeHtml(t("study.practice"))}</option>
          <option value="Revision">${escapeHtml(t("home.review"))}</option>
          <option value="official">${escapeHtml(t("week.officialOnly"))}</option>
          <option value="user">${escapeHtml(t("week.myWork"))}</option>
        </select>
        <select id="weekSort">
          <option value="section">${escapeHtml(t("week.section"))}</option>
          <option value="name">${escapeHtml(t("week.name"))}</option>
          <option value="type">${escapeHtml(t("week.type"))}</option>
          <option value="size">${escapeHtml(t("week.size"))}</option>
        </select>
        <span class="toolbar-spacer"></span>
        <select id="batchCourse" aria-label="${escapeHtml(t("aria.batchCourse"))}">${state.courses.map((item) => `<option value="${item.id}" ${Number(item.id) === Number(courseId) ? "selected" : ""}>${escapeHtml(courseLabel(item))}</option>`).join("")}</select>
        <select id="batchWeek" aria-label="${escapeHtml(t("aria.batchWeek"))}"></select>
        <select id="batchMaterialType" aria-label="${escapeHtml(t("dialog.materialType"))}">${state.materialTypes.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(materialTypeLabel(type))}</option>`).join("")}</select>
        <button class="button secondary" id="batchClassify" disabled>${escapeHtml(t("week.applySelected"))}</button>
        <button class="button secondary" id="batchStar" disabled>${escapeHtml(t("week.starSelected"))}</button>
        <button class="button secondary" id="batchRemove" disabled>${escapeHtml(t("week.removeSelected"))}</button>
      </div>
    </section>
    <section id="weekBridgePreview" class="notice" hidden></section>
    <section id="weekFiles" class="week-file-groups"></section>
  `;
  $("#weekFilter").addEventListener("change", () => {
    state.weekFilter = $("#weekFilter").value;
    renderWeekFiles();
  });
  $("#weekSort").addEventListener("change", () => {
    state.weekSort = $("#weekSort").value;
    renderWeekFiles();
  });
  $("#weekPreviewGpt").addEventListener("click", previewWeekWithGpt);
  populateBatchWeeks();
  $("#batchCourse").addEventListener("change", populateBatchWeeks);
  $("#batchClassify").addEventListener("click", () => batchManageMaterials("classify"));
  $("#batchStar").addEventListener("click", () => batchManageMaterials("star"));
  $("#batchRemove").addEventListener("click", () => batchManageMaterials("remove"));
  renderWeekFiles();
}

function renderWeekFiles() {
  let files = [...state.weekFiles];
  const filter = state.weekFilter;
  if (filter === "official") files = files.filter((file) => file.is_official);
  else if (filter === "user") files = files.filter((file) => !file.is_official);
  else if (filter !== "all") files = files.filter((file) => [file.section, file.category, file.exercise_type].includes(filter));

  files.sort((a, b) => {
    if (state.weekSort === "name") return a.filename.localeCompare(b.filename);
    if (state.weekSort === "type") return (a.extension || "").localeCompare(b.extension || "") || a.filename.localeCompare(b.filename);
    if (state.weekSort === "size") return b.size - a.size;
    return `${a.section || ""}${a.category || ""}${a.filename}`.localeCompare(`${b.section || ""}${b.category || ""}${b.filename}`);
  });

  if (!files.length) {
    $("#weekFiles").innerHTML = empty(t("week.noMatch"));
    return;
  }
  const isExercise = (file) => file.section === "02 Exercises" || /exercise|tutorial|workshop|lab|quiz|practice/i.test(`${file.section || ""} ${file.category || ""} ${file.exercise_type || ""}`);
  const isPersonal = (file) => /my_work|review/i.test(`${file.section || ""}`);
  const groups = [
    [t("week.courseMaterialsHeading"), files.filter((file) => !isPersonal(file) && (file.section === "01 Course Materials" || !isExercise(file)))],
    [t("week.exercisesHeading"), files.filter((file) => !isPersonal(file) && isExercise(file))],
    [t("week.myWorkHeading"), files.filter(isPersonal)],
  ];
  $("#weekFiles").innerHTML = groups
    .filter(([, rows]) => rows.length)
    .map((group) => fileGroup(group[0], group[1]))
    .join("");
}

function populateBatchWeeks() {
  const courseId = Number($("#batchCourse")?.value || state.selectedCourseId || 0);
  const select = $("#batchWeek");
  if (!select) return;
  select.innerHTML = weeksFor(courseId).map((week) => `<option value="${week.id}" ${week.week_label === state.selectedWeek ? "selected" : ""}>${escapeHtml(learningUnitLabel(week.week_label, week.kind))}</option>`).join("");
}

function fileGroup(title, rows) {
  return `
    <section class="section-block quiet-section">
      <div class="section-head">
        <h2>${escapeHtml(title)}</h2>
        <span class="muted">${escapeHtml(t(rows.length === 1 ? "week.fileCount" : "week.fileCountPlural", { count: rows.length }))}</span>
      </div>
      <div class="file-list">${rows.map((file) => fileCard(file, true)).join("")}</div>
    </section>
  `;
}

function countBy(items, fn) {
  return items.reduce((acc, item) => {
    const key = fn(item);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

async function renderSearch() {
  setTitle(t("nav.search"));
  view.innerHTML = `
    <section class="search-hero">
      <div class="searchbar primary-search">
        <label class="sr-only" for="searchInput">${escapeHtml(t("nav.search"))}</label>
        <input id="searchInput" name="studyhub-search" placeholder="${escapeHtml(t("search.placeholder"))}" autocomplete="off" />
        <button class="button secondary" id="toggleSearchFilters">${escapeHtml(t(state.searchFiltersOpen ? "common.hideFilters" : "common.filters"))}</button>
      </div>
      <div class="search-filters ${state.searchFiltersOpen ? "open" : ""}">
        <select id="searchCourse" name="search-course" aria-label="${escapeHtml(t("search.allCourses"))}">
          <option value="">${escapeHtml(t("search.allCourses"))}</option>
          ${state.courses.map((course) => `<option value="${course.id}">${escapeHtml(courseLabel(course))}</option>`).join("")}
        </select>
        <select id="searchScope" name="search-material-type" aria-label="${escapeHtml(t("search.allMaterial"))}">
          <option value="">${escapeHtml(t("search.allMaterial"))}</option>
          <option value="01 Course Materials">${escapeHtml(t("week.courseMaterials"))}</option>
          <option value="02 Exercises">${escapeHtml(t("week.exercises"))}</option>
          ${["lecture", "tutorial", "workshop", "lab", "quiz"].map((type) => `<option value="${escapeHtml(materialTypeValue(type))}">${escapeHtml(materialTypeLabel(type))}</option>`).join("")}
        </select>
        <label class="inline-check"><input id="searchArchived" type="checkbox" /> ${escapeHtml(t("search.includeArchived"))}</label>
        <button class="button ghost" id="clearSearchFilters">${escapeHtml(t("common.clear"))}</button>
      </div>
    </section>
    <section id="searchResults" class="file-list search-results"></section>
  `;
  $("#searchInput").addEventListener("input", debounce(runSearch, 220));
  $("#searchCourse").addEventListener("change", runSearch);
  $("#searchScope").addEventListener("change", runSearch);
  $("#searchArchived").addEventListener("change", runSearch);
  $("#toggleSearchFilters").addEventListener("click", () => {
    state.searchFiltersOpen = !state.searchFiltersOpen;
    renderSearch();
  });
  $("#clearSearchFilters").addEventListener("click", () => {
    $("#searchCourse").value = "";
    $("#searchScope").value = "";
    $("#searchArchived").checked = false;
    runSearch();
  });
  await runSearch();
}

async function runSearch() {
  const q = $("#searchInput")?.value || "";
  const course = $("#searchCourse")?.value || "";
  const scope = $("#searchScope")?.value || "";
  const archived = $("#searchArchived")?.checked ? "1" : "0";
  if (!q.trim() && !course && !scope && archived === "0") {
    $("#searchResults").innerHTML = empty(t("search.help"));
    return;
  }
  const contextCourse = Number(state.selectedCourseId || 0);
  const contextWeek = state.selectedWeek || "";
  const results = await api(`/api/search?q=${encodeURIComponent(q)}&course_id=${course}&scope=${encodeURIComponent(scope)}&include_archived=${archived}&context_course_id=${contextCourse}&context_week=${encodeURIComponent(contextWeek)}`);
  $("#searchResults").innerHTML = results.length ? results.map((file) => fileCard(file)).join("") : empty(t("search.noMatch"));
}

async function renderAskGpt() {
  setTitle(t("nav.ai"));
  if (!state.ask.context.course && state.selectedCourseId) {
    setAskContext(state.selectedWeek ? currentWeekContext(state.selectedCourseId, state.selectedWeek) : currentCourseContext(state.selectedCourseId));
  }
  await loadConversationList();
  const scopes = availableScopes();
  if (!scopes.some((scope) => scope.value === state.ask.scope && scope.enabled)) {
    state.ask.scope = defaultScopeForContext(state.ask.context);
  }
  view.innerHTML = `
    <section class="ask-shell ${state.ask.mode === "focus" ? "focus" : ""}">
      <div class="ask-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("ai.eyebrow"))}</p>
          <div class="ask-title-row">
            <h2>${escapeHtml(state.ask.conversationTitle || t("ai.workspace"))}</h2>
            ${state.ask.conversationId ? `<span class="chip">${escapeHtml(t("ai.savedLocally"))}</span>` : `<span class="chip">${escapeHtml(t("ai.newChatBadge"))}</span>`}
          </div>
        </div>
        <div class="ask-tools">
          <button class="button secondary" id="historyButton">${escapeHtml(t("ai.history"))}</button>
          <button class="button secondary" id="newAskChat">${escapeHtml(t("ai.newChat"))}</button>
          <button class="button secondary" id="toggleFocusMode">${escapeHtml(t(state.ask.mode === "focus" ? "ai.exitFocus" : "ai.openFull"))}</button>
        </div>
      </div>
      <div class="ask-context">
        <div>
          <h3>${escapeHtml(t("ai.answeringFrom"))}</h3>
          <div class="context-lines">${askContextLines().map((line) => `<span>${escapeHtml(line)}</span>`).join("")}</div>
        </div>
        <label>${escapeHtml(t("ai.scope"))}
          <select id="askScope">
            ${scopes.map((scope) => `<option value="${scope.value}" ${scope.value === state.ask.scope ? "selected" : ""} ${scope.enabled ? "" : "disabled"}>${scopeLabel(scope.value)}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="quick-actions" id="quickActions">
        ${quickActionsForContext().map((text) => `<button class="button secondary" data-quick-prompt="${escapeHtml(text)}">${escapeHtml(text)}</button>`).join("")}
      </div>
      <div class="conversation" id="askConversation">${renderAskMessages()}</div>
      <div class="ask-compose">
        <div class="composer-stack">
          <div class="composer-context">${escapeHtml(t("ai.answeringFrom"))}: ${escapeHtml(scopeLabel())}${state.ask.context.file ? ` · ${escapeHtml(state.ask.context.file)}` : ""}</div>
          <textarea id="askPagePrompt" placeholder="${escapeHtml(t("ai.placeholder"))}">${escapeHtml(state.ask.draft)}</textarea>
        </div>
        <button class="button primary" id="sendAskPage">${escapeHtml(t("common.send"))}</button>
      </div>
    </section>
  `;
  $("#askScope").addEventListener("change", () => {
    state.ask.scope = $("#askScope").value;
  });
  $("#historyButton").addEventListener("click", async () => {
    await openHistoryDrawer();
  });
  $("#toggleFocusMode").addEventListener("click", () => {
    setAiMode(state.ask.mode === "focus" ? "compact" : "focus");
    renderAskGpt();
  });
  $("#newAskChat").addEventListener("click", async () => {
    state.ask.messages = [];
    saveAskDraft("");
    state.ask.conversationId = null;
    state.ask.conversationTitle = "";
    localStorage.removeItem("studyhub.currentConversationId");
    await createConversation(state.ask.context, state.ask.scope);
    renderAskGpt();
  });
  $("#sendAskPage").addEventListener("click", sendAskPagePrompt);
  $("#askPagePrompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAskPagePrompt();
    }
  });
  $("#askPagePrompt").addEventListener("input", () => {
    saveAskDraft($("#askPagePrompt").value);
    $("#askPagePrompt").style.height = "auto";
    $("#askPagePrompt").style.height = `${Math.min($("#askPagePrompt").scrollHeight, 180)}px`;
  });
}

function renderAskMessages() {
  if (!state.ask.messages.length) {
    return `<div class="notice">${escapeHtml(t("ai.empty"))}</div>`;
  }
  return state.ask.messages
    .map((message) => {
      if (message.role === "system") return `<div class="context-change">${escapeHtml(message.text)}</div>`;
      const sources = message.sources?.length ? `<div class="source-list"><h3>${escapeHtml(t("common.sources"))}</h3>${message.sources.map(sourceCard).join("")}</div>` : "";
      const badge = message.role === "assistant" ? `<span class="chip">${escapeHtml(t("ai.explanation"))}</span>` : `<span class="chip">${escapeHtml(t("ai.myPrompt"))}</span>`;
      return `
        <article class="chat-message ${message.role}">
          <div class="message-head">${badge}${message.status ? `<span class="chip">${escapeHtml(message.status)}</span>` : ""}</div>
      <div class="message-body">${renderMarkdown(message.text)}</div>
          ${
            message.role === "assistant"
              ? `<div class="message-actions"><button class="tiny-button" data-copy-message="${escapeHtml(message.text)}">${escapeHtml(t("common.copy"))}</button><button class="tiny-button" data-retry-last="1">${escapeHtml(t("common.retry"))}</button>${sources ? `<button class="tiny-button" data-show-sources="1">${escapeHtml(t("common.sources"))}</button>` : ""}</div>`
              : ""
          }
          ${sources}
        </article>
      `;
    })
    .join("");
}

function sourceCard(source) {
  const loc = source.page_start ? `p.${source.page_start}` : source.slide_start ? `Slide ${source.slide_start}` : source.source_location || "";
  return `
    <button class="source-card" data-preview="${source.source_file_id || source.file_id}" data-page="${source.page_start || ""}">
      <span class="chip official">${escapeHtml(t("ai.officialSource"))}</span>
      <strong>${escapeHtml(courseLabel(source))}</strong>
      <span>${escapeHtml(source.week_label || "")}</span>
      <span>${escapeHtml(source.filename || "")}</span>
      ${loc ? `<span>${escapeHtml(loc)}</span>` : ""}
    </button>
  `;
}

async function openHistoryDrawer(query = "") {
  await loadConversationList(query);
  $("#historyDrawer").hidden = false;
  $("#historySearch").value = query;
  renderHistoryList();
  $("#historySearch").focus();
}

function renderHistoryList() {
  const groups = groupConversations(state.ask.history);
  const html = ["Today", "Yesterday", "Previous 7 days", "Older"]
    .filter((label) => groups[label]?.length)
    .map(
      (label) => `
        <section class="history-group">
          <h3>${escapeHtml(t({ Today: "ai.today", Yesterday: "ai.yesterday", "Previous 7 days": "ai.previous7", Older: "ai.older" }[label]))}</h3>
          ${groups[label].map(historyItem).join("")}
        </section>
      `,
    )
    .join("");
  $("#historyList").innerHTML = html || empty(t("ai.noConversations"));
}

function historyItem(row) {
  const active = Number(row.id) === Number(state.ask.conversationId) ? "active" : "";
  const source = row.source_available === false ? t("ai.sourceUnavailable") : [courseLabel(row), learningUnitLabel(row.week_label), row.filename].filter(Boolean).join(" · ");
  return `
    <article class="history-item ${active}">
      <button class="button secondary" data-open-conversation="${row.id}">${escapeHtml(row.title)}</button>
      <div class="history-meta">${escapeHtml(source || t("ai.generalChat"))} · ${row.message_count || 0}</div>
      <div class="history-actions">
        <button class="tiny-button" data-rename-conversation="${row.id}">${escapeHtml(t("common.rename"))}</button>
        <button class="tiny-button" data-duplicate-conversation="${row.id}">${escapeHtml(t("common.duplicate"))}</button>
        <button class="tiny-button" data-delete-conversation="${row.id}">${escapeHtml(t("common.delete"))}</button>
      </div>
    </article>
  `;
}

async function sendAskPagePrompt() {
  const prompt = $("#askPagePrompt").value.trim();
  if (!prompt) return;
  const context = contextForScope();
  if (!context.course) {
    state.ask.messages.push({ role: "assistant", text: t("ai.selectContext"), status: "no_context", sources: [] });
    renderAskGpt();
    return;
  }
  const qn = detectQuestionNumber(prompt);
  if (qn && state.ask.scope === "question") context.questionNumber = qn;
  context.scope = state.ask.scope;
  state.ask.messages.push({ role: "user", text: prompt, context: { ...context } });
  state.ask.messages.push({ role: "system", text: t("ai.searching") });
  $("#askConversation").innerHTML = renderAskMessages();
  $("#sendAskPage").disabled = true;
  $("#askPagePrompt").disabled = true;
  try {
    state.ask.messages[state.ask.messages.length - 1].text = t("ai.preparing");
    $("#askConversation").innerHTML = renderAskMessages();
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context, prompt, scope: state.ask.scope, conversationId: state.ask.conversationId }),
    });
    state.ask.conversationId = result.conversationId || result.conversation?.id || state.ask.conversationId;
    state.ask.conversationTitle = result.conversation?.title || state.ask.conversationTitle;
    if (state.ask.conversationId) localStorage.setItem("studyhub.currentConversationId", String(state.ask.conversationId));
    hydrateMessages(result.messages || [
      ...state.ask.messages.filter((message) => message.role !== "system"),
      { role: "assistant", body: result.response, status: result.status, sources: result.sources || [], questions: result.questions || [] },
    ]);
    saveAskDraft("");
    await loadConversationList();
    if (state.view === "ai") renderAskGpt();
  } catch (error) {
    state.ask.messages.pop();
    state.ask.messages.push({ role: "assistant", text: error.message, status: "error", sources: [] });
    if (state.view === "ai") renderAskGpt();
  } finally {
    const sendButton = $("#sendAskPage");
    const promptBox = $("#askPagePrompt");
    if (sendButton) sendButton.disabled = false;
    if (promptBox) promptBox.disabled = false;
  }
}

function debounce(fn, wait) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), wait);
  };
}

async function renderStarred() {
  setTitle("Starred", "Saved study material");
  const rows = await api("/api/starred");
  view.innerHTML = `<section class="section-block quiet-section"><div class="file-list">${rows.length ? rows.map((file) => fileCard(file)).join("") : empty("No starred files yet.")}</div></section>`;
}

function studyTabs() {
  const tabs = [
    ["plan", t("study.plan")],
    ["practice", t("study.practice")],
    ["wrong", t("study.wrong")],
    ["exam", t("study.exam")],
  ];
  return `
    <div class="segmented-tabs" role="tablist" aria-label="${escapeHtml(t("study.mode"))}">
      ${tabs.map(([mode, label]) => `<button class="${state.studyMode === mode ? "active" : ""}" data-study-mode="${mode}" role="tab" aria-selected="${state.studyMode === mode ? "true" : "false"}">${label}</button>`).join("")}
    </div>
  `;
}

async function renderStudy(mode = state.studyMode) {
  state.studyMode = mode;
  setTitle(t("nav.study"), t("study.eyebrow"));
  let body = "";
  if (mode === "plan") body = await studyPlanPanel();
  else if (mode === "wrong") body = await studyWrongPanel();
  else if (mode === "exam") body = studyExamPanel();
  else body = studyPracticePanel();
  view.innerHTML = `
    <section class="study-shell">
      ${studyTabs()}
      ${body}
    </section>
  `;
  if (mode === "practice") {
    $("#practiceCourse").addEventListener("change", loadPracticeQuestions);
    $("#practiceWeek").addEventListener("change", loadPracticeQuestions);
    $("#practiceType").addEventListener("change", loadPracticeQuestions);
    await loadPracticeQuestions();
  }
}

async function studyPlanPanel() {
  const overview = await api("/api/study/overview");
  const summary = overview.summary;
  return `
    <section class="study-overview" aria-labelledby="study-overview-heading">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("study.localRecords"))}</p>
          <h2 id="study-overview-heading">${escapeHtml(t("study.overview"))}</h2>
        </div>
        <span class="progress-ring" style="--progress:${summary.progress_percent}" aria-label="${escapeHtml(t("study.progressPercent", { percent: summary.progress_percent }))}">${summary.progress_percent}%</span>
      </div>
      <div class="metric-grid study-metrics">
        <article class="metric-card"><strong>${summary.completed}</strong><span>${escapeHtml(t("study.completed"))}</span></article>
        <article class="metric-card"><strong>${summary.in_progress}</strong><span>${escapeHtml(t("study.inProgress"))}</span></article>
        <article class="metric-card"><strong>${summary.needs_review}</strong><span>${escapeHtml(t("study.needsReview"))}</span></article>
        <article class="metric-card"><strong>${summary.total}</strong><span>${escapeHtml(t("study.totalMaterials"))}</span></article>
      </div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head"><div><p class="eyebrow">${escapeHtml(t("study.next"))}</p><h2>${escapeHtml(t("study.queue"))}</h2></div></div>
      <div class="file-list">${overview.queue.length ? overview.queue.map((file) => fileCard(file)).join("") : empty(t("study.queueEmpty"))}</div>
    </section>
    <section class="section-block quiet-section">
      <div class="section-head"><h2>${escapeHtml(t("study.courseProgressHeading"))}</h2></div>
      <div class="course-list-main">
        ${overview.courses.length ? overview.courses.map((item) => `
          <article class="course-row">
            <button class="course-row-main" data-course="${item.course_id}">
              <strong>${escapeHtml(item.course_code || item.course_name || "")}</strong>
              <span>${escapeHtml(item.course_name || "")}</span>
              <span class="mini-progress"><span style="width:${item.progress_percent}%"></span></span>
            </button>
            <div class="course-row-meta"><span>${escapeHtml(t("study.courseProgress", { completed: item.completed, total: item.total }))}</span></div>
          </article>`).join("") : empty(t("home.noCourses"))}
      </div>
    </section>
  `;
}

function studyPracticePanel() {
  return `
    <section class="section-block quiet-section">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("study.teacherOnly"))}</p>
          <h2>${escapeHtml(t("study.practice"))}</h2>
        </div>
        <span class="muted">${escapeHtml(t("study.noGenerated"))}</span>
      </div>
      <div class="toolbar practice-toolbar">
        <select id="practiceCourse">
          <option value="">${escapeHtml(t("search.allCourses"))}</option>
          ${state.courses.map((course) => `<option value="${escapeHtml(course.code)}">${escapeHtml(courseLabel(course))}</option>`).join("")}
        </select>
        <select id="practiceWeek">
          <option value="">${escapeHtml(t("study.allWeeks"))}</option>
          ${[...new Set(state.courses.flatMap((course) => weeksFor(course.id).map((week) => week.week_label)))].sort().map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(learningUnitLabel(label))}</option>`).join("")}
        </select>
        <select id="practiceType">
          <option value="">${escapeHtml(t("study.allTypes"))}</option>
          ${["tutorial", "workshop", "lab", "quiz"].map((type) => `<option value="${escapeHtml(materialTypeValue(type))}">${escapeHtml(materialTypeLabel(type))}</option>`).join("")}
          <option value="Practice">${escapeHtml(t("study.practice"))}</option>
          <option value="Revision">${escapeHtml(t("home.review"))}</option>
        </select>
      </div>
    </section>
    <section id="practiceResults" class="study-list"></section>
  `;
}

async function studyWrongPanel() {
  const rows = await api("/api/wrong-questions");
  return `
    <section class="section-block quiet-section">
      <div class="section-head">
        <div>
          <p class="eyebrow">${escapeHtml(t("study.userRecords"))}</p>
          <h2>${escapeHtml(t("study.wrong"))}</h2>
        </div>
      </div>
      ${rows.length ? `<div class="study-list">${rows.map(wrongRow).join("")}</div>` : empty(t("home.noWrong"))}
    </section>
  `;
}

function studyExamPanel() {
  const blocks = state.courses.map((course) => {
    const weeks = weeksFor(course.id);
    return `
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="muted">${escapeHtml(courseLabel(course))}</p>
            <h2>${escapeHtml(course.name)}</h2>
          </div>
          <button class="button secondary" data-course="${course.id}">Course</button>
        </div>
        <div class="week-list compact">
          ${weeks.map((week) => `
            <button class="week-row ${week.has_materials ? "has-materials" : ""}" data-week="${week.week_label}" data-course="${course.id}">
              <strong>${escapeHtml(week.week_label.replace("Week ", "W"))}</strong>
              <span class="muted">${week.file_count || 0}</span>
            </button>
          `).join("")}
        </div>
      </section>
    `;
  });
  return blocks.join("");
}

async function renderPractice() {
  state.studyMode = "practice";
  await renderStudy("practice");
}

async function renderWrong() {
  state.studyMode = "wrong";
  await renderStudy("wrong");
}

async function renderExam() {
  state.studyMode = "exam";
  await renderStudy("exam");
}

function wrongRow(row) {
  return `
    <article class="file-card">
      <div class="file-name">${escapeHtml(row.course_id || "")} ${escapeHtml(row.week_label || "")}</div>
      <p class="muted">${escapeHtml(row.question_ref || t("study.questionReference"))}</p>
      <div class="chips"><span class="chip">${escapeHtml(row.mastery || "new")}</span></div>
    </article>
  `;
}

async function renderSettings() {
  setTitle(t("nav.settings"), t("settings.eyebrow"));
  setPageActions(true);
  state.health = await api("/api/health");
  const ai = await api("/api/ai-status");
  const allTerms = await api("/api/terms?include_archived=1");
  const askReady = ai.openAI === "Configured" && ai.vectorStore === "Configured" ? t("status.ready") : t("status.notReady");
  view.innerHTML = `
    <section class="settings-layout">
      <section class="section-block quiet-section">
        <div class="section-head">
          <div><p class="eyebrow">${escapeHtml(t("settings.general"))}</p><h2>${escapeHtml(t("settings.language"))}</h2></div>
        </div>
        <label class="settings-field">${escapeHtml(t("settings.language"))}
          <select id="languagePreference">
            <option value="system" ${i18n.preference() === "system" ? "selected" : ""}>${escapeHtml(t("settings.systemDefault"))}</option>
            <option value="en" ${i18n.preference() === "en" ? "selected" : ""}>${escapeHtml(t("settings.english"))}</option>
            <option value="zh-CN" ${i18n.preference() === "zh-CN" ? "selected" : ""}>${escapeHtml(t("settings.simplifiedChinese"))}</option>
          </select>
        </label>
        <p class="muted">${escapeHtml(t("settings.languageHelp"))}</p>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div><p class="eyebrow">${escapeHtml(t("settings.organization"))}</p><h2>${escapeHtml(t("settings.terms"))}</h2></div>
          <button class="button secondary" data-new-term="1">${escapeHtml(t("settings.newTerm"))}</button>
        </div>
        <div class="settings-list">
          ${allTerms.map((term) => `
            <div>
              <span>${escapeHtml(term.stable_id === "term_imported" ? t("settings.importedCourses") : term.name)}${term.archived ? ` (${escapeHtml(t("common.archived"))})` : ""}</span>
              <span class="settings-inline-actions">
                <button class="button secondary" data-rename-term="${term.id}" data-term-name="${escapeHtml(term.name)}">${escapeHtml(t("common.rename"))}</button>
                ${term.stable_id === "term_imported" ? "" : `<button class="button secondary" data-${term.archived ? "restore" : "archive"}-term="${term.id}">${escapeHtml(t(term.archived ? "common.restore" : "common.archive"))}</button>`}
              </span>
            </div>
          `).join("")}
        </div>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="eyebrow">${escapeHtml(t("settings.general"))}</p>
            <h2>${escapeHtml(t("settings.libraryActions"))}</h2>
          </div>
        </div>
        <div class="settings-actions">
          <button class="button primary" data-run-scan="1">${escapeHtml(t("courses.scan"))}</button>
          <button class="button secondary" data-open-upload="1" data-course-id="0">${escapeHtml(t("courses.addInbox"))}</button>
          <button class="button secondary" data-view="courses">${escapeHtml(t("home.browseCourses"))}</button>
        </div>
        ${librarySetupForm()}
        ${recoveryCards("all", true)}
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="eyebrow">${escapeHtml(t("settings.libraryHealth"))}</p>
            <h2>${escapeHtml(t("settings.indexStatus"))}</h2>
          </div>
        </div>
        <div class="settings-list">
          <div><span>${escapeHtml(t("settings.studyLibrary"))}</span><strong>${escapeHtml(t(state.health.studyLibraryConnected ? "status.connected" : "status.missing"))}</strong></div>
          <div><span>${escapeHtml(t("settings.filesLibrary"))}</span><strong>${escapeHtml(state.health.filesIndexed || 0)}</strong></div>
          <div><span>${escapeHtml(t("settings.pdfSupport"))}</span><strong>${escapeHtml(t(`status.${String(state.health.pdfTextExtraction || "unknown").replace(/^./, (c) => c.toLowerCase())}`))}</strong></div>
          <div><span>${escapeHtml(t("settings.officePreview"))}</span><strong>${escapeHtml(t(`status.${String(state.health.officeVisualPreview || "unknown").replace(/^./, (c) => c.toLowerCase())}`))}</strong></div>
          <div><span>${escapeHtml(t("settings.suspicious"))}</span><strong>${escapeHtml(state.health.suspiciousFiles || 0)}</strong></div>
        </div>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="eyebrow">AI</p>
            <h2>${escapeHtml(t("settings.askAi"))}</h2>
          </div>
        </div>
        <div class="settings-list">
          <div><span>OpenAI</span><strong>${escapeHtml(statusLabel(ai.openAI))}</strong></div>
          <div><span>${escapeHtml(t("settings.aiFileSearch"))}</span><strong>${escapeHtml(statusLabel(ai.vectorStore))}</strong></div>
          <div><span>${escapeHtml(t("settings.aiAnswers"))}</span><strong>${escapeHtml(askReady)}</strong></div>
          <div><span>${escapeHtml(t("settings.lastAiSync"))}</span><strong>${escapeHtml(ai.lastAISync || t("common.never"))}</strong></div>
          <div><span>${escapeHtml(t("settings.filesAi"))}</span><strong>${escapeHtml(ai.indexedFiles || 0)}</strong></div>
          <div><span>${escapeHtml(t("settings.filesAiSearch"))}</span><strong>${escapeHtml(ai.vectorIndexedFiles || 0)}</strong></div>
        </div>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="eyebrow">${escapeHtml(t("settings.privacy"))}</p>
            <h2>${escapeHtml(t("settings.boundaries"))}</h2>
          </div>
        </div>
        <div class="settings-list">
          <div><span>${escapeHtml(t("settings.serverBind"))}</span><strong>127.0.0.1</strong></div>
          <div><span>${escapeHtml(t("settings.telemetry"))}</span><strong>${escapeHtml(t("status.off"))}</strong></div>
          <div><span>${escapeHtml(t("settings.originalFiles"))}</span><strong>${escapeHtml(t("settings.localTruth"))}</strong></div>
          <div><span>${escapeHtml(t("settings.aiHistory"))}</span><strong>${escapeHtml(t("settings.storedLocally"))}</strong></div>
        </div>
        <button class="button secondary" data-open-history="1">${escapeHtml(t("settings.manageAiHistory"))}</button>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div><p class="eyebrow">${escapeHtml(t("settings.localData"))}</p><h2>${escapeHtml(t("settings.storageControls"))}</h2></div>
        </div>
        <div class="settings-actions">
          <button class="button secondary" data-clear-cache="1">${escapeHtml(t("settings.clearCache"))}</button>
          <button class="button secondary" data-reset-studyhub="1">${escapeHtml(t("settings.resetMetadata"))}</button>
        </div>
        <p class="muted">${escapeHtml(t("settings.dataSafety"))}</p>
      </section>
      <section class="section-block quiet-section">
        <div class="section-head">
          <div>
            <p class="eyebrow">${escapeHtml(t("settings.advanced"))}</p>
            <h2>${escapeHtml(t("settings.diagnostics"))}</h2>
          </div>
        </div>
        <div class="settings-list compact">
          <div><span>${escapeHtml(t("settings.release"))}</span><strong>${escapeHtml(state.health.version || "Local build")}</strong></div>
          <div><span>${escapeHtml(t("settings.mode"))}</span><strong>${escapeHtml(t("settings.localLibrary"))}</strong></div>
          <div><span>${escapeHtml(t("settings.vectorStatus"))}</span><strong>${escapeHtml(statusLabel(ai.vectorStoreLabel || ai.vectorStore || "Not configured"))}</strong></div>
        </div>
      </section>
    </section>
  `;
  bindLibrarySetupForm();
  $("#languagePreference").addEventListener("change", (event) => i18n.setPreference(event.target.value));
}

function empty(text) {
  return `<div class="notice">${escapeHtml(text)}</div>`;
}

async function route(viewName = state.view, options = {}) {
  if (viewName === "askgpt") viewName = "ai";
  if (["plan", "practice", "wrong", "exam"].includes(viewName)) {
    state.studyMode = viewName === "exam" ? "exam" : viewName;
    viewName = "study";
  }
  if (viewName === "thisWeek") viewName = "home";
  if (viewName === "starred") viewName = "courses";
  if (viewName === "file") {
    const fileId = Number(options.fileId || 0);
    if (!fileId) return route("home", { history: options.history, replace: options.replace });
    state.view = "file";
    const file = await api(`/api/file?id=${fileId}`);
    const wasRestoring = restoringRoute;
    restoringRoute = true;
    try {
      if (file.course_id && file.week_label) await renderWeek(Number(file.course_id), file.week_label);
      else await route("home", { history: false });
    } finally {
      restoringRoute = wasRestoring;
    }
    document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", false));
    if (options.history !== false) recordRoute({ view: "file", fileId, page: Number(options.page || 0) }, options.replace);
    return openFileDrawer(fileId, Number(options.page || 0), { history: false });
  }
  if ($("#fileDrawer")) $("#fileDrawer").hidden = true;
  state.view = viewName;
  if (viewName === "study" && options.mode) state.studyMode = options.mode;
  if (options.history !== false) recordRoute({ view: viewName, mode: viewName === "study" ? state.studyMode : undefined }, options.replace);
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  if (viewName === "home") return renderHome();
  if (viewName === "courses") return renderCourses();
  if (viewName === "search") return renderSearch();
  if (viewName === "ai") return renderAskGpt();
  if (viewName === "study") return renderStudy();
  if (viewName === "settings") return renderSettings();
}

async function openFileDrawer(fileId, page = 0, options = {}) {
  let file = await api(`/api/file?id=${fileId}`);
  if ((file.study_status || "not_started") === "not_started") {
    const studyState = await postJson("/api/study/material", { file_id: file.id, action: "open" });
    file = { ...file, ...studyState, study_status: studyState.status || "in_progress" };
  }
  state.selectedFile = file;
  rememberFile(file);
  $("#drawerMeta").textContent = `${courseLabel(file)} · ${file.week_label || ""} · ${file.category || ""}`;
  $("#drawerTitle").textContent = file.filename;
  $("#contextIndicator").innerHTML = [courseLabel(file), file.week_label, file.exercise_type || file.category, file.filename]
    .filter(Boolean)
    .map((line) => `<span>${escapeHtml(line)}</span>`)
    .join("");
  $("#fileDetails").innerHTML = `
    <div><span>${escapeHtml(t("file.filename"))}</span><strong>${escapeHtml(file.filename)}</strong></div>
    <div><span>${escapeHtml(t("week.type"))}</span><strong>${escapeHtml(file.mime_type || file.extension || t("common.unknown"))}</strong></div>
    <div><span>${escapeHtml(t("file.aiReadiness"))}</span><strong>${escapeHtml(file.ai_index_status || t("common.unknown"))}</strong></div>
    <div><span>${escapeHtml(t("common.source"))}</span><strong>${escapeHtml(file.source_label || file.source || t("file.local"))}</strong></div>
    <div><span>${escapeHtml(t("study.statusLabel"))}</span><strong id="fileStudyStatus">${escapeHtml(studyStatusLabel(file))}</strong></div>
  `;
  $("#extractedText").textContent = file.extractedText || t("file.noReadableText");
  const pageHash = page ? `#page=${page}` : "";
  $("#previewFrame").innerHTML = `<iframe title="${escapeHtml(t("file.preview"))}" src="/preview/${file.id}${pageHash}"></iframe>`;
  $("#askResponse").textContent = "";
  $("#askPrompt").value = "";
  $("#noteBody").value = "";
  await loadFileNotes(file.id);
  updateFileStudyControls();
  $("#fileDrawer").hidden = false;
  const savedWidth = localStorage.getItem("studyhub.previewWidth");
  if (savedWidth) document.querySelector(".drawer-panel").style.setProperty("--studyhub-preview-width", savedWidth);
  if (options.history !== false) recordRoute({ view: "file", fileId, page: Number(page || 0) }, options.replace);
}

function updateFileStudyControls() {
  const file = state.selectedFile;
  if (!file) return;
  const completeButton = $("#studyCompleteFile");
  const reviewButton = $("#studyReviewFile");
  const completed = file.study_status === "completed";
  const needsReview = Boolean(Number(file.needs_review || 0));
  completeButton.textContent = t(completed ? "study.reopen" : "study.markComplete");
  completeButton.dataset.studyAction = completed ? "reopen" : "complete";
  completeButton.setAttribute("aria-pressed", String(completed));
  reviewButton.textContent = t(needsReview ? "study.markReviewed" : "study.markForReview");
  reviewButton.dataset.studyAction = needsReview ? "review" : "needs_review";
  reviewButton.setAttribute("aria-pressed", String(needsReview));
  const label = $("#fileStudyStatus");
  if (label) label.textContent = studyStatusLabel(file);
}

async function setMaterialStudyState(action) {
  if (!state.selectedFile) return;
  const result = await postJson("/api/study/material", { file_id: state.selectedFile.id, action });
  state.selectedFile = {
    ...state.selectedFile,
    ...result,
    study_status: result.status || result.study_status || state.selectedFile.study_status,
  };
  state.weekFiles = state.weekFiles.map((file) => Number(file.id) === Number(state.selectedFile.id) ? { ...file, ...state.selectedFile } : file);
  updateFileStudyControls();
  toast(t(action === "complete" ? "toast.studyCompleted" : action === "reopen" ? "toast.studyReopened" : action === "review" ? "toast.studyReviewed" : "toast.studyNeedsReview"));
}

async function loadFileNotes(fileId) {
  const notes = await api(`/api/notes?targetType=file&targetId=${fileId}`);
  $("#fileNotes").innerHTML = notes.length
    ? notes.map(noteCard).join("")
    : `<div class="notice compact">${escapeHtml(t("file.noNotes"))}</div>`;
}

function noteCard(note) {
  return `
    <article class="note-card">
      <div class="chips"><span class="chip">${escapeHtml(t("file.userNote"))}</span><span class="chip">${escapeHtml(note.updated_at || "")}</span></div>
      <p>${escapeHtml(note.body)}</p>
    </article>
  `;
}

async function saveNote() {
  if (!state.selectedFile) return;
  const body = $("#noteBody").value.trim();
  if (!body) {
    toast(t("file.writeNote"));
    return;
  }
  await api("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      targetType: "file",
      targetId: state.selectedFile.id,
      courseId: state.selectedFile.course_id,
      week: state.selectedFile.week_label,
      body,
    }),
  });
  $("#noteBody").value = "";
  await loadFileNotes(state.selectedFile.id);
  toast(t("file.noteSaved"));
}

async function openAskForFile(fileId, prompt = "") {
  const file = await api(`/api/file?id=${fileId}`);
  state.selectedFile = file;
  setAskContext(fileAskContext(file), { prompt });
  $("#fileDrawer").hidden = true;
  route("ai");
}

async function openOriginal(fileId) {
  await api(`/api/open/${fileId}`, { method: "POST" });
  toast(t("file.originalOpened"));
}

async function toggleStar(fileId) {
  const result = await api(`/api/star/${fileId}`, { method: "POST" });
  toast(t(result.starred ? "file.starred" : "file.unstarred"));
  if (state.selectedFile?.id === fileId) state.selectedFile.star_id = result.starred ? 1 : null;
  if (state.weekFiles.length) {
    state.weekFiles = state.weekFiles.map((file) => (file.id === fileId ? { ...file, star_id: result.starred ? 1 : null } : file));
    renderWeekFiles();
  } else {
    await route();
  }
}

async function copyContext(fileId) {
  const context = await api(`/api/prepare-context?file_id=${fileId}&q=${encodeURIComponent($("#askPrompt")?.value || "")}`);
  await navigator.clipboard.writeText(JSON.stringify(context, null, 2));
  toast(t("file.sourceCopied"));
}

async function askAboutFile() {
  if (!state.selectedFile) return;
  const prompt = $("#askPrompt").value.trim();
  $("#askBtn").disabled = true;
  $("#askResponse").textContent = `${t("ai.searching")}\n${t("ai.preparing")}`;
  const context = {
    fileId: state.selectedFile.id,
    course: state.selectedFile.course_code,
    week: state.selectedFile.week_label,
    weekNumber: state.selectedFile.week_number,
    file: state.selectedFile.filename,
    materialType: state.selectedFile.category,
    category: state.selectedFile.category,
    exerciseType: state.selectedFile.exercise_type || state.selectedFile.category,
    questionNumber: detectQuestionNumber(prompt),
  };
  try {
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context, prompt, scope: "file", conversationId: state.ask.conversationId }),
    });
    state.ask.conversationId = result.conversationId || result.conversation?.id || state.ask.conversationId;
    state.ask.conversationTitle = result.conversation?.title || state.ask.conversationTitle;
    if (state.ask.conversationId) localStorage.setItem("studyhub.currentConversationId", String(state.ask.conversationId));
    hydrateMessages(result.messages || []);
    $("#askResponse").innerHTML = `<div class="message-body">${renderMarkdown(result.response)}</div><div class="source-list">${(result.sources || []).map(sourceCard).join("")}</div>`;
  } finally {
    $("#askBtn").disabled = false;
  }
}

function formatSource(source) {
  const loc = source.page_start ? `p.${source.page_start}` : source.slide_start ? `Slide ${source.slide_start}` : source.source_location || "";
  return `${courseLabel(source)} ${source.week_label || ""} — ${source.filename || ""} ${loc}`.trim();
}

async function previewWeekWithGpt() {
  const course = courseById(state.selectedCourseId);
  setAskContext({ course: course?.code, courseId: course?.id, week: state.selectedWeek }, { prompt: "帮我预习这一周的内容。", scope: "week" });
  route("ai");
}

async function loadPracticeQuestions() {
  const course = $("#practiceCourse")?.value || "";
  const week = $("#practiceWeek")?.value || "";
  const type = $("#practiceType")?.value || "";
  const rows = await api(`/api/questions?course=${encodeURIComponent(course)}&week=${encodeURIComponent(week)}&type=${encodeURIComponent(type)}`);
  $("#practiceResults").innerHTML = rows.length
    ? rows.map(questionCard).join("")
    : empty(t("study.noTeacherQuestion"));
}

function questionCard(row) {
  return `
    <article class="file-card">
      <header>
        <div>
          <div class="file-name">${escapeHtml(courseLabel(row))} ${escapeHtml(row.week_label || "")} ${escapeHtml(row.question_number || "Question")}</div>
          <p class="muted">${escapeHtml(row.filename || "")} · ${escapeHtml(row.source_location || "")}</p>
        </div>
      </header>
      <p>${escapeHtml(row.question_text)}</p>
      <div class="file-actions">
        <button class="button secondary" data-preview="${row.source_file_id}">${escapeHtml(t("study.openSource"))}</button>
        <button class="button secondary" data-ask-question="${row.id}" data-source-file="${row.source_file_id}" data-course-code="${escapeHtml(row.course_code)}" data-week-label="${escapeHtml(row.week_label || "")}" data-exercise-type="${escapeHtml(row.exercise_type || "")}" data-question-number="${escapeHtml(row.question_number || "")}" data-filename="${escapeHtml(row.filename || "")}">${escapeHtml(t("courses.askAi"))}</button>
        <button class="button secondary" data-ask-question="${row.id}" data-source-file="${row.source_file_id}" data-course-code="${escapeHtml(row.course_code)}" data-week-label="${escapeHtml(row.week_label || "")}" data-exercise-type="${escapeHtml(row.exercise_type || "")}" data-question-number="${escapeHtml(row.question_number || "")}" data-filename="${escapeHtml(row.filename || "")}" data-question-prompt="Check my answer">${escapeHtml(t("study.checkAi"))}</button>
        <details class="row-more">
          <summary>More</summary>
          <div>
            <button class="button secondary" data-context="${row.source_file_id}">${escapeHtml(t("file.copySource"))}</button>
          </div>
        </details>
      </div>
    </article>
  `;
}

async function scanLibrary() {
  $("#scanBtn").disabled = true;
  $("#scanBtn").textContent = t("toast.scanning");
  try {
    await api("/api/scan", { method: "POST" });
    await loadBase();
    await route();
    toast(t("toast.libraryScanned"));
  } finally {
    $("#scanBtn").disabled = false;
    $("#scanBtn").textContent = `↻ ${t("courses.scan")}`;
  }
}

function openCourseDialog(course = null) {
  populateCourseTerms();
  $("#courseDialogTitle").textContent = t(course ? "dialog.editCourse" : "dialog.createCourse");
  $("#courseEditId").value = course?.id || "";
  $("#courseCode").value = course?.code || "";
  $("#courseName").value = course?.name || "";
  if (course?.term_id) $("#courseTerm").value = String(course.term_id);
  $("#courseDialog").showModal();
  $("#courseName").focus();
}

async function saveCourse(event) {
  event.preventDefault();
  const id = Number($("#courseEditId").value || 0);
  await postJson("/api/courses/manage", {
    action: id ? "update" : "create",
    id: id || undefined,
    term_id: Number($("#courseTerm").value || 0),
    course_code: $("#courseCode").value.trim(),
    display_name: $("#courseName").value.trim(),
  });
  $("#courseDialog").close();
  localStorage.setItem("studyhub.onboardingDismissed", "true");
  await loadBase();
  await renderCourses();
  toast(t(id ? "toast.courseUpdated" : "toast.courseCreated"));
}

function openWeekDialog(courseId, week = null) {
  $("#weekCourseId").value = String(courseId);
  $("#weekEditId").value = week?.id || "";
  $("#weekDialogTitle").textContent = t(week ? "dialog.renameWeek" : "dialog.addWeek");
  $("#weekName").value = week?.week_label || "";
  $("#weekKind").value = week?.kind || "week";
  $("#weekDialog").showModal();
  $("#weekName").focus();
}

async function saveWeek(event) {
  event.preventDefault();
  const courseId = Number($("#weekCourseId").value);
  const id = Number($("#weekEditId").value || 0);
  await postJson("/api/weeks/manage", {
    action: id ? "rename" : "create",
    id: id || undefined,
    course_id: courseId,
    label: $("#weekName").value.trim(),
    kind: $("#weekKind").value,
  });
  $("#weekDialog").close();
  await loadBase();
  await renderCourse(courseId);
  toast(t("toast.courseStructureUpdated"));
}

function openAddMaterial(courseId = 0, weekLabel = "") {
  state.nativeImportPaths = [];
  populateUploadCourses();
  if (courseId) $("#uploadCourse").value = String(courseId);
  populateUploadWeeks();
  if (weekLabel) {
    const week = weeksFor(Number($("#uploadCourse").value)).find((item) => item.week_label === weekLabel);
    if (week) $("#uploadWeek").value = String(week.id);
  }
  $("#uploadSelection").hidden = true;
  $("#uploadFile").value = "";
  $("#chooseNativeFiles").hidden = !state.health?.desktopMode;
  $("#uploadFileLabel").textContent = state.health?.desktopMode
    ? "Choose files from this Mac, or drop them into StudyHub"
    : t("dialog.chooseFilesBody");
  $("#uploadDialog").showModal();
}

function showNativeSelection(paths) {
  state.nativeImportPaths = paths || [];
  const box = $("#uploadSelection");
  box.hidden = !state.nativeImportPaths.length;
  box.textContent = state.nativeImportPaths.length
    ? `${state.nativeImportPaths.length} local file${state.nativeImportPaths.length === 1 ? "" : "s"} selected. Originals stay in place.`
    : "";
}

async function chooseNativeMaterialFiles() {
  const paths = await desktopInvoke("choose_study_files");
  if (!paths?.length) return;
  showNativeSelection(paths);
  try {
    const suggestion = await api(`/api/materials/suggest?path=${encodeURIComponent(paths[0])}`);
    if (suggestion.course_id) {
      $("#uploadCourse").value = String(suggestion.course_id);
      populateUploadWeeks();
    }
    if (suggestion.week_label) {
      const week = weeksFor(Number($("#uploadCourse").value)).find((item) => item.week_label === suggestion.week_label);
      if (week) $("#uploadWeek").value = String(week.id);
    }
    if (suggestion.material_type) $("#uploadMaterialType").value = suggestion.material_type;
  } catch (_error) {
    // Suggestions are optional; the user-confirmed destination remains authoritative.
  }
}

async function importCourseFolder() {
  const selected = await desktopInvoke("choose_study_folder");
  if (!selected) return;
  const result = await postJson("/api/materials/import-folder", { path: selected });
  await loadBase();
  await renderCourse(result.course.id);
  toast(`${result.added} material${result.added === 1 ? "" : "s"} added`);
}

async function batchManageMaterials(action) {
  const ids = [...state.selectedMaterialIds];
  if (!ids.length) return;
  if (action === "remove" && !confirm(t("confirm.removeSelected"))) return;
  await postJson("/api/materials/manage", {
    action,
    ids,
    course_id: Number($("#batchCourse")?.value || state.selectedCourseId),
    week_id: Number($("#batchWeek")?.value || 0),
    week_label: state.selectedWeek,
    material_type: $("#batchMaterialType")?.value || "other",
  });
  await renderWeek(state.selectedCourseId, state.selectedWeek);
  toast(t(action === "remove" ? "toast.removed" : action === "star" ? "toast.selectedStarred" : "toast.classificationUpdated"));
}

async function submitNativeImport(duplicatePolicy = "skip") {
  const result = await postJson("/api/materials/import-paths", {
    paths: state.nativeImportPaths,
    course_id: Number($("#uploadCourse").value || 0),
    week_id: Number($("#uploadWeek").value || 0),
    material_type: $("#uploadMaterialType").value,
    duplicate_policy: duplicatePolicy,
  });
  const duplicates = result.items.filter((item) => item.status === "duplicate");
  if (duplicates.length && duplicatePolicy === "skip") {
    state.duplicateImport = { items: duplicates };
    $("#duplicateMessage").textContent = `${duplicates.length} selected file${duplicates.length === 1 ? " has" : "s have"} matching content already in StudyHub.`;
  }
  return result;
}

async function uploadMaterial(event) {
  event.preventDefault();
  if (state.health?.desktopMode) {
    if (!state.nativeImportPaths.length) {
      await chooseNativeMaterialFiles();
      if (!state.nativeImportPaths.length) return;
    }
    const result = await submitNativeImport();
    $("#uploadDialog").close();
    if (result.items.some((item) => item.status === "duplicate")) $("#duplicateDialog").showModal();
    await loadBase();
    if (state.selectedCourseId && state.selectedWeek) await renderWeek(state.selectedCourseId, state.selectedWeek);
    else await renderCourses();
    toast(`${result.added} material${result.added === 1 ? "" : "s"} added`);
    return;
  }
  const files = $("#uploadFile").files;
  if (!files.length) return;
  const materialType = $("#uploadMaterialType").value;
  const form = new FormData();
  form.append("course_id", $("#uploadCourse").value);
  const selectedWeek = weeksFor(Number($("#uploadCourse").value)).find((week) => Number(week.id) === Number($("#uploadWeek").value));
  form.append("week", selectedWeek?.week_label || "Unclassified");
  form.append("section", ["tutorial", "workshop", "lab", "quiz", "assignment", "exam"].includes(materialType) ? "02 Exercises" : "01 Course Materials");
  form.append("category", materialType);
  Array.from(files).forEach((file) => form.append("files", file));
  await fetch("/api/upload", { method: "POST", headers: { "X-StudyHub-CSRF": state.csrfToken }, body: form }).then(async (response) => {
    if (!response.ok) throw new Error((await response.json()).error || t("toast.uploadFailed"));
  });
  $("#uploadDialog").close();
  await loadBase();
  await route();
  toast(t("toast.materialAdded"));
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.quickPrompt) {
    const box = $("#askPagePrompt");
    if (box) {
      box.value = target.dataset.quickPrompt;
      state.ask.draft = target.dataset.quickPrompt;
      box.focus();
    }
    return;
  }
  if (target.dataset.dismissOnboarding) {
    localStorage.setItem("studyhub.onboardingDismissed", "true");
    route("home");
    return;
  }
  if (target.dataset.openLibrarySetup) {
    route("settings").then(() => $("#studyLibraryPathInput")?.focus());
    return;
  }
  if (target.dataset.createFirstCourse) {
    openCourseDialog();
    return;
  }
  if (target.dataset.importFirstFolder || target.dataset.importCourseFolder) {
    try {
      await importCourseFolder();
    } catch (error) {
      toast(error.message || String(error));
    }
    return;
  }
  if (target.dataset.newTerm) {
    const name = prompt(t("prompt.newTerm"), t("prompt.semesterOne"));
    if (!name?.trim()) return;
    await postJson("/api/terms/manage", { action: "create", name: name.trim() });
    await loadBase();
    await renderSettings();
    toast(t("toast.termCreated"));
    return;
  }
  if (target.dataset.renameTerm) {
    const name = prompt(t("prompt.renameTerm"), target.dataset.termName || "");
    if (!name?.trim()) return;
    await postJson("/api/terms/manage", { action: "rename", id: Number(target.dataset.renameTerm), name: name.trim() });
    await loadBase();
    await renderSettings();
    toast(t("toast.termRenamed"));
    return;
  }
  if (target.dataset.archiveTerm || target.dataset.restoreTerm) {
    const id = Number(target.dataset.archiveTerm || target.dataset.restoreTerm);
    const action = target.dataset.archiveTerm ? "archive" : "restore";
    await postJson("/api/terms/manage", { action, id });
    await loadBase();
    await renderSettings();
    toast(t(action === "archive" ? "toast.termArchived" : "toast.termRestored"));
    return;
  }
  if (target.dataset.clearCache) {
    target.disabled = true;
    try {
      const result = await postJson("/api/cache/clear");
      await loadBase();
      await renderSettings();
      toast(`${result.reindexed} material${result.reindexed === 1 ? "" : "s"} reindexed`);
    } finally {
      target.disabled = false;
    }
    return;
  }
  if (target.dataset.resetStudyhub) {
    const confirmation = prompt(`${t("confirm.resetTitle")}\n\n${t("confirm.resetBody")}\n\n${t("confirm.typeReset")}`, "");
    if (confirmation !== "RESET STUDYHUB") return;
    await postJson("/api/reset", { confirmation });
    localStorage.clear();
    const restart = desktopInvoke("restart_backend");
    if (restart) await restart;
    else {
      window.location.reload();
    }
    return;
  }
  if (target.dataset.chooseStudyFolder) {
    try {
      const selected = await desktopInvoke("choose_study_folder");
      if (!selected) return;
      const form = $("#librarySetupForm");
      const input = $("#studyLibraryPathInput");
      if (form && input) {
        input.value = selected;
        form.requestSubmit();
        return;
      }
      await api("/api/config/study-library", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selected }),
      });
      localStorage.setItem("studyhub.onboardingDismissed", "true");
      const restart = desktopInvoke("restart_backend");
      if (restart) await restart;
    } catch (error) {
      toast(error.message || String(error));
    }
    return;
  }
  if (target.dataset.studyMode) {
    state.studyMode = target.dataset.studyMode;
    route("study");
    return;
  }
  if (target.dataset.runScan) {
    await scanLibrary();
    return;
  }
  if (target.dataset.newCourse) {
    openCourseDialog();
    return;
  }
  if (target.dataset.editCourse) {
    openCourseDialog(courseById(Number(target.dataset.editCourse)));
    return;
  }
  if (target.dataset.archiveCourse) {
    if (!confirm(t("confirm.archiveCourse"))) return;
    await postJson("/api/courses/manage", { action: "archive", id: Number(target.dataset.archiveCourse) });
    await loadBase();
    await renderCourses();
    toast(t("toast.courseArchived"));
    return;
  }
  if (target.dataset.restoreCourse) {
    await postJson("/api/courses/manage", { action: "restore", id: Number(target.dataset.restoreCourse) });
    await loadBase();
    await renderCourses();
    toast(t("toast.courseRestored"));
    return;
  }
  if (target.dataset.removeCourse) {
    if (!confirm(`${t("confirm.removeCourseTitle")}\n\n${t("confirm.removeCourseBody")}`)) return;
    await postJson("/api/courses/manage", { action: "remove", id: Number(target.dataset.removeCourse) });
    await loadBase();
    await renderCourses();
    toast(t("toast.courseRemoved"));
    return;
  }
  if (target.dataset.addWeek) {
    openWeekDialog(Number(target.dataset.addWeek));
    return;
  }
  if (target.dataset.editWeek) {
    const courseId = Number(target.dataset.courseId);
    const week = weeksFor(courseId).find((item) => Number(item.id) === Number(target.dataset.editWeek));
    if (week) openWeekDialog(courseId, week);
    return;
  }
  if (target.dataset.removeWeek) {
    const courseId = Number(target.dataset.courseId);
    if (!confirm(t("confirm.removeEmptyWeek"))) return;
    await postJson("/api/weeks/manage", { action: "remove", id: Number(target.dataset.removeWeek) });
    await loadBase();
    await renderCourse(courseId);
    toast(t("toast.emptySectionRemoved"));
    return;
  }
  if (target.dataset.openUpload) {
    const courseId = target.dataset.courseId !== undefined
      ? Number(target.dataset.courseId)
      : Number(state.selectedCourseId || 0);
    openAddMaterial(courseId, target.dataset.weekLabel || (courseId ? state.selectedWeek : "") || "");
    return;
  }
  if (target.dataset.renameMaterial) {
    const displayName = prompt(t("prompt.renameMaterial"), target.dataset.materialName || "");
    if (!displayName?.trim()) return;
    await postJson("/api/materials/manage", { action: "rename", id: Number(target.dataset.renameMaterial), display_name: displayName.trim() });
    await loadBase();
    await route();
    toast(t("toast.materialRenamed"));
    return;
  }
  if (target.dataset.removeMaterial) {
    if (!confirm(`${t("confirm.removeMaterialTitle")}\n\n${t("confirm.removeMaterialBody")}`)) return;
    await postJson("/api/materials/manage", { action: "remove", id: Number(target.dataset.removeMaterial) });
    await loadBase();
    await route();
    toast(t("toast.removed"));
    return;
  }
  if (target.dataset.relinkMaterial) {
    const paths = await desktopInvoke("choose_study_files");
    if (!paths?.length) return;
    await postJson("/api/materials/manage", { action: "relink", id: Number(target.dataset.relinkMaterial), path: paths[0] });
    await loadBase();
    await route();
    toast(t("toast.fileRelinked"));
    return;
  }
  if (target.dataset.openHistory) {
    await openHistoryDrawer();
    return;
  }
  if (target.dataset.askCourse) {
    setAskContext(currentCourseContext(Number(target.dataset.askCourse)), { scope: "course" });
    route("ai");
    return;
  }
  if (target.dataset.askWeek) {
    setAskContext(currentWeekContext(Number(target.dataset.askWeek), target.dataset.weekLabel), { scope: "week" });
    route("ai");
    return;
  }
  if (target.dataset.askFile) {
    await openAskForFile(Number(target.dataset.askFile));
    return;
  }
  if (target.dataset.askQuestion) {
    setAskContext(
      {
        questionId: Number(target.dataset.askQuestion),
        fileId: Number(target.dataset.sourceFile || 0) || undefined,
        course: target.dataset.courseCode,
        week: target.dataset.weekLabel,
        exerciseType: target.dataset.exerciseType,
        questionNumber: target.dataset.questionNumber,
        file: target.dataset.filename,
      },
      { scope: "question", prompt: target.dataset.questionPrompt || t("ai.explainQuestion") },
    );
    route("ai");
    return;
  }
  if (target.dataset.openConversation) {
    await reopenConversation(Number(target.dataset.openConversation));
    return;
  }
  if (target.dataset.renameConversation) {
    const row = state.ask.history.find((item) => Number(item.id) === Number(target.dataset.renameConversation));
    const title = prompt(t("prompt.renameConversation"), row?.title || "");
    if (title?.trim()) {
      await api("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "rename", id: Number(target.dataset.renameConversation), title: title.trim() }),
      });
      await openHistoryDrawer($("#historySearch")?.value || "");
    }
    return;
  }
  if (target.dataset.duplicateConversation) {
    const data = await api("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "duplicate", id: Number(target.dataset.duplicateConversation) }),
    });
    await reopenConversation(data.conversation.id);
    return;
  }
  if (target.dataset.deleteConversation) {
    if (confirm(t("confirm.deleteConversation"))) {
      await api("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", id: Number(target.dataset.deleteConversation) }),
      });
      if (Number(state.ask.conversationId) === Number(target.dataset.deleteConversation)) {
        state.ask.conversationId = null;
        state.ask.conversationTitle = "";
        state.ask.messages = [];
        localStorage.removeItem("studyhub.currentConversationId");
      }
      await openHistoryDrawer($("#historySearch")?.value || "");
    }
    return;
  }
  if (target.dataset.copyMessage) {
    await navigator.clipboard.writeText(target.dataset.copyMessage);
    toast(t("toast.copied"));
    return;
  }
  if (target.dataset.retryLast) {
    const lastUser = [...state.ask.messages].reverse().find((message) => message.role === "user");
    if (lastUser) {
      saveAskDraft(lastUser.text);
      renderAskGpt();
    }
    return;
  }
  if (target.dataset.view) return route(target.dataset.view);
  if (target.dataset.course && !target.dataset.week) return renderCourse(Number(target.dataset.course));
  if (target.dataset.week) return renderWeek(Number(target.dataset.course), target.dataset.week);
  if (target.dataset.preview) return route("file", { fileId: Number(target.dataset.preview), page: Number(target.dataset.page || 0) });
  if (target.dataset.open) return openOriginal(Number(target.dataset.open));
  if (target.dataset.star) return toggleStar(Number(target.dataset.star));
  if (target.dataset.context) return copyContext(Number(target.dataset.context));
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-material-select]")) {
    const id = Number(event.target.dataset.materialSelect);
    if (event.target.checked) state.selectedMaterialIds.add(id);
    else state.selectedMaterialIds.delete(id);
    const disabled = state.selectedMaterialIds.size === 0;
    if ($("#batchClassify")) $("#batchClassify").disabled = disabled;
    if ($("#batchStar")) $("#batchStar").disabled = disabled;
    if ($("#batchRemove")) $("#batchRemove").disabled = disabled;
    if ($("#inboxAssign")) $("#inboxAssign").disabled = disabled;
  }
});

$("#closeDrawer").addEventListener("click", () => {
  $("#fileDrawer").hidden = true;
  if (window.location.hash.startsWith("#/file/") && state.selectedFile?.course_id && state.selectedFile?.week_label) {
    recordRoute({ view: "week", courseId: Number(state.selectedFile.course_id), week: state.selectedFile.week_label }, true);
  }
});
$("#closeHistory").addEventListener("click", () => {
  $("#historyDrawer").hidden = true;
});
$("#clearHistory").addEventListener("click", async () => {
  if (!confirm(t("confirm.clearConversations"))) return;
  await api("/api/conversations", { method: "POST", body: JSON.stringify({ action: "clear" }) });
  state.ask.conversationId = null;
  state.ask.conversationTitle = "";
  localStorage.removeItem("studyhub.currentConversationId");
  await loadConversationList();
  renderHistoryList();
  renderAskGpt();
});
$("#historySearch").addEventListener("input", debounce(async () => {
  await loadConversationList($("#historySearch").value);
  renderHistoryList();
}, 200));
$("#openOriginal").addEventListener("click", () => state.selectedFile && openOriginal(state.selectedFile.id));
$("#askFileFull").addEventListener("click", () => state.selectedFile && openAskForFile(state.selectedFile.id));
$("#toggleAiExpanded").addEventListener("click", () => {
  const panel = document.querySelector(".drawer-panel");
  panel.classList.toggle("ai-expanded");
  $("#toggleAiExpanded").textContent = t(panel.classList.contains("ai-expanded") ? "file.compact" : "file.splitAi");
});
$("#starFile").addEventListener("click", () => state.selectedFile && toggleStar(state.selectedFile.id));
$("#studyCompleteFile").addEventListener("click", (event) => setMaterialStudyState(event.currentTarget.dataset.studyAction));
$("#studyReviewFile").addEventListener("click", (event) => setMaterialStudyState(event.currentTarget.dataset.studyAction));
$("#copyContext").addEventListener("click", () => state.selectedFile && copyContext(state.selectedFile.id));
$("#askBtn").addEventListener("click", askAboutFile);
$("#saveNote").addEventListener("click", saveNote);
$("#sidebarToggle").addEventListener("click", () => setSidebarCollapsed(!state.sidebarCollapsed));
$("#scanBtn").addEventListener("click", scanLibrary);
$("#uploadBtn").addEventListener("click", () => openAddMaterial());
$("#uploadCourse").addEventListener("change", populateUploadWeeks);
$("#chooseNativeFiles").addEventListener("click", chooseNativeMaterialFiles);
$("#uploadSubmit").addEventListener("click", uploadMaterial);
$("#courseSubmit").addEventListener("click", saveCourse);
$("#weekSubmit").addEventListener("click", saveWeek);
$("#openDuplicate").addEventListener("click", () => {
  const existingId = Number(state.duplicateImport?.items?.[0]?.id || 0);
  $("#duplicateDialog").close();
  if (existingId) route("file", { fileId: existingId });
});
$("#addDuplicateAnyway").addEventListener("click", async () => {
  $("#duplicateDialog").close();
  const result = await submitNativeImport("add_anyway");
  await loadBase();
  await route();
  toast(`${result.added} duplicate material${result.added === 1 ? "" : "s"} added`);
});

document.querySelectorAll(".rail-tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.ask.railTab = button.dataset.railTab;
    document.querySelectorAll(".rail-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
    document.querySelectorAll(".rail-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `rail-${state.ask.railTab}`));
  });
});

$("#drawerSplitHandle").addEventListener("pointerdown", (event) => {
  const panel = document.querySelector(".drawer-panel");
  if (!panel.classList.contains("ai-expanded")) return;
  const startX = event.clientX;
  const startWidth = parseFloat(getComputedStyle(panel).getPropertyValue("--studyhub-preview-width")) || 55;
  const rect = panel.getBoundingClientRect();
  $("#drawerSplitHandle").setPointerCapture(event.pointerId);
  const move = (moveEvent) => {
    const pct = Math.min(70, Math.max(35, startWidth + ((moveEvent.clientX - startX) / rect.width) * 100));
    panel.style.setProperty("--studyhub-preview-width", `${pct}%`);
    localStorage.setItem("studyhub.previewWidth", `${pct}%`);
  };
  const up = () => {
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", up);
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", up);
});

$("#drawerSplitHandle").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const panel = document.querySelector(".drawer-panel");
  if (!panel.classList.contains("ai-expanded")) return;
  event.preventDefault();
  const current = parseFloat(getComputedStyle(panel).getPropertyValue("--studyhub-preview-width")) || 55;
  const next = event.key === "Home" ? 35 : event.key === "End" ? 70 : Math.min(70, Math.max(35, current + (event.key === "ArrowLeft" ? -2 : 2)));
  panel.style.setProperty("--studyhub-preview-width", `${next}%`);
  localStorage.setItem("studyhub.previewWidth", `${next}%`);
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "a") {
    event.preventDefault();
    setAiMode(state.ask.mode === "focus" ? "compact" : "focus");
    route("ai");
  }
  if (event.key === "Escape") {
    if (!$("#historyDrawer").hidden) $("#historyDrawer").hidden = true;
    else if (state.ask.mode === "focus") {
      setAiMode("compact");
      route("ai");
    }
  }
});

async function enableDesktopFileDrop() {
  const listen = window.__TAURI__?.event?.listen;
  if (!state.health?.desktopMode || typeof listen !== "function") return;
  await listen("tauri://drag-drop", (event) => {
    const paths = event.payload?.paths || [];
    if (!paths.length) return;
    openAddMaterial(state.selectedCourseId || 0, state.selectedWeek || "");
    showNativeSelection(paths);
  });
}

$(".skip-link")?.addEventListener("click", (event) => {
  event.preventDefault();
  const main = $("#mainContent");
  main?.focus({ preventScroll: true });
  main?.scrollIntoView({ block: "start" });
});

loadBase()
  .then(async () => {
    i18n.apply(document);
    appLoaded = true;
    await enableDesktopFileDrop();
    if (!window.location.hash) history.replaceState({ studyhub: true, view: "home" }, "", routeHash({ view: "home" }));
    await restoreRouteFromLocation();
  })
  .catch((error) => {
    view.innerHTML = empty(error.message);
  });

window.addEventListener("popstate", () => {
  if (!appLoaded) return;
  restoreRouteFromLocation();
});

window.addEventListener("studyhub:languagechange", async () => {
  i18n.apply(document);
  applySidebarState();
  const libraryState = $("#libraryState");
  if (libraryState && !libraryState.hidden) {
    libraryState.textContent = t(state.health?.studyLibraryConnected ? "settings.libraryNeedsAttention" : "settings.libraryMissing");
  }
  populateUploadCourses();
  populateCourseTerms();
  if (appLoaded) await route(state.view, { history: false });
  if (!$("#fileDrawer")?.hidden && state.selectedFile) await openFileDrawer(state.selectedFile.id, 0, { history: false });
});
