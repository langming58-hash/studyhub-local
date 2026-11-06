#!/usr/bin/env node
"use strict";

// Executable frontend/Reader integration test for the Phase 4 page-state fix.
//
// `static/app.js` is evaluated in a sandbox with a minimal DOM shim and a stubbed
// Reader at its public `window.StudyHubReader.open(...)` boundary. The test drives
// the real `openFileDrawer()` and `saveNote()` code paths together with the real
// `noteAnchorPayload()` helper from `static/notes-ui.js`, so the regression is
// exercised end-to-end at the boundary where the Reader reports its page.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const appSource = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
const notesUi = require(path.join(ROOT, "static", "notes-ui.js"));

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}

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
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() {
      return makeElement(`${selector} > *`);
    },
    querySelectorAll() {
      return [];
    },
    close() {},
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

function interpolate(key, vars) {
  return String(key).replace(/\{(\w+)\}/g, (_match, name) =>
    vars && vars[name] !== undefined ? String(vars[name]) : `{${name}}`,
  );
}

const i18n = {
  t: interpolate,
  materialType: (value) => String(value || "Other"),
  catalogs: { en: {} },
  learningUnit: (value) => String(value || ""),
  apply() {},
  setPreference() {},
};

const readerStub = {
  pdf: null,
  opened: [],
  async open(options) {
    this.opened.push(options);
  },
  destroy() {
    this.pdf = null;
  },
};

let fetchHandler = () => ({
  ok: false,
  status: 500,
  statusText: "Server Error",
  json: async () => ({}),
});

const sandbox = {
  window: null,
  document: documentShim,
  addEventListener() {},
  removeEventListener() {},
  open() {},
  localStorage: {
    _store: {},
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
    },
    setItem(key, value) {
      this._store[key] = String(value);
    },
    removeItem(key) {
      delete this._store[key];
    },
  },
  navigator: { clipboard: { writeText: async () => {} } },
  history: { pushState() {}, replaceState() {} },
  location: { hash: "" },
  fetch: (url, options) => fetchHandler(url, options),
  Headers,
  Response,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  requestAnimationFrame: (callback) => setTimeout(() => callback(Date.now()), 0),
  cancelAnimationFrame: clearTimeout,
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  console,
  StudyHubI18n: i18n,
  StudyHubReader: readerStub,
  StudyHubNotesUI: notesUi,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(
  appSource +
    "\n;globalThis.__studyhubTest = {" +
    "state, api, openFileDrawer, saveNote, loadFileNotes, $," +
    "get currentReaderPage() { return currentReaderPage; }," +
    "set currentReaderPage(value) { currentReaderPage = value; }" +
    "};",
  sandbox,
);

const app = sandbox.__studyhubTest;

function respond(data) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

function pdfFile(id, extension = ".pdf") {
  return {
    id,
    extension,
    mime_type: extension === ".pdf" ? "application/pdf" : "text/plain",
    filename: `sample-${id}${extension}`,
    source_label: "",
    week_label: "Week 01",
    category: "Lecture",
    exercise_type: "",
    extractedText: "",
    ai_index_status: "indexed",
    course_id: 7,
    course_code: "TEST1001",
    week_number: 1,
    progress_state: "NOT_STARTED",
    needs_review: false,
  };
}

const posted = [];

async function main() {
  // Flush the bootstrap `loadBase()` so it settles against the failing default fetch.
  await new Promise((resolve) => setTimeout(resolve, 0));

  const files = { 1: pdfFile(1, ".pdf"), 2: pdfFile(2, ".docx"), 3: pdfFile(3, ".pdf") };
  fetchHandler = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method === "POST" && url === "/api/notes") {
      posted.push(JSON.parse(options.body));
      return respond({});
    }
    if (url.startsWith("/api/notes?")) return respond([]);
    if (url.startsWith("/api/file?")) {
      const id = Number(new URL(url, "http://localhost").searchParams.get("id"));
      return respond(files[id]);
    }
    return respond({});
  };

  // 1. Opening a PDF directly at page 3 keeps page 3 without any manual change.
  readerStub.pdf = { numPages: 3 };
  await app.openFileDrawer(1, 3, { history: false });
  check("open_pdf_at_page_3_initializes_reader_page", app.currentReaderPage === 3);
  check("reader_opened_with_requested_page", readerStub.opened.length === 1 && readerStub.opened[0].page === 3);
  app.$("#noteBody").value = "Page 3 summary";
  await app.saveNote();
  check(
    "save_without_page_change_stores_page_3_anchor",
    posted.length === 1 && posted[0].anchorPage === 3 && posted[0].pageCount === 3 && posted[0].targetId === 1,
  );

  // 2. Opening a different file does not reuse stale page state.
  readerStub.pdf = { numPages: 1 };
  await app.openFileDrawer(2, 1, { history: false });
  check("different_file_does_not_reuse_stale_page", app.currentReaderPage === 1);
  app.$("#noteBody").value = "Docx note";
  await app.saveNote();
  check(
    "non_pdf_file_creates_unanchored_note",
    posted.length === 2 && !("anchorPage" in posted[1]),
  );

  // 3. A failed PDF preview (no page count available) yields no misleading anchor.
  readerStub.pdf = null;
  await app.openFileDrawer(3, 5, { history: false });
  app.$("#noteBody").value = "Broken preview note";
  await app.saveNote();
  check(
    "failed_pdf_preview_creates_no_anchor",
    posted.length === 3 && !("anchorPage" in posted[2]),
  );

  return failures;
}

main()
  .then((failed) => {
    if (failed > 0) {
      console.error(`Notes Reader acceptance failures: ${failed}`);
      process.exit(1);
    }
    console.log("Notes Reader acceptance: PASS");
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
