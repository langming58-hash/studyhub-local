#!/usr/bin/env python3
"""Scan public Git history for credential files and literal secret material."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".cer", ".key", ".keychain-db", ".mobileprovision", ".p12", ".p8", ".pem"}
PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Apple certificate/password literal": re.compile(
        rb"\bAPPLE_(?:CERTIFICATE|CERTIFICATE_PASSWORD|PASSWORD|API_PRIVATE_KEY)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{20,}"
    ),
    "OpenAI key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
}


def git(*args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text, check=False)


def main() -> int:
    objects = git("rev-list", "--objects", "--all", text=True)
    if objects.returncode:
        print("Git history credential scan: FAIL (could not enumerate objects)", file=sys.stderr)
        return 1
    issues: list[str] = []
    seen: set[str] = set()
    checked = 0
    for line in objects.stdout.splitlines():
        object_id, _, object_path = line.partition(" ")
        if not object_path or object_id in seen:
            continue
        seen.add(object_id)
        if Path(object_path).suffix.lower() in FORBIDDEN_SUFFIXES:
            issues.append(f"forbidden credential path in history: {object_path}")
            continue
        object_type = git("cat-file", "-t", object_id, text=True)
        if object_type.returncode or object_type.stdout.strip() != "blob":
            continue
        size = git("cat-file", "-s", object_id, text=True)
        if size.returncode or int(size.stdout.strip() or 0) > 1_000_000:
            continue
        payload = git("cat-file", "blob", object_id).stdout
        checked += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(payload):
                issues.append(f"{label} in historical blob: {object_path} ({object_id[:12]})")
    if issues:
        print("Git history credential scan: FAIL", file=sys.stderr)
        for issue in sorted(set(issues)):
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"Git history credential scan: PASS ({checked} text-sized blobs checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
