#!/usr/bin/env python3
"""Regression checks for the generic public privacy scanner."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_privacy_module():
    module_path = ROOT / "bin" / "privacy_check.py"
    spec = importlib.util.spec_from_file_location("studyhub_privacy_check", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_file(root: Path, rel: str, text: str = "demo") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def issue_contains(issues: list[str], fragment: str) -> bool:
    return any(fragment in issue for issue in issues)


def run_detection_cases() -> dict[str, bool]:
    privacy = load_privacy_module()
    with tempfile.TemporaryDirectory() as tmp_name:
        root = Path(tmp_name)
        source = write_file(root, "src/app.py", "OPENAI_API_KEY=sk-" + "a" * 24)
        binary = write_file(root, "fixtures/lecture.pdf", "%PDF synthetic")
        env_file = write_file(root, ".env.local", "OPENAI_API_KEY=sk-" + "b" * 24)
        database = write_file(root, "data/runtime.sqlite", "sqlite")
        synthetic_home_path = "/".join(["", "home", "example-user", "private-study", "file.txt"])
        private_path = write_file(root, "docs/path.txt", synthetic_home_path)
        marker_source = write_file(root, "docs/marker.txt", "TEST-PRIVATE-COURSE")
        (root / ".privacy.local.json").write_text(
            json.dumps({"forbidden_markers": ["TEST-PRIVATE-COURSE"]}),
            encoding="utf-8",
        )
        explicit_paths = [source, binary, env_file, database, private_path, marker_source]
        config = privacy.load_local_config(root)
        issues = privacy.check_paths(root, explicit_paths) + privacy.check_text(root, explicit_paths, config)
        return {
            "generic_secret_detection": issue_contains(issues, "Potential secret"),
            "academic_binary_rejected": issue_contains(issues, "academic material extension"),
            "env_local_rejected": issue_contains(issues, "private configuration file"),
            "runtime_database_rejected": issue_contains(issues, "runtime directory") and issue_contains(issues, "runtime database"),
            "absolute_private_path_rejected": issue_contains(issues, "private absolute path"),
            "local_config_marker_rejected": issue_contains(issues, "local privacy marker"),
        }


def public_source_has_no_private_deny_data() -> bool:
    forbidden_obfuscation_patterns = [
        re.compile(r"FORBIDDEN_TEXT\s*="),
        re.compile(r"REAL_[A-Z_]+\s*="),
        re.compile(r"STUDY_DIR\s*="),
        re.compile(r"APP_DIR\s*="),
        re.compile(r"HOME\s*="),
    ]
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if set(path.relative_to(ROOT).parts) & {"data", "cache", "logs", "node_modules", "__pycache__"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.name == "privacy_check.py" and any(pattern.search(text) for pattern in forbidden_obfuscation_patterns):
            return False
    return True


def main() -> int:
    checks = run_detection_cases()
    checks["public_source_has_no_user_specific_deny_data"] = public_source_has_no_private_deny_data()
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
