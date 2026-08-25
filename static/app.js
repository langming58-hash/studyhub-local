const state = {
  courses: [],
  weeksByCourse: new Map(),
  health: null,
  view: "home",
  selectedCourseId: null,
  selectedWeek: "",
  selectedFile: null,
  csrfToken: "",
  weekFiles: [],
  weekFilter: "all",
  weekSort: "section",
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setTitle(title) {
  $("#pageTitle").textContent = title;
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
      text: "Context changed",
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
  return lines.length ? lines : ["No course selected"];
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
    { value: "question", label: "Current Question", enabled: Boolean(context.questionId || context.questionNumber) },
    { value: "file", label: "Current File", enabled: Boolean(context.fileId) },
    { value: "week", label: "Current Week", enabled: Boolean(context.week) },
    { value: "course", label: "Current Course", enabled: Boolean(context.course) },
  ];
}

function quickActionsForContext(context = state.ask.context) {
  if (!context.course) return [];
  if (context.questionId || context.questionNumber) {
    return ["Explain the question", "What is this asking?", "Check my answer"];
  }
  if (context.fileId) {
    const actions = ["Explain this file", "Summarize"];
    if ((context.materialType || "").toLowerCase().includes("slide") || /\.pptx?$/i.test(context.file || "")) actions.push("Explain current slide/page");
    return actions;
  }
  if (context.week) {
    return ["Explain this week", "Key concepts", "Important terminology", "Prepare me for tutorial"];
  }
  return ["Key concepts", "Important terminology"];
}

function courseById(id) {
  return state.courses.find((course) => Number(course.id) === Number(id));
}

function courseLabel(item = {}) {
  return item.display_code || item.display_course_code || item.code || item.course_code || "";
}

function scopeLabel(scope = state.ask.scope) {
  return {
    question: "This question",
    file: "This file",
    week: "This week",
    course: "This course",
    selected: "Selected sources",
  }[scope] || "This course";
}

function saveAskDraft(value) {
  state.ask.draft = value;
  localStorage.setItem("studyhub.askDraft", value);
}

function setAiMode(mode) {
  state.ask.mode = mode;
  localStorage.setItem("studyhub.aiMode", mode);
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\\\((.+?)\\\)|\$([^$\n]+)\$/g, (_m, a, b) => `<span class="math-inline">${a || b}</span>`);
}

function renderMarkdown(text = "") {
  const blocks = String(text).replace(/\r\n/g, "\n").split(/\n{2,}/);
  const html = [];
  let inCode = false;
  let code = [];
  let codeLang = "";
  const flushCode = () => {
    if (!inCode) return;
    html.push(`<pre><code data-lang="${escapeHtml(codeLang)}">${escapeHtml(code.join("\n"))}</code></pre>`);
    inCode = false;
    code = [];
    codeLang = "";
  };
  for (const block of blocks) {
    const lines = block.split("\n");
    if (lines[0].startsWith("```")) {
      inCode = true;
      codeLang = lines[0].slice(3).trim();
      code.push(...lines.slice(1));
      if (lines.at(-1).startsWith("```") && lines.length > 1) {
        code.pop();
        flushCode();
      }
      continue;
    }
    if (inCode) {
      code.push("", ...lines);
      if (lines.at(-1).startsWith("```")) {
        code.pop();
        flushCode();
      }
      continue;
    }
    const trimmed = block.trim();
    if (/^#{1,3}\s/.test(trimmed)) {
      const level = Math.min(trimmed.match(/^#+/)[0].length, 3);
      html.push(`<h${level}>${renderInlineMarkdown(trimmed.replace(/^#{1,3}\s*/, ""))}</h${level}>`);
    } else if (/^(\*|-)\s+/m.test(trimmed)) {
      html.push(`<ul>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^(\*|-)\s+/, ""))}</li>`).join("")}</ul>`);
    } else if (/^\d+\.\s+/m.test(trimmed)) {
      html.push(`<ol>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^\d+\.\s+/, ""))}</li>`).join("")}</ol>`);
    } else if (/^\|.+\|\n\|[-:\s|]+\|/.test(trimmed)) {
      const rows = lines.filter((line) => /^\|.*\|$/.test(line)).map((line) => line.split("|").slice(1, -1).map((cell) => cell.trim()));
      const head = rows[0] || [];
      const body = rows.slice(2);
      html.push(`<div class="table-wrap"><table><thead><tr>${head.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    } else if (/^\\\[([\s\S]+)\\\]$/.test(trimmed) || /^\$\$([\s\S]+)\$\$$/.test(trimmed)) {
      html.push(`<div class="math-block">${escapeHtml(trimmed.replace(/^\\\[|\\\]$|^\$\$|\$\$$/g, ""))}</div>`);
    } else if (/^>\s+/m.test(trimmed)) {
      html.push(`<blockquote>${lines.map((line) => renderInlineMarkdown(line.replace(/^>\s?/, ""))).join("<br>")}</blockquote>`);
    } else if (/^---+$/.test(trimmed)) {
      html.push("<hr>");
    } else {
      html.push(`<p>${lines.map(renderInlineMarkdown).join("<br>")}</p>`);
    }
  }
  flushCode();
  return html.join("");
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
  route("askgpt");
}

async function createConversation(context = state.ask.context, scope = state.ask.scope) {
  const data = await api("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "create", context, scope, title: "New study conversation" }),
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

function extensionIcon(ext = "") {
  const value = ext.toLowerCase();
  if (value === ".pdf") return "PDF";
  if ([".doc", ".docx"].includes(value)) return "DOC";
  if ([".ppt", ".pptx"].includes(value)) return "PPT";
  if ([".xls", ".xlsx", ".csv", ".tsv"].includes(value)) return "XLS";
  if ([".py", ".r", ".ipynb"].includes(value)) return "CODE";
  return "FILE";
}

function fileCard(file) {
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
        <button class="icon-button" data-star="${file.id}" aria-label="Star file">${star}</button>
      </header>
      <div class="chips">
        <span class="chip ${officialClass}">${escapeHtml(file.source_label || file.source || "Local file")}</span>
        <span class="chip">${escapeHtml(file.extension || "")}</span>
        <span class="chip">${formatSize(file.size)}</span>
        ${file.week_label ? `<span class="chip">${escapeHtml(file.week_label)}</span>` : ""}
        ${file.category ? `<span class="chip">${escapeHtml(file.category)}</span>` : ""}
        ${suspicious}
      </div>
      <div class="file-actions">
        <button class="button secondary" data-preview="${file.id}">Preview</button>
        <button class="button secondary" data-ask-file="${file.id}">Ask GPT</button>
        <button class="button secondary" data-open="${file.id}">Open</button>
        <button class="button secondary" data-context="${file.id}">Context</button>
      </div>
    </article>
  `;
}

async function loadBase() {
  const session = await api("/api/session");
  state.csrfToken = session.csrfToken || state.csrfToken || "";
  state.health = await api("/api/health");
  state.courses = await api("/api/courses");
  await Promise.all(
    state.courses.map(async (course) => {
      state.weeksByCourse.set(Number(course.id), await api(`/api/weeks?course_id=${course.id}`));
    }),
  );
  $("#libraryState").textContent = state.health.studyLibraryConnected
    ? `${state.health.filesIndexed} indexed files`
    : "Library missing";
  renderCourseList();
  populateUploadCourses();
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

function renderCourseList() {
  $("#courseList").innerHTML = state.courses
    .map((course) => {
      const week = latestWeek(course);
      return `
        <div class="course-pill-row">
          <button class="course-pill" data-course="${course.id}">
            <strong>${escapeHtml(courseLabel(course))}</strong>
            <span>${course.file_count || 0} files · ${escapeHtml(week?.week_label || "No week")}</span>
          </button>
          <button class="mini-ask" data-ask-course="${course.id}">Ask</button>
        </div>
      `;
    })
    .join("");
}

function populateUploadCourses() {
  $("#uploadCourse").innerHTML = state.courses
    .map((course) => `<option value="${course.id}">${escapeHtml(courseLabel(course))}</option>`)
    .join("");
  $("#uploadWeek").innerHTML = Array.from({ length: 12 }, (_, i) => {
    const label = `Week ${String(i + 1).padStart(2, "0")}`;
    return `<option>${label}</option>`;
  }).join("");
}

async function renderHome() {
  setTitle("Home");
  const recent = await api("/api/recent");
  const wrong = await api("/api/wrong-questions");
  const totalCourses = state.courses.length;
  const totalFiles = state.health?.filesIndexed || 0;
  const suspicious = state.health?.suspiciousFiles || 0;
  view.innerHTML = `
    <section class="focus-strip">
      ${state.courses.map(focusCourseCard).join("")}
    </section>
    <div class="grid cols-3">
      <section class="metric-card"><p class="muted">Courses</p><div class="metric">${totalCourses}</div></section>
      <section class="metric-card"><p class="muted">Indexed files</p><div class="metric">${totalFiles}</div></section>
      <section class="metric-card"><p class="muted">Needs review</p><div class="metric">${suspicious}</div></section>
    </div>
    <section class="section-block">
      <div class="section-head">
        <h2>Recent Files</h2>
        <button class="button secondary" data-view="search">Search Library</button>
      </div>
      <div class="grid">${recent.length ? recent.map(fileCard).join("") : empty("No indexed files yet.")}</div>
    </section>
    <section class="section-block">
      <div class="section-head">
        <h2>Review Queue</h2>
        <button class="button secondary" data-view="wrong">Open Queue</button>
      </div>
      ${wrong.length ? `<div class="grid">${wrong.map(wrongRow).join("")}</div>` : empty("No wrong-question records yet.")}
    </section>
  `;
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
        <h2>${escapeHtml(week?.week_label || "No week")}</h2>
      </div>
      <div class="progress-track"><span style="width:${pct}%"></span></div>
      <div class="focus-meta">
        <span>${done}/${weeks.length} weeks</span>
        <button class="button secondary" data-course="${course.id}" data-week="${escapeHtml(week?.week_label || "")}">Open</button>
      </div>
    </article>
  `;
}

function courseSummary(course) {
  const weeks = weeksFor(course.id);
  const done = weeks.filter((week) => week.has_materials).length;
  return `
    <article class="file-card">
      <header>
        <div>
          <div class="file-name">${escapeHtml(courseLabel(course))}</div>
          <p class="muted">${escapeHtml(course.name)}</p>
        </div>
      </header>
      <div class="chips">
        <span class="chip">${course.file_count || 0} files</span>
        <span class="chip">${done}/${weeks.length} weeks with files</span>
        <span class="chip">latest Week ${String(course.latest_week || "--").padStart(2, "0")}</span>
      </div>
      <div class="file-actions">
        <button class="button secondary" data-course="${course.id}">Open Course</button>
        <button class="button secondary" data-ask-course="${course.id}">Ask GPT</button>
      </div>
    </article>
  `;
}

async function renderCourses() {
  setTitle("Courses");
  view.innerHTML = `
    <section class="section-block">
      <div class="grid cols-2">${state.courses.map(courseSummary).join("")}</div>
    </section>
  `;
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
          <div class="grid">${files.length ? files.slice(0, 6).map(fileCard).join("") : empty("No files indexed for this week.")}</div>
        </section>
      `;
    }),
  );
  view.innerHTML = blocks.join("");
}

async function renderCourse(courseId) {
  const course = courseById(courseId);
  state.selectedCourseId = courseId;
  setTitle(course ? courseLabel(course) : "Course");
  const weeks = weeksFor(courseId);
  const files = await api(`/api/files?course_id=${courseId}`);
  const bySection = countBy(files, (file) => file.section || "Other");
  view.innerHTML = `
    <section class="course-hero">
      <div>
        <p class="muted">${escapeHtml(courseLabel(course))}</p>
        <h2>${escapeHtml(course?.name || "")}</h2>
      </div>
      <div class="mini-bars">
        ${Object.entries(bySection).map(([label, count]) => `<span><b>${count}</b>${escapeHtml(label)}</span>`).join("")}
      </div>
      <div class="course-actions">
        <button class="button secondary" data-ask-course="${courseId}">Ask GPT about this course</button>
      </div>
    </section>
    <section class="section-block">
      <h2>Weeks</h2>
      <div class="week-grid">
        ${weeks.map((week) => `
          <button class="week-tile ${week.has_materials ? "has-materials" : ""}" data-week="${week.week_label}" data-course="${courseId}">
            <strong>${escapeHtml(week.week_label)}</strong>
            <span class="muted">${week.file_count || 0} files</span>
          </button>
        `).join("")}
      </div>
    </section>
    <section class="section-block">
      <h2>All Course Files</h2>
      <div class="grid">${files.length ? files.slice(0, 18).map(fileCard).join("") : empty("No files indexed for this course.")}</div>
    </section>
  `;
}

async function renderWeek(courseId, weekLabel) {
  const course = courseById(courseId);
  state.selectedCourseId = courseId;
  state.selectedWeek = weekLabel;
  state.weekFiles = await api(`/api/files?course_id=${courseId}&week=${encodeURIComponent(weekLabel)}`);
  state.weekFilter = "all";
  state.weekSort = "section";
  setTitle(`${course?.code || "Course"} · ${weekLabel}`);
  view.innerHTML = `
    <section class="section-block">
      <div class="section-head">
        <div>
          <p class="muted">${escapeHtml(course?.name || "")}</p>
          <h2>${escapeHtml(weekLabel)}</h2>
        </div>
        <div class="top-actions">
          <button class="button secondary" id="weekPreviewGpt">Preview with GPT</button>
          <button class="button secondary" data-ask-week="${courseId}" data-week-label="${escapeHtml(weekLabel)}">Ask GPT</button>
          <button class="button secondary" data-course="${courseId}">Back to Course</button>
        </div>
      </div>
      <div class="toolbar">
        <select id="weekFilter">
          <option value="all">All files</option>
          <option value="01 Course Materials">Course materials</option>
          <option value="02 Exercises">Exercises</option>
          <option value="Lecture">Lecture</option>
          <option value="Tutorial">Tutorial</option>
          <option value="Workshop">Workshop</option>
          <option value="Lab">Lab</option>
          <option value="Quiz">Quiz</option>
          <option value="Practice">Practice</option>
          <option value="Revision">Revision</option>
          <option value="official">Official only</option>
          <option value="user">My work / AI</option>
        </select>
        <select id="weekSort">
          <option value="section">Section</option>
          <option value="name">Name</option>
          <option value="type">Type</option>
          <option value="size">Size</option>
        </select>
      </div>
    </section>
    <section id="weekBridgePreview" class="notice" hidden></section>
    <section id="weekFiles" class="grid"></section>
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

  $("#weekFiles").innerHTML = files.length ? files.map(fileCard).join("") : empty("No files match this filter.");
}

function countBy(items, fn) {
  return items.reduce((acc, item) => {
    const key = fn(item);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

async function renderSearch() {
  setTitle("Search");
  view.innerHTML = `
    <section class="section-block sticky-tools">
      <div class="searchbar">
        <input id="searchInput" placeholder="Search filename or local extracted text" autofocus />
        <select id="searchCourse">
          <option value="">All courses</option>
          ${state.courses.map((course) => `<option value="${course.id}">${escapeHtml(courseLabel(course))}</option>`).join("")}
        </select>
        <select id="searchScope">
          <option value="">All material</option>
          <option>01 Course Materials</option>
          <option>02 Exercises</option>
          <option>Lecture</option>
          <option>Tutorial</option>
          <option>Workshop</option>
        </select>
      </div>
    </section>
    <section id="searchResults" class="grid"></section>
  `;
  $("#searchInput").addEventListener("input", debounce(runSearch, 220));
  $("#searchCourse").addEventListener("change", runSearch);
  $("#searchScope").addEventListener("change", runSearch);
  await runSearch();
}

async function runSearch() {
  const q = $("#searchInput")?.value || "";
  const course = $("#searchCourse")?.value || "";
  const scope = $("#searchScope")?.value || "";
  const results = await api(`/api/search?q=${encodeURIComponent(q)}&course_id=${course}&scope=${encodeURIComponent(scope)}`);
  $("#searchResults").innerHTML = results.length ? results.map(fileCard).join("") : empty("No matching files.");
}

async function renderAskGpt() {
  setTitle("Ask GPT");
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
          <p class="eyebrow">Course learning assistant</p>
          <div class="ask-title-row">
            <h2>${escapeHtml(state.ask.conversationTitle || "AI Study Workspace")}</h2>
            ${state.ask.conversationId ? `<span class="chip">Saved locally</span>` : `<span class="chip">New chat</span>`}
          </div>
        </div>
        <div class="ask-tools">
          <button class="button secondary" id="historyButton">History</button>
          <button class="button secondary" id="newAskChat">New Chat</button>
          <button class="button secondary" id="toggleFocusMode">${state.ask.mode === "focus" ? "Exit Focus" : "Open AI Workspace"}</button>
        </div>
      </div>
      <div class="ask-context">
        <div>
          <h3>Asking about</h3>
          <div class="context-lines">${askContextLines().map((line) => `<span>${escapeHtml(line)}</span>`).join("")}</div>
        </div>
        <label>Scope
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
          <div class="composer-context">Effective scope: ${escapeHtml(scopeLabel())}${state.ask.context.file ? ` · ${escapeHtml(state.ask.context.file)}` : ""}</div>
          <textarea id="askPagePrompt" placeholder="Ask about these materials...">${escapeHtml(state.ask.draft)}</textarea>
        </div>
        <button class="button primary" id="sendAskPage">Send</button>
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
    return `<div class="notice">Ask about the selected official course materials. GPT answers must cite sources.</div>`;
  }
  return state.ask.messages
    .map((message) => {
      if (message.role === "system") return `<div class="context-change">${escapeHtml(message.text)}</div>`;
      const sources = message.sources?.length ? `<div class="source-list"><h3>Sources</h3>${message.sources.map(sourceCard).join("")}</div>` : "";
      const badge = message.role === "assistant" ? `<span class="chip">GPT explanation</span>` : `<span class="chip">My prompt</span>`;
      return `
        <article class="chat-message ${message.role}">
          <div class="message-head">${badge}${message.status ? `<span class="chip">${escapeHtml(message.status)}</span>` : ""}</div>
          <div class="message-body">${message.role === "assistant" ? renderMarkdown(message.text) : `<p>${escapeHtml(message.text)}</p>`}</div>
          ${
            message.role === "assistant"
              ? `<div class="message-actions"><button class="tiny-button" data-copy-message="${escapeHtml(message.text)}">Copy</button><button class="tiny-button" data-retry-last="1">Retry</button>${sources ? `<button class="tiny-button" data-show-sources="1">Sources</button>` : ""}</div>`
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
      <span class="chip official">Official course source</span>
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
          <h3>${label}</h3>
          ${groups[label].map(historyItem).join("")}
        </section>
      `,
    )
    .join("");
  $("#historyList").innerHTML = html || empty("No saved AI conversations yet.");
}

function historyItem(row) {
  const active = Number(row.id) === Number(state.ask.conversationId) ? "active" : "";
  const source = row.source_available === false ? "Source unavailable" : [courseLabel(row), row.week_label, row.filename].filter(Boolean).join(" · ");
  return `
    <article class="history-item ${active}">
      <button class="button secondary" data-open-conversation="${row.id}">${escapeHtml(row.title)}</button>
      <div class="history-meta">${escapeHtml(source || "General study chat")} · ${row.message_count || 0} messages</div>
      <div class="history-actions">
        <button class="tiny-button" data-rename-conversation="${row.id}">Rename</button>
        <button class="tiny-button" data-duplicate-conversation="${row.id}">Duplicate</button>
        <button class="tiny-button" data-delete-conversation="${row.id}">Delete</button>
      </div>
    </article>
  `;
}

async function sendAskPagePrompt() {
  const prompt = $("#askPagePrompt").value.trim();
  if (!prompt) return;
  const context = contextForScope();
  if (!context.course) {
    state.ask.messages.push({ role: "assistant", text: "Select a course, week, file, or teacher question before asking GPT.", status: "no_context", sources: [] });
    renderAskGpt();
    return;
  }
  const qn = detectQuestionNumber(prompt);
  if (qn && state.ask.scope === "question") context.questionNumber = qn;
  context.scope = state.ask.scope;
  state.ask.messages.push({ role: "user", text: prompt, context: { ...context } });
  state.ask.messages.push({ role: "system", text: "Searching your materials..." });
  $("#askConversation").innerHTML = renderAskMessages();
  $("#sendAskPage").disabled = true;
  $("#askPagePrompt").disabled = true;
  try {
    state.ask.messages[state.ask.messages.length - 1].text = "Preparing sources… Asking OpenAI…";
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
    renderAskGpt();
  } catch (error) {
    state.ask.messages.pop();
    state.ask.messages.push({ role: "assistant", text: error.message, status: "error", sources: [] });
    renderAskGpt();
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
  setTitle("Starred");
  const rows = await api("/api/starred");
  view.innerHTML = `<section class="section-block"><div class="grid">${rows.length ? rows.map(fileCard).join("") : empty("No starred files yet.")}</div></section>`;
}

async function renderPractice() {
  setTitle("Practice");
  view.innerHTML = `
    <section class="section-block sticky-tools">
      <div class="toolbar practice-toolbar">
        <select id="practiceCourse">
          <option value="">All courses</option>
          ${state.courses.map((course) => `<option value="${escapeHtml(course.code)}">${escapeHtml(courseLabel(course))}</option>`).join("")}
        </select>
        <select id="practiceWeek">
          <option value="">All weeks</option>
          ${Array.from({ length: 12 }, (_, i) => `<option>Week ${String(i + 1).padStart(2, "0")}</option>`).join("")}
        </select>
        <select id="practiceType">
          <option value="">All types</option>
          <option>Tutorial</option>
          <option>Workshop</option>
          <option>Lab</option>
          <option>Quiz</option>
          <option>Practice</option>
          <option>Revision</option>
        </select>
      </div>
    </section>
    <section id="practiceResults" class="grid"></section>
  `;
  $("#practiceCourse").addEventListener("change", loadPracticeQuestions);
  $("#practiceWeek").addEventListener("change", loadPracticeQuestions);
  $("#practiceType").addEventListener("change", loadPracticeQuestions);
  await loadPracticeQuestions();
}

async function renderWrong() {
  setTitle("Wrong Questions");
  const rows = await api("/api/wrong-questions");
  view.innerHTML = `
    <section class="section-block">
      ${rows.length ? `<div class="grid">${rows.map(wrongRow).join("")}</div>` : empty("No wrong-question records yet.")}
    </section>
  `;
}

async function renderExam() {
  setTitle("Exam Review");
  const blocks = state.courses.map((course) => {
    const weeks = weeksFor(course.id);
    return `
      <section class="section-block">
        <div class="section-head">
          <div>
            <p class="muted">${escapeHtml(courseLabel(course))}</p>
            <h2>${escapeHtml(course.name)}</h2>
          </div>
          <button class="button secondary" data-course="${course.id}">Course</button>
        </div>
        <div class="week-grid compact">
          ${weeks.map((week) => `
            <button class="week-tile ${week.has_materials ? "has-materials" : ""}" data-week="${week.week_label}" data-course="${course.id}">
              <strong>${escapeHtml(week.week_label.replace("Week ", "W"))}</strong>
              <span class="muted">${week.file_count || 0}</span>
            </button>
          `).join("")}
        </div>
      </section>
    `;
  });
  view.innerHTML = blocks.join("");
}

function wrongRow(row) {
  return `
    <article class="file-card">
      <div class="file-name">${escapeHtml(row.course_id || "")} ${escapeHtml(row.week_label || "")}</div>
      <p class="muted">${escapeHtml(row.question_ref || "Question reference")}</p>
      <div class="chips"><span class="chip">${escapeHtml(row.mastery || "new")}</span></div>
    </article>
  `;
}

async function renderSettings() {
  setTitle("Settings");
  state.health = await api("/api/health");
  const ai = await api("/api/ai-status");
  const askReady = ai.openAI === "Configured" && ai.vectorStore === "Configured" ? "Ready" : "Not ready";
  view.innerHTML = `
    <section class="section-block">
      <h2>Study Library</h2>
      <div class="grid cols-3">
        <section class="metric-card"><p class="muted">Indexed Files</p><div class="metric">${escapeHtml(state.health.filesIndexed || 0)}</div></section>
        <section class="metric-card"><p class="muted">OpenAI</p><div class="metric small">${escapeHtml(ai.openAI)}</div></section>
        <section class="metric-card"><p class="muted">Vector Store</p><div class="metric small">${escapeHtml(ai.vectorStore)}</div></section>
        <section class="metric-card"><p class="muted">Ask GPT</p><div class="metric small">${escapeHtml(askReady)}</div></section>
        <section class="metric-card"><p class="muted">PDF Text</p><div class="metric small">${escapeHtml(state.health.pdfTextExtraction || "Unknown")}</div></section>
      </div>
      ${(state.health.extractionWarnings || []).length ? `<div class="notice">${state.health.extractionWarnings.map(escapeHtml).join("<br>")}</div>` : ""}
    </section>
    <section class="section-block">
      <h2>AI Bridge</h2>
      <div class="settings-list">
        <div><span>Last AI Sync</span><strong>${escapeHtml(ai.lastAISync || "Never")}</strong></div>
        <div><span>Local Indexed Files</span><strong>${escapeHtml(ai.indexedFiles || 0)}</strong></div>
        <div><span>Vector Indexed Files</span><strong>${escapeHtml(ai.vectorIndexedFiles || 0)}</strong></div>
        <div><span>Vector Store Status</span><strong>${escapeHtml(ai.vectorStoreLabel || ai.vectorStore || "Not configured")}</strong></div>
      </div>
    </section>
  `;
}

function empty(text) {
  return `<div class="notice">${escapeHtml(text)}</div>`;
}

async function route(viewName = state.view) {
  state.view = viewName;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  if (viewName === "home") return renderHome();
  if (viewName === "thisWeek") return renderThisWeek();
  if (viewName === "courses") return renderCourses();
  if (viewName === "search") return renderSearch();
  if (viewName === "askgpt") return renderAskGpt();
  if (viewName === "starred") return renderStarred();
  if (viewName === "practice") return renderPractice();
  if (viewName === "wrong") return renderWrong();
  if (viewName === "exam") return renderExam();
  if (viewName === "settings") return renderSettings();
}

async function openFileDrawer(fileId, page = 0) {
  const file = await api(`/api/file?id=${fileId}`);
  state.selectedFile = file;
  $("#drawerMeta").textContent = `${courseLabel(file)} · ${file.week_label || ""} · ${file.category || ""}`;
  $("#drawerTitle").textContent = file.filename;
  $("#contextIndicator").innerHTML = [courseLabel(file), file.week_label, file.exercise_type || file.category, file.filename]
    .filter(Boolean)
    .map((line) => `<span>${escapeHtml(line)}</span>`)
    .join("");
  $("#fileDetails").innerHTML = `
    <div><span>Filename</span><strong>${escapeHtml(file.filename)}</strong></div>
    <div><span>Type</span><strong>${escapeHtml(file.mime_type || file.extension || "Unknown")}</strong></div>
    <div><span>Index</span><strong>${escapeHtml(file.ai_index_status || "Unknown")}</strong></div>
  `;
  $("#extractedText").textContent = file.extractedText || "No local text preview available. Open the original file for the authoritative version.";
  const pageHash = page ? `#page=${page}` : "";
  $("#previewFrame").innerHTML = `<iframe title="Preview" src="/preview/${file.id}${pageHash}"></iframe>`;
  $("#askResponse").textContent = "";
  $("#askPrompt").value = "";
  $("#noteBody").value = "";
  await loadFileNotes(file.id);
  $("#fileDrawer").hidden = false;
  const savedWidth = localStorage.getItem("studyhub.previewWidth");
  if (savedWidth) document.querySelector(".drawer-panel").style.setProperty("--studyhub-preview-width", savedWidth);
}

async function loadFileNotes(fileId) {
  const notes = await api(`/api/notes?targetType=file&targetId=${fileId}`);
  $("#fileNotes").innerHTML = notes.length
    ? notes.map(noteCard).join("")
    : `<div class="notice compact">No user notes for this file yet.</div>`;
}

function noteCard(note) {
  return `
    <article class="note-card">
      <div class="chips"><span class="chip">User note</span><span class="chip">${escapeHtml(note.updated_at || "")}</span></div>
      <p>${escapeHtml(note.body)}</p>
    </article>
  `;
}

async function saveNote() {
  if (!state.selectedFile) return;
  const body = $("#noteBody").value.trim();
  if (!body) {
    toast("Write a note first");
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
  toast("Note saved");
}

async function openAskForFile(fileId, prompt = "") {
  const file = await api(`/api/file?id=${fileId}`);
  state.selectedFile = file;
  setAskContext(fileAskContext(file), { prompt });
  $("#fileDrawer").hidden = true;
  route("askgpt");
}

async function openOriginal(fileId) {
  await api(`/api/open/${fileId}`, { method: "POST" });
  toast("Original file opened");
}

async function toggleStar(fileId) {
  const result = await api(`/api/star/${fileId}`, { method: "POST" });
  toast(result.starred ? "Starred" : "Unstarred");
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
  toast("Context copied");
}

async function askAboutFile() {
  if (!state.selectedFile) return;
  const prompt = $("#askPrompt").value.trim();
  $("#askBtn").disabled = true;
  $("#askResponse").textContent = "Searching official course materials...\nGenerating explanation...";
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
  route("askgpt");
}

async function loadPracticeQuestions() {
  const course = $("#practiceCourse")?.value || "";
  const week = $("#practiceWeek")?.value || "";
  const type = $("#practiceType")?.value || "";
  const rows = await api(`/api/questions?course=${encodeURIComponent(course)}&week=${encodeURIComponent(week)}&type=${encodeURIComponent(type)}`);
  $("#practiceResults").innerHTML = rows.length
    ? rows.map(questionCard).join("")
    : empty("No suitable teacher-provided question was found in the indexed official course materials.");
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
        <button class="button secondary" data-preview="${row.source_file_id}">Open Source File</button>
        <button class="button secondary" data-ask-question="${row.id}" data-source-file="${row.source_file_id}" data-course-code="${escapeHtml(row.course_code)}" data-week-label="${escapeHtml(row.week_label || "")}" data-exercise-type="${escapeHtml(row.exercise_type || "")}" data-question-number="${escapeHtml(row.question_number || "")}" data-filename="${escapeHtml(row.filename || "")}">Ask GPT</button>
        <button class="button secondary" data-ask-question="${row.id}" data-source-file="${row.source_file_id}" data-course-code="${escapeHtml(row.course_code)}" data-week-label="${escapeHtml(row.week_label || "")}" data-exercise-type="${escapeHtml(row.exercise_type || "")}" data-question-number="${escapeHtml(row.question_number || "")}" data-filename="${escapeHtml(row.filename || "")}" data-question-prompt="Check my answer">Check with GPT</button>
        <button class="button secondary" data-context="${row.source_file_id}">Prepare Context</button>
      </div>
    </article>
  `;
}

async function scanLibrary() {
  $("#scanBtn").disabled = true;
  $("#scanBtn").textContent = "Scanning";
  try {
    await api("/api/scan", { method: "POST" });
    await loadBase();
    await route();
    toast("Library scanned");
  } finally {
    $("#scanBtn").disabled = false;
    $("#scanBtn").textContent = "↻ Scan Library";
  }
}

async function uploadMaterial(event) {
  event.preventDefault();
  const files = $("#uploadFile").files;
  if (!files.length) return;
  const form = new FormData();
  form.append("course_id", $("#uploadCourse").value);
  form.append("week", $("#uploadWeek").value);
  form.append("section", $("#uploadSection").value);
  form.append("category", $("#uploadCategory").value.trim() || "Uploaded");
  Array.from(files).forEach((file) => form.append("files", file));
  await fetch("/api/upload", { method: "POST", headers: { "X-StudyHub-CSRF": state.csrfToken }, body: form }).then(async (response) => {
    if (!response.ok) throw new Error((await response.json()).error || "Upload failed");
  });
  $("#uploadDialog").close();
  await loadBase();
  await route();
  toast("Material added");
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
  if (target.dataset.askCourse) {
    setAskContext(currentCourseContext(Number(target.dataset.askCourse)), { scope: "course" });
    route("askgpt");
    return;
  }
  if (target.dataset.askWeek) {
    setAskContext(currentWeekContext(Number(target.dataset.askWeek), target.dataset.weekLabel), { scope: "week" });
    route("askgpt");
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
      { scope: "question", prompt: target.dataset.questionPrompt || "Explain the question" },
    );
    route("askgpt");
    return;
  }
  if (target.dataset.openConversation) {
    await reopenConversation(Number(target.dataset.openConversation));
    return;
  }
  if (target.dataset.renameConversation) {
    const row = state.ask.history.find((item) => Number(item.id) === Number(target.dataset.renameConversation));
    const title = prompt("Rename conversation", row?.title || "");
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
    if (confirm("Delete this local AI conversation?")) {
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
    toast("Copied");
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
  if (target.dataset.view) route(target.dataset.view);
  if (target.dataset.course && !target.dataset.week) renderCourse(Number(target.dataset.course));
  if (target.dataset.week) renderWeek(Number(target.dataset.course), target.dataset.week);
  if (target.dataset.preview) openFileDrawer(Number(target.dataset.preview), Number(target.dataset.page || 0));
  if (target.dataset.open) openOriginal(Number(target.dataset.open));
  if (target.dataset.star) toggleStar(Number(target.dataset.star));
  if (target.dataset.context) copyContext(Number(target.dataset.context));
});

$("#closeDrawer").addEventListener("click", () => {
  $("#fileDrawer").hidden = true;
});
$("#closeHistory").addEventListener("click", () => {
  $("#historyDrawer").hidden = true;
});
$("#clearHistory").addEventListener("click", async () => {
  if (!confirm("Clear all saved AI conversations?")) return;
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
  $("#toggleAiExpanded").textContent = panel.classList.contains("ai-expanded") ? "Compact AI" : "Expand AI";
});
$("#starFile").addEventListener("click", () => state.selectedFile && toggleStar(state.selectedFile.id));
$("#copyContext").addEventListener("click", () => state.selectedFile && copyContext(state.selectedFile.id));
$("#askBtn").addEventListener("click", askAboutFile);
$("#saveNote").addEventListener("click", saveNote);
$("#scanBtn").addEventListener("click", scanLibrary);
$("#uploadBtn").addEventListener("click", () => $("#uploadDialog").showModal());
$("#uploadSubmit").addEventListener("click", uploadMaterial);

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

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "a") {
    event.preventDefault();
    setAiMode(state.ask.mode === "focus" ? "compact" : "focus");
    route("askgpt");
  }
  if (event.key === "Escape") {
    if (!$("#historyDrawer").hidden) $("#historyDrawer").hidden = true;
    else if (state.ask.mode === "focus") {
      setAiMode("compact");
      route("askgpt");
    }
  }
});

loadBase()
  .then(() => route("home"))
  .catch((error) => {
    view.innerHTML = empty(error.message);
  });
