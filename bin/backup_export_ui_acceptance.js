#!/usr/bin/env node
"use strict";

// Executable frontend acceptance for the Settings backup-export action. It
// drives the real app.js exportBackup() with a stubbed native invoke bridge.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const appSource = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");

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

let fetchHandler = () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({}) });
const posted = [];
const requests = [];
const invocations = [];
let invokeResult = "Saved Backup.zip";
const sandbox = {
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
  fetch: (url, options) => {
    requests.push(url);
    return fetchHandler(url, options);
  },
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
  __TAURI__: {
    core: {
      invoke: async (command, args) => {
        invocations.push({ command, args });
        return invokeResult;
      },
    },
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(ROOT, "static", "i18n", "catalog.js"), "utf8"), sandbox);
vm.runInContext(
  appSource +
    "\n;globalThis.__test = { state, $, exportBackup };",
  sandbox,
);
const app = sandbox.__test;

async function main() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  app.state.health = { desktopMode: true };
  requests.length = 0;
  const stubTranslate = i18n.t;
  for (const locale of ["en", "zh-CN"]) {
    const warning = sandbox.STUDYHUB_TRANSLATIONS[locale]["backup.exportConfirm"];
    check(locale + "_export_warning_present", typeof warning === "string" && warning.trim().length > 0);
    check(locale + "_export_warning_privacy_and_scope", locale === "en"
      ? /unencrypted ZIP/.test(warning) && /private notes/.test(warning) && /Original study files and credentials are not included/.test(warning)
      : /未加密的 ZIP/.test(warning) && /私人笔记/.test(warning) && /不包含原始学习文件或凭证/.test(warning));
    check(locale + "_export_warning_acceptance_boundary", locale === "en"
      ? /Full real-GUI backup save\/restore\/undo acceptance remains incomplete/.test(warning)
      : /真实 GUI 备份保存、恢复和撤销验收仍未完成/.test(warning));
    const confirmations = [];
    i18n.t = (key) => sandbox.STUDYHUB_TRANSLATIONS[locale][key] || key;
    sandbox.confirm = (message) => {
      confirmations.push(message);
      return false;
    };
    await app.exportBackup();
    check(locale + "_decline_shows_localized_warning", confirmations.length === 1 && confirmations[0] === warning);
    check(locale + "_decline_makes_no_backend_request", requests.length === 0 && posted.length === 0);
    check(locale + "_decline_makes_no_native_save_invocation", invocations.length === 0);
  }
  i18n.t = stubTranslate;
  let desktopGuardConfirmations = 0;
  sandbox.confirm = () => {
    desktopGuardConfirmations += 1;
    return true;
  };
  app.state.health = { desktopMode: false };
  await app.exportBackup();
  check("non_desktop_export_still_blocked", desktopGuardConfirmations === 0 && requests.length === 0 && invocations.length === 0 && sandbox.document.querySelector("#toast").textContent === "backup.desktopRequired");
  app.state.health = { desktopMode: true };
  fetchHandler = async (url, options = {}) => {
    if (url === "/api/backup/export" && (options.method || "GET").toUpperCase() === "POST") {
      posted.push(options.body || "");
      return { ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true, backup: { export_id: "abc", filename: "studyhub-backup-v2-test.zip" } }) };
    }
    return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
  };

  await app.exportBackup();
  check("export_posts_to_backend", posted.length === 1 && posted[0] === "{}");
  check("export_invokes_native_save", invocations.length === 1 && invocations[0].command === "export_backup_archive" && invocations[0].args.filename === "studyhub-backup-v2-test.zip");
  check("export_success_toast", sandbox.document.querySelector("#toast").textContent === "backup.exportSaved");

  invokeResult = null;
  await app.exportBackup();
  check("export_cancel_toast", sandbox.document.querySelector("#toast").textContent === "backup.exportCancelled");

  fetchHandler = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true, backup: {} }) });
  await app.exportBackup();
  check("missing_filename_fails", invocations.length === 2 && sandbox.document.querySelector("#toast").textContent === "backup.exportFailed");

  fetchHandler = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true, backup: { filename: "not-a-zip" } }) });
  await app.exportBackup();
  check("malformed_filename_fails", invocations.length === 2 && sandbox.document.querySelector("#toast").textContent === "backup.exportFailed");

  fetchHandler = async () => ({ ok: false, status: 500, statusText: "ERR", json: async () => ({ error: "boom" }) });
  await app.exportBackup();
  check("export_failure_toast", invocations.length === 2 && sandbox.document.querySelector("#toast").textContent === "boom");

  return failures;
}

main()
  .then((failed) => {
    if (failed > 0) {
      console.error(`Backup export UI failures: ${failed}`);
      process.exit(1);
    }
    console.log("Backup export UI acceptance: PASS");
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
