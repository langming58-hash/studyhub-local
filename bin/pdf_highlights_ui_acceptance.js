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

// --- Part A: Reader span geometry capture and overlay rendering ---
const readerSandbox = {
  window: null,
  document: {},
  console,
  getComputedStyle: () => ({ getPropertyValue: (prop) => (prop === "--font-height" ? "18px" : "") }),
};
readerSandbox.window = readerSandbox;
vm.createContext(readerSandbox);
vm.runInContext(readerSource, readerSandbox);
const reader = readerSandbox.StudyHubReader;

function makeSpan(leftPct, topPct, widthPx, heightPx, text) {
  return {
    textContent: text,
    style: { left: `${leftPct}%`, top: `${topPct}%` },
    getBoundingClientRect: () => ({ width: widthPx, height: heightPx }),
  };
}
const spanA = makeSpan(11.76, 7.36, 103.8, 16, "Page three text");
const spanB = makeSpan(11.76, 9.0, 103.8, 16, "Second line");
const layer = {
  childNodes: [],
  appendChild(node) {
    this.childNodes.push(node);
  },
  querySelectorAll(selector) {
    return selector === "span" ? [spanA, spanB] : [];
  },
};
const range = { intersectsNode: (span) => span === spanA };

const rects = reader.selectionRects(range, layer);
check(
  "span_geometry_captured_as_page_fraction_and_em",
  rects.length === 1
    && rects[0][0] === 0.1176
    && rects[0][1] === 0.0736
    && rects[0][2] === 5.7667
    && rects[0][3] === 0.8889,
);

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
reader.rendered.set(3, { holder });
reader.highlights = [{ anchor_page: 3, anchor_state: "ok", rects: [[0.1176, 0.0736, 5.7667, 0.8889]] }];
reader.applyPageHighlights(3);
const px = (value) => parseFloat(value.replace("px", ""));
check(
  "highlight_overlay_uses_span_percentage_and_font_height",
  renderedDivs.length === 1
    && renderedDivs[0].style.left === "11.76%"
    && renderedDivs[0].style.top === "7.36%"
    && Math.abs(px(renderedDivs[0].style.width) - 103.8) < 0.1
    && Math.abs(px(renderedDivs[0].style.height) - 16) < 0.1,
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
      rects: [[0.1176, 0.0736, 5.7667, 0.8889]],
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
  app.state.selectedFile = { id: 1, course_id: 7, week_label: "Week 01", extension: ".pdf" };
  await app.saveHighlight();
  check(
    "save_highlight_posts_page_and_geometry",
    posted.length === 1
      && posted[0].action === "create"
      && posted[0].fileId === 1
      && posted[0].anchorPage === 3
      && posted[0].sourceVersion === "matver_x"
      && JSON.stringify(posted[0].rects) === "[[0.1176,0.0736,5.7667,0.8889]]",
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
