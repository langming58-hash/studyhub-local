#!/usr/bin/env python3
"""Offline guards for reviewed dependency and workflow security boundaries.

These regression checks do not replace npm audit, pip-audit or cargo audit.
"""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Minimum patched versions selected for this lockfile, not a general advisory DB.
PATCHED = {"ctutils": (0, 4, 4), "rustix": (1, 1, 6), "tokio": (1, 53, 3)}


def patched_crates(lock: dict) -> bool:
    for package in lock["package"]:
        minimum = PATCHED.get(package["name"])
        if minimum is None:
            continue
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", package["version"])
        if match is None or tuple(map(int, match.groups())) < minimum:
            return False
    return True


def workflow_checks(ci: str, desktop: str) -> dict[str, bool]:
    workflows = (ci, desktop)
    references = re.findall(r"(?m)^\s*-\s+uses:\s+([^\s#]+)", "\n".join(workflows))
    immutable = bool(references) and all(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}", ref)
        for ref in references
    )
    checkout_blocks = []
    for source in workflows:
        lines = source.splitlines()
        for index, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            indent = len(line) - len(line.lstrip())
            block = []
            for following in lines[index + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                block.append(following.strip())
            checkout_blocks.append("persist-credentials: false" in block)
    return {
        "actions_use_full_commit_shas": immutable,
        "ci_token_explicitly_read_only": "permissions:\n  contents: read\n" in ci
        and not re.search(r"(?m)^\s+[\w-]+:\s+write\s*$", ci),
        "ci_installs_locked_npm_tree": bool(re.search(r"(?m)^\s+- run: npm ci\s*$", ci))
        and not re.search(r"\bnpm install\b", ci),
        "checkout_does_not_persist_credentials": bool(checkout_blocks) and all(checkout_blocks),
    }


def main() -> int:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    desktop = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    lock = tomllib.loads((ROOT / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    checks = workflow_checks(ci, desktop)
    checks["advised_crates_are_patched"] = patched_crates(lock)
    checks["desktop_rust_tests_use_lockfile"] = "cargo test --locked --manifest-path" in desktop
    for script in ("check_desktop.py", "build_desktop.py"):
        source = (ROOT / "bin" / script).read_text(encoding="utf-8")
        checks[f"{script}_uses_lockfile"] = '"--locked"' in source
    for name, old in {"ctutils": "0.4.2", "rustix": "1.1.4", "tokio": "1.53.1"}.items():
        bad = copy.deepcopy(lock)
        for package in bad["package"]:
            if package["name"] == name:
                package["version"] = old
        checks[f"rejects_{name}_downgrade"] = not patched_crates(bad)
    mutable = re.sub(r"(actions/checkout)@[0-9a-f]{40}", r"\1@v4", ci)
    checks["rejects_mutable_action"] = not workflow_checks(mutable, desktop)["actions_use_full_commit_shas"]
    checks["rejects_ci_write_token"] = not workflow_checks(
        ci.replace("contents: read", "contents: write"), desktop
    )["ci_token_explicitly_read_only"]
    checks["rejects_unlocked_npm_install"] = not workflow_checks(
        ci.replace("run: npm ci", "run: npm install"), desktop
    )["ci_installs_locked_npm_tree"]
    checks["rejects_persisted_checkout_credentials"] = not workflow_checks(
        ci.replace("persist-credentials: false", "persist-credentials: true"), desktop
    )["checkout_does_not_persist_credentials"]
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    print(f"Supply-chain acceptance: {'PASS' if all(checks.values()) else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
