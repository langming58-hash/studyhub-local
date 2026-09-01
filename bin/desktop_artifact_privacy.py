#!/usr/bin/env python3
"""Scan the packaged macOS app or mounted DMG for private data and secrets."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
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
    "synthetic fixture course code": re.compile(rb"\bTEST\d{4}\b"),
}

FORBIDDEN_NAMES = {".env", ".env.local", ".privacy.local.json"}
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".log", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"
}
FORBIDDEN_PARTS = {"cache", "logs", "previews", "demo-data"}


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


def scan_app(app: Path) -> tuple[list[str], int]:
    issues: list[str] = []
    markers = local_markers()
    files = sorted(item for item in app.rglob("*") if item.is_file())
    for path in files:
        rel = path.relative_to(app)
        lower_parts = {part.lower() for part in rel.parts}
        if FORBIDDEN_PARTS & lower_parts or ("tests" in lower_parts and "fixtures" in lower_parts):
            issues.append(f"runtime/test directory bundled: {rel}")
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            issues.append(f"forbidden bundled file: {rel}")
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            issues.append(f"unreadable bundled file: {rel} ({error.__class__.__name__})")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                issues.append(f"{label}: {rel}")
        if any(marker in data for marker in markers):
            issues.append(f"local privacy marker: {rel}")
    return issues, len(files)


def mounted_app(dmg: Path) -> tuple[Path, str]:
    result = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("DMG could not be mounted read-only")
    payload = plistlib.loads(result.stdout)
    entities = payload.get("system-entities", [])
    mount_points = [str(entity.get("mount-point")) for entity in entities if entity.get("mount-point")]
    if len(mount_points) != 1:
        for mount_point in reversed(mount_points):
            subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)
        raise RuntimeError("DMG did not expose exactly one mounted volume")
    mount_point = mount_points[0]
    apps = list(Path(mount_point).glob("*.app"))
    if len(apps) != 1:
        subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)
        raise RuntimeError("DMG must contain exactly one app bundle")
    applications = Path(mount_point) / "Applications"
    if not applications.is_symlink() or applications.readlink() != Path("/Applications"):
        subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)
        raise RuntimeError("DMG must contain the standard Applications shortcut")
    signature = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(apps[0])],
        check=False,
        capture_output=True,
    )
    if signature.returncode:
        subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)
        raise RuntimeError("DMG app bundle failed strict code-signature integrity verification")
    return apps[0], mount_point


def report(issues: list[str], count: int, label: str) -> int:
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        print(f"Desktop artifact privacy ({label}): FAIL")
        return 1
    print(f"Desktop artifact privacy ({label}): PASS ({count} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", nargs="?", default=str(DEFAULT_APP))
    parser.add_argument("--dmg", help="Mount and scan a generated DMG instead of the default app bundle.")
    args = parser.parse_args()

    if args.dmg:
        dmg = Path(args.dmg).expanduser().resolve()
        if not dmg.is_file():
            print("Desktop artifact privacy (DMG): FAIL (DMG not found)")
            return 1
        mount_point = ""
        try:
            app, mount_point = mounted_app(dmg)
            issues, count = scan_app(app)
            return report(issues, count, "DMG")
        except (OSError, RuntimeError, plistlib.InvalidFileException) as error:
            print(f"Desktop artifact privacy (DMG): FAIL ({error})")
            return 1
        finally:
            if mount_point:
                subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)

    app = Path(args.app).expanduser().resolve()
    if not app.is_dir():
        print("Desktop artifact privacy (app): FAIL (app bundle not found)")
        return 1
    issues, count = scan_app(app)
    return report(issues, count, "app")


if __name__ == "__main__":
    raise SystemExit(main())
