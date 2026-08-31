#!/usr/bin/env python3
"""Bilingual UI and production-fixture isolation acceptance checks."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def catalogs() -> dict[str, dict[str, str]]:
    source = read("static/i18n/catalog.js")
    english_start = source.index("en: {") + len("en: ")
    english_end = source.index('\n  },\n  "zh-CN": {') + len("\n  }")
    chinese_start = source.index('"zh-CN": {') + len('"zh-CN": ')
    chinese_end = source.rindex("\n  }") + len("\n  }")
    return {
        "en": json.loads(source[english_start:english_end]),
        "zh-CN": json.loads(source[chinese_start:chinese_end]),
    }


def main() -> int:
    translations = catalogs()
    english = translations.get("en", {})
    chinese = translations.get("zh-CN", {})
    app = read("static/app.js")
    html = read("static/index.html")
    runtime_i18n = read("static/i18n.js")
    server = read("server.py")
    tauri = json.loads(read("src-tauri/tauri.conf.json"))
    production_sources = "\n".join(
        [server, app, html, runtime_i18n, read("static/i18n/catalog.js"), read("src-tauri/src/lib.rs")]
    )
    referenced_keys = set(re.findall(r'\bt\("([a-z][a-zA-Z0-9_.]+)"', app))
    referenced_keys.update(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-z][a-zA-Z0-9_.]+)"', html))
    resource_paths = json.dumps(tauri["bundle"]["resources"])
    fixture_root = ROOT / "tests" / "fixtures" / "synthetic-courses"

    checks = {
        "english_and_chinese_catalogs_exist": bool(english) and bool(chinese),
        "translation_key_parity": set(english) == set(chinese),
        "all_referenced_keys_exist": referenced_keys <= set(english),
        "system_locale_detection_present": "navigator.language" in runtime_i18n and '"zh-CN"' in runtime_i18n,
        "language_preference_is_local_only": "studyhub.language" in runtime_i18n and "localStorage" in runtime_i18n,
        "settings_language_selector_present": 'id="languagePreference"' in app,
        "live_language_event_present": "studyhub:languagechange" in app and "studyhub:languagechange" in runtime_i18n,
        "accessibility_attributes_are_translatable": "data-i18n-aria-label" in html and "aria-label" in runtime_i18n,
        "stable_material_type_ids_are_lowercase": all(
            token in server for token in ('"lecture"', '"tutorial"', '"workshop"', '"lab"', '"quiz"')
        ),
        "production_api_has_no_demo_contract": '"demoMode"' not in server,
        "production_ui_has_no_demo_controls": all(
            token not in app for token in ("Try Demo", "Open Synthetic Demo", "data-start-demo", "data-switch-demo")
        ),
        "production_sources_have_no_fixture_course_codes": re.search(r"\bTEST\d{4}\b", production_sources) is None,
        "tauri_bundle_excludes_test_fixtures": "demo-data" not in resource_paths and "tests/fixtures" not in resource_paths,
        "synthetic_fixture_library_is_test_only": fixture_root.is_dir()
        and all(path.is_file() for path in fixture_root.rglob("*") if path.suffix),
        "clean_first_run_copy_present_in_both_languages": all(
            key in english and key in chinese for key in ("onboarding.title", "onboarding.createCourse", "onboarding.importFolder")
        ),
    }
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
