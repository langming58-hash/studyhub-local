#!/usr/bin/env python3
"""Scan the internal macOS prototype bundle for secrets and private build paths."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"

PATTERNS = {
    "OpenAI API key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    "Bearer token": re.compile(rb"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
    "OpenAI vector store ID": re.compile(rb"\bvs_[A-Za-z0-9_-]{12,}\b"),
    "OpenAI file ID": re.compile(rb"\bfile-[A-Za-z0-9_-]{12,}\b"),
    "macOS home path": re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    "Linux home path": re.compile(rb"/home/[A-Za-z0-9._-]+/"),
    "Windows home path": re.compile(rb"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "public network bind": re.compile(rb"\b0\.0\.0\.0\b"),
}

FORBIDDEN_NAMES = {".env", ".env.local", ".privacy.local.json"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def local_markers() -> list[bytes]:
    path = ROOT / ".privacy.local.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    markers = payload.get("forbidden_markers", [])
    if not isinstance(markers, list):
        return []
    return [str(marker).encode("utf-8") for marker in markers if str(marker).strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", nargs="?", default=str(DEFAULT_APP))
    args = parser.parse_args()
    app = Path(args.app).expanduser().resolve()
    if not app.is_dir():
        print("Desktop artifact privacy: FAIL (app bundle not found)")
        return 1

    issues: list[str] = []
    markers = local_markers()
    for path in sorted(item for item in app.rglob("*") if item.is_file()):
        rel = path.relative_to(app)
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            issues.append(f"forbidden bundled file: {rel}")
            continue
        data = path.read_bytes()
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                issues.append(f"{label}: {rel}")
        if any(marker in data for marker in markers):
            issues.append(f"local privacy marker: {rel}")

    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        print("Desktop artifact privacy: FAIL")
        return 1
    print(f"Desktop artifact privacy: PASS ({sum(1 for item in app.rglob('*') if item.is_file())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
