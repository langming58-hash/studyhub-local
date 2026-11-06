#!/usr/bin/env node
"use strict";

// Executable frontend acceptance for PDF Text Excerpts V1. Part A exercises the
// real ReaderController selection validator with a fake DOM/selection; Part B
// drives the real app.js excerpt-to-note flow with a stubbed Reader and api.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const readerSource = fs.readFileSync(path.join(ROOT, "static", "reader.js"), "utf8");
const appSource = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}

// --- Part A: Reader selection validator ---
function makeTextLayer(page) {
  const layer = {
    childNodes: [],
    closest(selector) {
      return selector === ".reader-page[data-page]" ? page : null;
    },
    contains(node) {
      return node === this || this.childNodes.includes(node);
    },
  };
  return layer;
}
function makePage(number) {
  const page = {
    dataset: { page: String(number) },
    textLayer: null,
    querySelector(selector) {
      return selector === ".reader-text-layer" ? this.textLayer : null;
    },
    contains(node) {
      return node === this || (this.textLayer && this.textLayer.contains(node));
    },
  };
  page.textLayer = makeTextLayer(page);
  return page;
}
function textNode(layer) {
  const node = { nodeType: 3, parentElement: layer };
  layer.childNodes.push(node);
  return node;
}

const page2 = makePage(2);
const page3 = makePage(3);
const pages = [page2, page3];
const readerSandbox = { window: null, document: {}, console };
readerSandbox.window = readerSandbox;
vm.createContext(readerSandbox);
vm.runInContext(readerSource, readerSandbox);
const reader = readerSandbox.StudyHubReader;
reader.fileId = 1;
reader.generation = 1;
reader.metadata = { visual_type: "pdf", source_version_stable_id: "matver_x" };
reader.pdf = { numPages: 3 };
reader.mount = {
  contains(node) {
    return pages.some((page) => page.contains(node));
  },
};

function selection(text, startNode, endNode) {
  return {
    isCollapsed: !text,
    rangeCount: 1,
    toString: () => text,
    getRangeAt: () => ({ startContainer: startNode, endContainer: endNode || startNode }),
  };
}

const page3Text = textNode(page3.textLayer);
const page2Text = textNode(page2.textLayer);
readerSandbox.getSelection = () => selection("Selected text on page three", page3Text, page3Text);
reader.updateSelection();
const captured = reader.getCapturedSelection();
check("page_three_selection_captured", captured && captured.page === 3 && captured.text === "Selected text on page three" && captured.sourceVersion === "matver_x");

readerSandbox.getSelection = () => selection("", page3Text, page3Text);
reader.updateSelection();
check("collapsed_selection_keeps_last_valid", reader.getCapturedSelection() && reader.getCapturedSelection().text === "Selected text on page three");

readerSandbox.getSelection = () => selection("cross page", page2Text, page3Text);
reader.updateSelection();
check("cross_page_selection_rejected", reader.getCapturedSelection() === null && reader.getSelectionError() === "invalid");

readerSandbox.getSelection = () => selection("x".repeat(4001), page3Text, page3Text);
reader.updateSelection();
check("oversized_selection_rejected", reader.getCapturedSelection() === null && reader.getSelectionError() === "too_long");

const outside = { nodeType: 3, parentElement: { closest: () => null, childNodes: [], contains: () => false } };
readerSandbox.getSelection = () => selection("outside text layer", outside, outside);
reader.updateSelection();
check("selection_outside_text_layer_rejected", reader.getCapturedSelection() === null);

readerSandbox.getSelection = () => selection("valid again", page3Text, page3Text);
reader.updateSelection();
reader.fileId = 2;
check("different_file_selection_rejected", reader.getCapturedSelection() === null);
reader.fileId = 1;
reader.generation = 2;
check("stale_generation_selection_rejected", reader.getCapturedSelection() === null);

// --- Part B: app.js excerpt-to-note flow ---
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
    focus() {},
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
  t: (key, vars = {}) => String(key).replace(/\{(\w+)\}/g, (_m, n) => (vars[n] !== undefined ? String(vars[n]) : `{${n}}`)),
  materialType: (v) => String(v || "Other"),
  catalogs: { en: {} },
  learningUnit: (v) => String(v || ""),
  apply() {},
  setPreference() {},
};
const posted = [];
let fetchHandler = () => ({ ok: true, status: 200, statusText: "OK", json: async () => [] });
const appSandbox = {
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
  StudyHubNotesUI: { noteAnchorPayload: () => ({}) },
  StudyHubReader: {
    pdf: { numPages: 3 },
    open: async () => {},
    destroy() {},
    getCapturedSelection: () => ({ generation: 1, fileId: 1, page: 3, pageCount: 3, text: "Selected text\nSecond line", sourceVersion: "matver_x" }),
    getSelectionError: () => null,
  },
};
appSandbox.window = appSandbox;
vm.createContext(appSandbox);
vm.runInContext(
  appSource +
    "\n;globalThis.__test = { state, $, api, addSelectionToNote, persistNote, quotedExcerpt, get noteExcerpt() { return noteExcerpt; } };",
  appSandbox,
);
const app = appSandbox.__test;

async function main() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  fetchHandler = async (url, options = {}) => {
    if (url === "/api/notes" && (options.method || "GET").toUpperCase() === "POST") {
      posted.push(JSON.parse(options.body));
      return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
    }
    return { ok: true, status: 200, statusText: "OK", json: async () => [] };
  };
  app.state.selectedFile = { id: 1, course_id: 7, week_label: "Week 01" };
  app.$("#noteBody").value = "";
  app.addSelectionToNote();
  check("excerpt_populates_quoted_draft", app.$("#noteBody").value === "> Selected text\n> Second line");
  check("excerpt_stores_selected_page", app.noteExcerpt && app.noteExcerpt.page === 3 && app.noteExcerpt.sourceVersion === "matver_x");

  app.$("#noteBody").value = "> Selected text\n> Second line\n\nMy commentary";
  await app.persistNote();
  check(
    "excerpt_saves_anchor_page_version",
    posted.length === 1
      && posted[0].anchorPage === 3
      && posted[0].pageCount === 3
      && posted[0].sourceVersion === "matver_x"
      && posted[0].body.includes("My commentary"),
  );
  check("excerpt_draft_cleared_after_save", app.noteExcerpt === null && app.$("#noteBody").value === "");

  return failures;
}

main()
  .then((failed) => {
    if (failed > 0) {
      console.error(`PDF text excerpts UI failures: ${failed}`);
      process.exit(1);
    }
    console.log("PDF text excerpts UI acceptance: PASS");
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
