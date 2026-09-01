#!/usr/bin/env python3
"""Fail fast if private study data or secrets are about to enter Git."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACADEMIC_MATERIAL_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".ipynb",
}

RUNTIME_DIR_NAMES = {
    "data",
    "cache",
    "logs",
    "backups",
    "exports",
}

BINARY_TEXT_SCAN_SKIP_EXTS = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
    ".woff",
    ".woff2",
}

PRIVATE_CONFIG_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".privacy.local.json",
}

PRIVATE_CREDENTIAL_EXTS = {
    ".cer",
    ".key",
    ".keychain-db",
    ".mobileprovision",
    ".p12",
    ".p8",
    ".pem",
}

STUDY_LIBRARY_DIR_NAMES = {
    "StudyLibrary",
    "study-library",
    "study_library",
    "course-materials",
    "course_materials",
    "canvas-downloads",
    "canvas_downloads",
    "private-study",
    "private_study",
}

GENERATED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bOPENAI_API_KEY\s*=\s*(?:sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{32,})"),
    re.compile(r"\bgh[p]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub[_]pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bvs_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bfile-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAPPLE_(?:CERTIFICATE|PASSWORD|API_PRIVATE_KEY)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{20,}"),
    re.compile(r"(?i)\b(cookie|session|oauth|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}"),
]

TEXT_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

PRIVATE_PATH_PATTERNS = [
    re.compile(r"/(?:Users)/[A-Za-z0-9._-]+(?:/[^\s'\"`<>)]*)?"),
    re.compile(r"/home/[A-Za-z0-9._-]+(?:/[^\s'\"`<>)]*)?"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s'\"`<>]+(?:\\[^\s'\"`<>]*)?"),
]

SOCIAL_POST_URL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?linkedin\.com/feed/update/urn:li:(?:share|activity):\d+/?", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?xiaohongshu\.com/explore/[A-Za-z0-9_-]+/?", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/\s]+/status/\d+/?", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?reddit\.com/r/[^/\s]+/comments/[A-Za-z0-9_/-]+", re.IGNORECASE),
    re.compile(r"https?://news\.ycombinator\.com/item\?id=\d+", re.IGNORECASE),
]

LAUNCH_LEDGER_PATTERNS = [
    re.compile(r"\b(?:PUBLISHED|UNDER REVIEW|READY AS FALLBACK|BLOCKED BY PLATFORM)\b", re.IGNORECASE),
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\s+(?:AEST|AEDT|UTC|GMT|BST|PST|PDT|EST|EDT|CST|CDT)\b"),
]

EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
APPROVED_PUBLIC_EMAIL_RE = re.compile(
    r"^(?:noreply@github\.com|[^@\s<>]+@users\.noreply\.github\.com|[^@\s<>]+@example\.(?:com|net|org))$",
    re.IGNORECASE,
)


@dataclass
class PrivacyConfig:
    forbidden_markers: list[str] = field(default_factory=list)


def run_git(root: Path, args: list[str]) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def load_local_config(root: Path) -> PrivacyConfig:
    path = root / ".privacy.local.json"
    if not path.exists():
        return PrivacyConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PrivacyConfig(forbidden_markers=[f"invalid privacy config: {exc}"])
    markers = data.get("forbidden_markers", [])
    if not isinstance(markers, list):
        return PrivacyConfig(forbidden_markers=["invalid forbidden_markers config"])
    clean = [str(marker) for marker in markers if str(marker).strip()]
    return PrivacyConfig(forbidden_markers=clean)


def tracked_or_staged(root: Path) -> list[Path]:
    names = set(run_git(root, ["ls-files"]))
    names.update(run_git(root, ["diff", "--cached", "--name-only"]))
    names.update(run_git(root, ["ls-files", "--others", "--exclude-standard"]))
    if not names:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if set(rel.parts) & GENERATED_DIR_NAMES:
                continue
            if set(rel.parts) & RUNTIME_DIR_NAMES:
                continue
            if path.name in PRIVATE_CONFIG_NAMES:
                continue
            names.add(str(rel))
    return sorted((root / name).resolve() for name in names)


def check_paths(root: Path, paths: list[Path]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        rel = path.relative_to(root)
        parts = set(rel.parts)
        if parts & RUNTIME_DIR_NAMES:
            issues.append(f"Forbidden runtime directory in repo: {rel}")
        if parts & STUDY_LIBRARY_DIR_NAMES:
            issues.append(f"Forbidden private study-library directory in repo: {rel}")
        if path.suffix.lower() in ACADEMIC_MATERIAL_EXTS:
            issues.append(f"Forbidden academic material extension in repo: {rel}")
        if path.suffix.lower() in {".sqlite", ".db", ".sqlite3"}:
            issues.append(f"Forbidden runtime database in repo: {rel}")
        if path.name in PRIVATE_CONFIG_NAMES:
            issues.append(f"Forbidden private configuration file: {rel}")
        if path.suffix.lower() in PRIVATE_CREDENTIAL_EXTS:
            issues.append(f"Forbidden private credential file: {rel}")
    return issues


def check_text(root: Path, paths: list[Path], config: PrivacyConfig) -> list[str]:
    issues: list[str] = []
    markers = config.forbidden_markers
    for path in paths:
        if not path.exists() or path.stat().st_size > 1_000_000:
            continue
        if path.suffix.lower() in BINARY_TEXT_SCAN_SKIP_EXTS:
            continue
        if path.suffix.lower() in ACADEMIC_MATERIAL_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(root)
        for marker in markers:
            if marker in text:
                issues.append(f"Forbidden local privacy marker in {rel}")
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(text):
                issues.append(f"Potential private absolute path in {rel}: {pattern.pattern}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(f"Potential secret in {rel}: {pattern.pattern}")
        for match in TEXT_EMAIL_RE.finditer(text):
            if not is_privacy_safe_public_email(match.group(0)):
                issues.append(f"Potential personal email in {rel}")
                break
        for pattern in SOCIAL_POST_URL_PATTERNS:
            if pattern.search(text):
                issues.append(f"Potential personal social post URL in {rel}: {pattern.pattern}")
        if len(rel.parts) >= 2 and rel.parts[0] == "docs" and rel.parts[1] == "launch":
            for pattern in LAUNCH_LEDGER_PATTERNS:
                if pattern.search(text):
                    issues.append(f"Potential personal launch ledger detail in {rel}: {pattern.pattern}")
    return issues


def normalize_email(value: str) -> str:
    return value.strip().strip("<>").lower()


def is_privacy_safe_public_email(value: str) -> bool:
    email = normalize_email(value)
    if not email:
        return True
    return bool(EMAIL_RE.fullmatch(email) and APPROVED_PUBLIC_EMAIL_RE.fullmatch(email))


def check_git_metadata(root: Path) -> list[str]:
    issues: list[str] = []
    commit_rows = run_git(root, ["log", "--all", "--format=%H%x09%ae%x09%ce"])
    for row in commit_rows:
        parts = row.split("\t")
        if len(parts) != 3:
            continue
        commit_sha, author_email, committer_email = parts
        if not is_privacy_safe_public_email(author_email):
            issues.append(f"Potential personal author email in commit metadata: {commit_sha[:12]}")
        if not is_privacy_safe_public_email(committer_email):
            issues.append(f"Potential personal committer email in commit metadata: {commit_sha[:12]}")
    tag_rows = run_git(root, ["for-each-ref", "refs/tags", "--format=%(refname:short)%09%(taggeremail)"])
    for row in tag_rows:
        tag_name, _, tagger_email = row.partition("\t")
        if not is_privacy_safe_public_email(tagger_email):
            issues.append(f"Potential personal tagger email in tag metadata: {tag_name}")
    return issues


def check_repository(root: Path = ROOT) -> list[str]:
    root = root.resolve()
    paths = tracked_or_staged(root)
    config = load_local_config(root)
    return check_paths(root, paths) + check_text(root, paths, config) + check_git_metadata(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check public tree for private study data and secrets.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root to scan.")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    os.chdir(root)
    issues = check_repository(root)
    if issues:
        print("Privacy check failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"Privacy check passed for {len(tracked_or_staged(root))} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
