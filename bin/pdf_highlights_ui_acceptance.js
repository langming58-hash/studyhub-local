#!/usr/bin/env node
"use strict";

// Executable frontend acceptance for Persistent PDF Highlights V1. Part A
// exercises the real Reader geometry conversion and overlay rendering; Part B
// drives the real app.js save-highlight flow with a stubbed Reader and api.

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

// --- Part A: Reader geometry conversion and overlay rendering ---
const readerSandbox = { window: null, document: {}, console };
readerSandbox.window = readerSandbox;
vm.createContext(readerSandbox);
vm.runInContext(readerSource, readerSandbox);
const reader = readerSandbox.StudyHubReader;

reader.rendered = new Map([
  [3, { viewport: { scale: 1.15, height: 910.8, viewBox: [0, 0, 612, 792] } }],
]);
const layer = {
  getBoundingClientRect: () => ({ left: 100, top: 200, width: 703.8, height: 910.8 }),
  childNodes: [],
  appendChild(node) {
    this.childNodes.push(node);
  },
  querySelectorAll() {
    return [];
  },
};
const range = {
  getClientRects: () => [
    { left: 150, top: 210, width: 115, height: 16 },
    { left: 150, top: 230, width: 115, height: 16 },
  ],
};

const rects = reader.selectionRectsInPdf(range, layer, 3);
check(
  "pdf_geometry_conversion_from_client_rects",
  rects.length === 2
    && rects[0][0] === 43.48
    && rects[0][1] === 769.39
    && rects[0][2] === 100
    && rects[0][3] === 13.91,
);
check("pdf_geometry_uses_bottom_origin", rects[0][1] + rects[0][3] < 792 && rects[0][1] > 700);

// Overlay rendering.
const renderedDivs = [];
const holder = {
  querySelector: (selector) => (selector === ".reader-text-layer" ? layer : null),
};
readerSandbox.document = {
  createElement: (tag) => {
    const el = { tag, className: "", style: {}, appendChild() {} };
    if (tag === "div") renderedDivs.push(el);
    return el;
  },
};
reader.rendered.set(3, { viewport: { scale: 1.15, height: 910.8, viewBox: [0, 0, 612, 792] }, holder });
reader.highlights = [{ anchor_page: 3, anchor_state: "ok", rects: [[43.48, 769.39, 100, 13.91]] }];
reader.applyPageHighlights(3);
const pct = (value) => parseFloat(value.replace("%", ""));
check(
  "highlight_overlay_rendered_as_page_percentage",
  renderedDivs.length === 1
    && Math.abs(pct(renderedDivs[0].style.left) - 7.1046) < 0.01
    && Math.abs(pct(renderedDivs[0].style.top) - 1.0985) < 0.01
    && Math.abs(pct(renderedDivs[0].style.width) - 16.3399) < 0.01,
);
readerSandbox.document = {};

// --- Part B: app.js save-highlight flow ---
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
let fetchHandler = () => ({ ok: true, status: 200, json: async () => [] });
const appSandbox = {
  window: null,
  document: documentShim,
  addEventListener() {},
  removeEventListener() {},
  open() {},
  confirm: () => true,
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
  StudyHubReviewCardsUI: {},
  StudyHubReader: {
    pdf: { numPages: 3 },
    open: async () => {},
    destroy() {},
    setHighlights: () => {},
    getCapturedSelection: () => ({
      generation: 1,
      fileId: 1,
      page: 3,
      pageCount: 3,
      text: "Page three text",
      sourceVersion: "matver_x",
      rects: [[43.48, 769.39, 100, 13.91]],
    }),
    getSelectionError: () => null,
  },
};
appSandbox.window = appSandbox;
vm.createContext(appSandbox);
vm.runInContext(
  appSource +
    "\n;globalThis.__test = { state, $, api, saveHighlight, loadFileHighlights };",
  appSandbox,
);
const app = appSandbox.__test;

async function main() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  fetchHandler = async (url, options = {}) => {
    if (url === "/api/highlights" && (options.method || "GET").toUpperCase() === "POST") {
      posted.push(JSON.parse(options.body));
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    if (url.startsWith("/api/highlights?")) return { ok: true, status: 200, json: async () => [] };
    return { ok: true, status: 200, json: async () => [] };
  };
  app.state.selectedFile = { id: 1, course_id: 7, week_label: "Week 01" };
  await app.saveHighlight();
  check(
    "save_highlight_posts_page_and_geometry",
    posted.length === 1
      && posted[0].action === "create"
      && posted[0].fileId === 1
      && posted[0].anchorPage === 3
      && posted[0].sourceVersion === "matver_x"
      && JSON.stringify(posted[0].rects) === "[[43.48,769.39,100,13.91]]",
  );
  return failures;
}

main()
  .then((failed) => {
    if (failed > 0) {
      console.error(`PDF highlights UI failures: ${failed}`);
      process.exit(1);
    }
    console.log("PDF highlights UI acceptance: PASS");
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
