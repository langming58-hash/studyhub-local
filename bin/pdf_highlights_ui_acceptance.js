#!/usr/bin/env node
"use strict";

// Executable frontend acceptance for Persistent PDF Highlights V1. Part A
// exercises the real Reader span/subrange geometry capture and overlay
// rendering; Part B drives the real app.js save-highlight flow.

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

// --- Part A: Reader subrange capture and overlay rendering ---
const RangeCtor = { START_TO_START: 0, END_TO_END: 1 };
const readerSandbox = {
  window: null,
  document: {},
  console,
  Range: RangeCtor,
  getComputedStyle: () => ({ getPropertyValue: (prop) => (prop === "--font-height" ? "18px" : "") }),
};
readerSandbox.window = readerSandbox;
vm.createContext(readerSandbox);
vm.runInContext(readerSource, readerSandbox);
const reader = readerSandbox.StudyHubReader;

const span = {
  textContent: "Page three text",
  style: { left: "11.76%", top: "7.36%" },
  getBoundingClientRect: () => ({ left: 100, top: 200, width: 114.77, height: 16 }),
};
const layer = {
  childNodes: [],
  appendChild(node) {
    this.childNodes.push(node);
  },
  querySelectorAll(selector) {
    return selector === "span" ? [span] : [];
  },
  getBoundingClientRect: () => ({ width: 703.8, height: 910.8 }),
};

const subRects = [{ left: 160, top: 210, width: 30, height: 16 }];
const subRange = { getClientRects: () => subRects };
const range = {
  intersectsNode: () => true,
  cloneRange: () => ({
    compareBoundaryPoints: () => 0,
    setStart() {},
    setEnd() {},
    getClientRects: () => subRects,
  }),
};
readerSandbox.document = {
  createRange: () => ({
    selectNodeContents() {},
    startContainer: span,
    startOffset: 0,
    endContainer: span,
    endOffset: 15,
  }),
};

const rects = reader.selectionRects(range, layer);
check(
  "subrange_geometry_captured_within_span",
  rects.length === 1
    && rects[0][0] === 0.1176
    && rects[0][1] === 0.0736
    && rects[0][2] === 3.3333
    && rects[0][3] === 0.5556
    && rects[0][4] === 1.6667
    && rects[0][5] === 0.8889,
);

// Cross-span selection: one rect per intersecting span with correct anchors.
function multiSpan(leftPct, topPct, text) {
  return {
    textContent: text,
    style: { left: `${leftPct}%`, top: `${topPct}%` },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 50, height: 16 }),
  };
}
const multiA = multiSpan(10, 5, "First span text");
const multiB = multiSpan(10, 20, "Second span text");
const multiLayer = {
  childNodes: [],
  appendChild(node) {
    this.childNodes.push(node);
  },
  querySelectorAll(selector) {
    return selector === "span" ? [multiA, multiB] : [];
  },
  getBoundingClientRect: () => ({ width: 703.8, height: 910.8 }),
};
const multiRange = {
  intersectsNode: () => true,
  cloneRange: () => ({
    compareBoundaryPoints: () => 0,
    setStart() {},
    setEnd() {},
    getClientRects: () => [{ left: 5, top: 2, width: 10, height: 10 }],
  }),
};
const multiRects = reader.selectionRects(multiRange, multiLayer);
check(
  "cross_span_selection_captures_per_span_anchors",
  multiRects.length === 2
    && multiRects[0][0] === 0.1
    && multiRects[0][1] === 0.05
    && multiRects[1][0] === 0.1
    && multiRects[1][1] === 0.2,
);

const renderedDivs = [];
const holder = { querySelector: (selector) => (selector === ".reader-text-layer" ? layer : null) };
readerSandbox.document = {
  createElement: (tag) => {
    const el = { tag, className: "", style: {}, appendChild() {} };
    if (tag === "div") renderedDivs.push(el);
    return el;
  },
};
reader.rendered.set(3, { holder });
reader.highlights = [{ anchor_page: 3, anchor_state: "ok", rects: [[0.1176, 0.0736, 3.3333, 0.5556, 1.6667, 0.8889]] }];
reader.applyPageHighlights(3);
const pct = (value) => parseFloat(value.replace("%", ""));
const px = (value) => parseFloat(value.replace("px", ""));
check(
  "subrange_overlay_positioned_and_sized_stably",
  renderedDivs.length === 1
    && Math.abs(pct(renderedDivs[0].style.left) - 20.29) < 0.05
    && Math.abs(pct(renderedDivs[0].style.top) - 8.46) < 0.05
    && Math.abs(px(renderedDivs[0].style.width) - 30) < 0.1
    && Math.abs(px(renderedDivs[0].style.height) - 16) < 0.1,
);

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
      text: "three",
      sourceVersion: "matver_x",
      rects: [[0.1176, 0.0736, 3.3333, 0.5556, 1.6667, 0.8889]],
      pageWidth: 703.8,
      pageHeight: 910.8,
      fontHeight: 18,
    }),
    getSelectionError: () => null,
  },
};
appSandbox.window = appSandbox;
vm.createContext(appSandbox);
vm.runInContext(
  appSource +
    "\n;globalThis.__test = { state, $, api, saveHighlight };",
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
  app.state.selectedFile = { id: 1, course_id: 7, week_label: "Week 01", extension: ".pdf" };
  await app.saveHighlight();
  check(
    "save_highlight_posts_subrange_geometry_and_page_context",
    posted.length === 1
      && posted[0].anchorPage === 3
      && posted[0].sourceVersion === "matver_x"
      && JSON.stringify(posted[0].rects) === "[[0.1176,0.0736,3.3333,0.5556,1.6667,0.8889]]"
      && posted[0].pageWidth === 703.8
      && posted[0].fontHeight === 18,
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
