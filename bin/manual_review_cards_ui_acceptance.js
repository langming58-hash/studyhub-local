#!/usr/bin/env node
"use strict";

// Executable frontend acceptance for Manual Review Cards V1. It exercises the
// pure render module and then drives the real `app.js` review flow inside a
// sandbox with a stubbed `api` transport.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const ui = require(path.join(ROOT, "static", "review-cards-ui.js"));
const appSource = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
const t = (key, vars = {}) =>
  String(key).replace(/\{(\w+)\}/g, (_m, n) => (vars[n] !== undefined ? String(vars[n]) : `{${n}}`));

function card(overrides) {
  return Object.assign(
    {
      id: 1,
      front: "What is a page?",
      back: "A page answer",
      anchor_page: 2,
      anchor_state: "ok",
      archived: false,
      filename: "notes.pdf",
      file_id: 21,
    },
    overrides || {},
  );
}

// Pure render checks.
const okChip = ui.anchorChip(card(), t, escapeHtml);
check(
  "ok_anchor_is_source_button_with_file_and_page",
  okChip.includes('data-card-source="1"') && okChip.includes('data-card-source-file="21"') && okChip.includes('data-card-source-page="2"'),
);
const staleChip = ui.anchorChip(card({ anchor_state: "stale" }), t, escapeHtml);
check("stale_anchor_is_not_actionable", !staleChip.includes("data-card-source") && staleChip.includes("file.pageAnchorStale"));
check("unanchored_anchor_is_empty", ui.anchorChip(card({ anchor_page: null }), t, escapeHtml) === "");

const hiddenView = ui.cardViewHtml(card(), false, t, escapeHtml);
check("hidden_card_shows_reveal", hiddenView.includes("data-card-reveal=\"1\"") && !hiddenView.includes("review-card-back"));
const revealedView = ui.cardViewHtml(card(), true, t, escapeHtml);
check("revealed_card_shows_answer_and_grades_hook", revealedView.includes("A page answer") && !revealedView.includes("data-card-reveal"));
const gradeBar = ui.gradeBar(card(), t, escapeHtml);
check("grade_bar_has_four_grades", ["again", "hard", "good", "easy"].every((g) => gradeBar.includes(`data-grade="${g}"`)));

const xssView = ui.cardViewHtml(card({ front: "<script>alert(1)</script>", back: "<img onerror=1>" }), true, t, escapeHtml);
check("card_text_is_escaped", !xssView.includes("<script>") && !xssView.includes("<img") && xssView.includes("&lt;script&gt;"));

// Sandbox the real app.js review flow.
function makeElement(selector) {
  return {
    selector,
    hidden: false,
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
    dataset: {},
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {},
    removeEventListener() {},
    querySelector() {
      return makeElement(`${selector} > *`);
    },
    querySelectorAll() {
      return [];
    },
    close() {},
    showModal() {},
    setPointerCapture() {},
    getBoundingClientRect() {
      return { width: 600 };
    },
  };
}

const elements = new Map();
const documentShim = {
  querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, makeElement(selector));
    return elements.get(selector);
  },
  querySelectorAll() {
    return [];
  },
  createElement(tag) {
    return makeElement(`<${tag}>`);
  },
  addEventListener() {},
  removeEventListener() {},
};

const i18n = {
  t,
  materialType: (v) => String(v || "Other"),
  catalogs: { en: {} },
  learningUnit: (v) => String(v || ""),
  apply() {},
  setPreference() {},
};

const posted = [];
let fetchHandler = () => ({ ok: false, status: 500, statusText: "ERR", json: async () => ({}) });
const sandbox = {
  window: null,
  document: documentShim,
  addEventListener() {},
  removeEventListener() {},
  open() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  history: { pushState() {}, replaceState() {} },
  location: { hash: "" },
  fetch: (url, options) => fetchHandler(url, options),
  Headers,
  Response,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 0),
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  console,
  StudyHubI18n: i18n,
  StudyHubReviewCardsUI: ui,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  appSource +
    "\n;globalThis.__test = { api, renderReviewCards, renderReviewCardView, gradeReviewCard, reviewCardSession, get viewHtml() { return view.innerHTML; } };",
  sandbox,
);
const app = sandbox.__test;

async function main() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  const queue = [
    card({ id: 11, front: "Due one", back: "Answer one", anchor_state: "ok", file_id: 31, anchor_page: 2 }),
    card({ id: 12, front: "Due two", back: "Answer two", anchor_state: "stale", file_id: 31, anchor_page: 2 }),
  ];
  let failNextGrade = false;
  fetchHandler = async (url, options = {}) => {
    if (url === "/api/review-cards/queue") return { ok: true, status: 200, json: async () => queue };
    if (url === "/api/review-cards" && (options.method || "GET").toUpperCase() === "POST") {
      if (failNextGrade) {
        failNextGrade = false;
        return { ok: false, status: 500, statusText: "Server Error", json: async () => ({ error: "boom" }) };
      }
      posted.push(JSON.parse(options.body));
      return { ok: true, status: 200, json: async () => card({}) };
    }
    return { ok: true, status: 200, json: async () => [] };
  };

  await app.renderReviewCards();
  check("review_session_renders_due_count", app.viewHtml.includes("cards.dueCount"));
  check("review_session_renders_first_question", app.viewHtml.includes("Due one"));
  check("review_session_does_not_reveal_answer_yet", !app.viewHtml.includes("Answer one"));

  app.reviewCardSession.reveal = true;
  app.renderReviewCardView();
  check("reveal_shows_answer", app.viewHtml.includes("Answer one"));
  check("reveal_shows_four_grades", ["again", "hard", "good", "easy"].every((g) => app.viewHtml.includes(`data-grade="${g}"`)));
  check("source_button_carries_file_and_page", app.viewHtml.includes('data-card-source-file="31"') && app.viewHtml.includes('data-card-source-page="2"'));

  await app.gradeReviewCard(11, "good");
  check("grading_posts_review_action", posted.length === 1 && posted[0].action === "review" && posted[0].id === 11 && posted[0].grade === "good");

  app.reviewCardSession.busy = true;
  await app.gradeReviewCard(12, "again");
  check("busy_guard_prevents_double_submit", posted.length === 1);

  app.reviewCardSession.busy = false;
  failNextGrade = true;
  await app.gradeReviewCard(11, "good");
  check("failed_grade_resets_busy", app.reviewCardSession.busy === false && posted.length === 1);
  await app.gradeReviewCard(11, "good");
  check("grade_retries_after_failure", posted.length === 2 && posted[1].grade === "good");

  return failures;
}

main()
  .then((failed) => {
    if (failed > 0) {
      console.error(`Manual review cards UI failures: ${failed}`);
      process.exit(1);
    }
    console.log("Manual review cards UI acceptance: PASS");
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
