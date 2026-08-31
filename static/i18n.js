(function () {
  const catalogs = window.STUDYHUB_TRANSLATIONS || {};
  const supported = ["en", "zh-CN"];
  const preferenceKey = "studyhub.language";
  let preference = localStorage.getItem(preferenceKey) || "system";

  function detectedLanguage() {
    const locale = String(navigator.language || "en").toLowerCase();
    return locale === "zh" || locale.startsWith("zh-cn") || locale.startsWith("zh-sg") ? "zh-CN" : "en";
  }

  function language() {
    return supported.includes(preference) ? preference : detectedLanguage();
  }

  function t(key, variables = {}) {
    const catalog = catalogs[language()] || catalogs.en || {};
    const fallback = catalogs.en || {};
    let value = catalog[key] ?? fallback[key] ?? key;
    Object.entries(variables).forEach(([name, replacement]) => {
      value = value.replaceAll(`{${name}}`, String(replacement));
    });
    return value;
  }

  function materialType(id) {
    return t(`material.${String(id || "other").toLowerCase()}`);
  }

  function learningUnit(label, kind = "") {
    const value = String(label || "");
    if (language() !== "zh-CN") return value;
    const week = value.match(/^Week\s+0*(\d+)$/i);
    if (week) return t("week.label", { number: Number(week[1]) });
    const module = value.match(/^Module\s+0*(\d+)$/i);
    if (module) return t("week.moduleLabel", { number: Number(module[1]) });
    if (kind === "week" && /^\d+$/.test(value)) return t("week.label", { number: Number(value) });
    return value;
  }

  function locale() {
    return language() === "zh-CN" ? "zh-CN" : "en-AU";
  }

  function formatDate(value, options = { dateStyle: "medium", timeStyle: "short" }) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(locale(), options).format(date);
  }

  function apply(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    for (const [attribute, dataKey] of [["placeholder", "i18nPlaceholder"], ["aria-label", "i18nAriaLabel"], ["title", "i18nTitle"]]) {
      root.querySelectorAll(`[data-${dataKey.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`)}]`).forEach((node) => {
        node.setAttribute(attribute, t(node.dataset[dataKey]));
      });
    }
    document.documentElement.lang = language();
  }

  function setPreference(next) {
    preference = supported.includes(next) ? next : "system";
    localStorage.setItem(preferenceKey, preference);
    apply(document);
    window.dispatchEvent(new CustomEvent("studyhub:languagechange", { detail: { preference, language: language() } }));
  }

  function keyParity() {
    const en = Object.keys(catalogs.en || {}).sort();
    const zh = Object.keys(catalogs["zh-CN"] || {}).sort();
    return en.length === zh.length && en.every((key, index) => key === zh[index]);
  }

  window.StudyHubI18n = {
    apply,
    catalogs,
    detectedLanguage,
    formatDate,
    keyParity,
    language,
    learningUnit,
    materialType,
    preference: () => preference,
    setPreference,
    t,
  };
})();
