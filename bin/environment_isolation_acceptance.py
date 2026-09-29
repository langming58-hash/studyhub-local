#!/usr/bin/env python3
"""Synthetic acceptance checks for production/development/demo isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"

STATE_ENV = (
    "DATABASE_PATH",
    "DEMO_MODE",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_VECTOR_STORE_ID",
    "STUDYHUB_CACHE_DIR",
    "STUDYHUB_CONFIG_PATH",
    "STUDYHUB_DATA_DIR",
    "STUDYHUB_DESKTOP_TEST_ROOT",
    "STUDYHUB_LOG_DIR",
    "STUDYHUB_RUNTIME_DIR",
    "STUDYHUB_RUNTIME_PROFILE",
    "STUDY_LIBRARY_PATH",
)

PROBE = r"""
import json
import os
import sqlite3
import sys

import server

action = sys.argv[1]
canary = sys.argv[2]
server.ensure_dirs()
conn = server.connect_db()
server.init_db(conn)
if action == "write":
    conn.execute(
        "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES ('profile_canary', ?, ?)",
        (canary, server.now_iso()),
    )
    conn.commit()
elif action == "reset":
    server.reset_studyhub_state(conn)
row = conn.execute("SELECT value FROM app_settings WHERE key='profile_canary'").fetchone()
health = server.api_health(conn)
conn.close()
entries = server.read_local_env_entries()
result = {
    "profile": server.RUNTIME_PROFILE,
    "runtime": str(server.RUNTIME_DIR),
    "data": str(server.DATA_DIR),
    "cache": str(server.CACHE_DIR),
    "logs": str(server.LOG_DIR),
    "database": str(server.DB_PATH),
    "config": str(server.ENV_LOCAL_PATH),
    "study_root": str(server.DEFAULT_STUDY_ROOT),
    "canary": row["value"] if row else "",
    "settings_marker": entries.get("PROFILE_MARKER", ""),
    "settings_has_study_path": "STUDY_LIBRARY_PATH" in entries,
    "settings_has_openai_key": "OPENAI_API_KEY" in entries,
    "settings_has_vector_id": "OPENAI_VECTOR_STORE_ID" in entries,
    "process_has_openai_key": bool(os.environ.get("OPENAI_API_KEY")),
    "process_has_vector_id": bool(os.environ.get("OPENAI_VECTOR_STORE_ID")),
    "first_run_files": health["filesIndexed"],
    "health_profile": health["runtimeProfile"],
}
print(json.dumps(result))
"""


def write_settings(path: Path, profile: str, *, blocked_values: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'PROFILE_MARKER="{profile}"', 'HOST="127.0.0.1"']
    if blocked_values:
        lines.extend(
            [
                'STUDY_LIBRARY_PATH="/synthetic/production/library"',
                'OPENAI_API_KEY="synthetic-production-key"',
                'OPENAI_VECTOR_STORE_ID="synthetic-provider-id"',
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def probe(profile: str, root: Path, action: str, canary: str, *, inherit_blocked: bool = False) -> dict[str, object]:
    runtime = root / "runtime"
    config = root / "config" / "settings.env"
    env = os.environ.copy()
    for key in STATE_ENV:
        env.pop(key, None)
    env.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": profile,
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "DATABASE_PATH": str(runtime / "data" / "studyhub.sqlite"),
            "STUDYHUB_CONFIG_PATH": str(config),
        }
    )
    if inherit_blocked:
        env.update(
            {
                "STUDY_LIBRARY_PATH": "/synthetic/inherited/production/library",
                "OPENAI_API_KEY": "synthetic-inherited-production-key",
                "OPENAI_VECTOR_STORE_ID": "synthetic-inherited-provider-id",
            }
        )
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, action, canary],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def path_is_within(raw_path: object, root: Path) -> bool:
    return Path(str(raw_path)).resolve().is_relative_to(root.resolve())


def main() -> int:
    rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    release_config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    dev_config = json.loads((ROOT / "src-tauri" / "tauri.dev.conf.json").read_text(encoding="utf-8"))
    demo_config = json.loads((ROOT / "src-tauri" / "tauri.demo.conf.json").read_text(encoding="utf-8"))
    development_runner = (ROOT / "bin" / "run_development.py").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="studyhub-environment-isolation-") as raw_tmp:
        temp = Path(raw_tmp)
        roots = {
            "production": temp / "production",
            "development": temp / "development",
            "demo-test": temp / "demo-test",
        }
        write_settings(roots["production"] / "config" / "settings.env", "production")
        write_settings(roots["development"] / "config" / "settings.env", "development")
        write_settings(
            roots["demo-test"] / "config" / "settings.env",
            "demo-test",
            blocked_values=True,
        )

        production = probe("production", roots["production"], "write", "production-canary")
        development = probe("development", roots["development"], "write", "development-canary")
        demo = probe(
            "demo-test",
            roots["demo-test"],
            "write",
            "demo-canary",
            inherit_blocked=True,
        )

        for profile, root in roots.items():
            marker = root / "runtime" / f"{profile}.canary"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(profile, encoding="utf-8")

        demo_after_reset = probe(
            "demo-test",
            roots["demo-test"],
            "reset",
            "ignored",
            inherit_blocked=True,
        )
        production_after_demo_reset = probe("production", roots["production"], "read", "ignored")
        development_after_demo_reset = probe("development", roots["development"], "read", "ignored")

        legacy_source_state = temp / "legacy-source-state"
        legacy_source_state.mkdir()
        legacy_canary = legacy_source_state / "studyhub.sqlite"
        legacy_canary.write_text("synthetic legacy canary", encoding="utf-8")

        checks = {
            "01_production_resolves_only_to_production_root": all(
                path_is_within(production[key], roots["production"])
                for key in ("runtime", "data", "cache", "logs", "database", "config", "study_root")
            ),
            "02_development_resolves_only_to_development_root": all(
                path_is_within(development[key], roots["development"])
                for key in ("runtime", "data", "cache", "logs", "database", "config", "study_root")
            ),
            "03_demo_resolves_only_to_demo_root": all(
                path_is_within(demo[key], roots["demo-test"])
                for key in ("runtime", "data", "cache", "logs", "database", "config", "study_root")
            ),
            "04_profile_database_canaries_are_isolated": production["canary"] == "production-canary"
            and development["canary"] == "development-canary"
            and demo["canary"] == "demo-canary",
            "05_development_cannot_read_production_settings": development["settings_marker"] == "development"
            and production["settings_marker"] == "production",
            "06_demo_cannot_read_production_settings": demo["settings_marker"] == "demo-test",
            "07_demo_cannot_inherit_production_study_library": demo["settings_has_study_path"] is False
            and path_is_within(demo["study_root"], roots["demo-test"]),
            "08_demo_cannot_inherit_openai_or_provider_ids": demo["settings_has_openai_key"] is False
            and demo["settings_has_vector_id"] is False
            and demo["process_has_openai_key"] is False
            and demo["process_has_vector_id"] is False,
            "09_demo_reset_does_not_modify_other_profiles": demo_after_reset["canary"] == ""
            and production_after_demo_reset["canary"] == "production-canary"
            and development_after_demo_reset["canary"] == "development-canary"
            and (roots["production"] / "runtime" / "production.canary").read_text(encoding="utf-8") == "production"
            and (roots["development"] / "runtime" / "development.canary").read_text(encoding="utf-8") == "development",
            "10_development_cache_and_logs_differ_from_production": development["cache"] != production["cache"]
            and development["logs"] != production["logs"],
            "11_demo_cache_and_logs_differ_from_both": demo["cache"] not in {production["cache"], development["cache"]}
            and demo["logs"] not in {production["logs"], development["logs"]},
            "12_explicit_temporary_test_root_remains_supported": "STUDYHUB_DESKTOP_TEST_ROOT" in rust
            and 'root.join("data")' in rust
            and 'root.join("config")' in rust,
            "13_packaged_release_configuration_is_production": release_config["identifier"] == "io.studyhublocal.desktop"
            and 'resolve_runtime_profile(PRODUCTION_IDENTIFIER, false, false)' in rust,
            "14_normal_developer_configuration_is_development": dev_config["identifier"] == "io.studyhublocal.desktop.dev"
            and package["scripts"]["desktop:dev"].endswith("src-tauri/tauri.dev.conf.json")
            and package["scripts"]["dev"] == "python3 bin/run_development.py"
            and '"STUDYHUB_RUNTIME_PROFILE": "development"' in development_runner,
            "15_production_first_run_behavior_is_unchanged": production["first_run_files"] == 0
            and production["health_profile"] == "production"
            and release_config["productName"] == "StudyHub Local",
            "16_existing_production_paths_are_not_renamed_or_migrated": legacy_canary.read_text(encoding="utf-8") == "synthetic legacy canary"
            and "app.path()\n                .app_data_dir()" in rust
            and "app.path()\n                .app_config_dir()" in rust,
            "desktop_child_environment_is_explicit_and_sanitized": all(
                f'"{key}"' in rust for key in STATE_ENV
            )
            and all(
                token in rust
                for token in (
                    '.env("STUDYHUB_DATA_DIR"',
                    '.env("STUDYHUB_CACHE_DIR"',
                    '.env("STUDYHUB_LOG_DIR"',
                    '.env("DATABASE_PATH"',
                )
            ),
            "webview_storage_identities_are_distinct": demo_config["identifier"] == "io.studyhublocal.desktop.demo"
            and "webview_data_dir" in rust
            and ".data_directory(directory)" in rust,
            "demo_external_source_entry_points_are_disabled": "Demo/Test mode cannot select external study folders." in rust
            and "Demo/Test mode cannot select external study files." in rust
            and "Demo/Test mode cannot connect an external study library." in SERVER.read_text(encoding="utf-8"),
        }

    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
