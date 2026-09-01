#!/usr/bin/env python3
"""Validate, name, and checksum the public Apple Silicon DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"
DMG_DIR = ROOT / "src-tauri" / "target" / "release" / "bundle" / "dmg"
RELEASE_DIR = ROOT / ".release"


def file_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"Could not read version from {path.relative_to(ROOT)}")
    return match.group(1)


def versions() -> dict[str, str]:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    return {
        "package.json": str(package["version"]),
        "src-tauri/tauri.conf.json": str(tauri["version"]),
        "src-tauri/Cargo.toml": file_version(ROOT / "src-tauri" / "Cargo.toml", r'^version\s*=\s*"([^"]+)"'),
        "server.py": file_version(ROOT / "server.py", r'^\s*"version":\s*"([^"]+)"'),
    }


def app_architectures(info: dict[str, object]) -> list[str]:
    executable = APP / "Contents" / "MacOS" / str(info.get("CFBundleExecutable") or "")
    if not executable.is_file():
        raise SystemExit("Packaged app executable was not found.")
    result = subprocess.run(["lipo", "-archs", str(executable)], text=True, capture_output=True, check=False)
    if result.returncode:
        raise SystemExit("Could not determine the packaged app architecture.")
    return sorted(result.stdout.split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Require a release tag matching the configured version.")
    parser.add_argument("--source-dmg", help="Use a trusted DMG instead of the default Tauri DMG output.")
    args = parser.parse_args()

    found_versions = versions()
    unique_versions = set(found_versions.values())
    if len(unique_versions) != 1:
        raise SystemExit(f"Version mismatch: {found_versions}")
    version = unique_versions.pop()
    if args.tag and args.tag != f"v{version}":
        raise SystemExit(f"Tag {args.tag} does not match v{version}.")
    if not APP.is_dir():
        raise SystemExit("Packaged app bundle was not found.")
    info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
    architectures = app_architectures(info)
    if architectures != ["arm64"]:
        raise SystemExit(f"Expected an Apple Silicon-only app, found: {architectures}")
    if info.get("CFBundleIdentifier") != "io.studyhublocal.desktop":
        raise SystemExit("Unexpected bundle identifier.")
    if str(info.get("LSMinimumSystemVersion")) != "13.0":
        raise SystemExit("Unexpected minimum macOS version.")

    if args.source_dmg:
        source = Path(args.source_dmg).expanduser().resolve()
        if not source.is_file():
            raise SystemExit("The trusted source DMG was not found.")
    else:
        dmgs = sorted(DMG_DIR.glob("*.dmg"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not dmgs:
            raise SystemExit("Generated DMG was not found.")
        source = dmgs[0]
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    destination = RELEASE_DIR / f"StudyHub-Local-v{version}-macos-arm64.dmg"
    if source != destination:
        shutil.copy2(source, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum = destination.with_suffix(destination.suffix + ".sha256")
    checksum.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise SystemExit("Checksum verification failed after release artifact preparation.")

    print(f"VERSION={version}")
    print("ARCHITECTURE=arm64")
    print(f"DMG={destination}")
    print(f"CHECKSUM_FILE={checksum}")
    print(f"SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
