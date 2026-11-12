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
import tomllib
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
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8"))
    cargo_name = tomllib.loads((ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))["package"]["name"]
    locked_app = [entry for entry in cargo_lock["package"] if entry["name"] == cargo_name]
    if len(locked_app) != 1:
        raise SystemExit("Cargo.lock must contain exactly one application package.")
    return {
        "package.json": str(package["version"]),
        "package-lock.json": str(lock["version"]),
        "package-lock.json packages root": str(lock["packages"][""]["version"]),
        "src-tauri/tauri.conf.json": str(tauri["version"]),
        "src-tauri/Cargo.toml": file_version(ROOT / "src-tauri" / "Cargo.toml", r'^version\s*=\s*"([^"]+)"'),
        "server.py": file_version(ROOT / "server.py", r'^\s*"version":\s*"([^"]+)"'),
        "server.py backup app_version": file_version(ROOT / "server.py", r'^\s*"app_version":\s*"([^"]+)"'),
        "src-tauri/Cargo.lock": str(locked_app[0]["version"]),
    }


def release_metadata(tag: str | None = None) -> dict[str, str]:
    found = versions()
    if len(set(found.values())) != 1:
        raise SystemExit(f"Version mismatch: {found}")
    version = found["package.json"]
    if not re.fullmatch(r"\d+\.\d+\.\d+-beta\.\d+", version):
        raise SystemExit("Public desktop releases require an exact beta version.")
    expected_tag = f"v{version}"
    if tag is not None and tag != expected_tag:
        raise SystemExit(f"Tag {tag} does not match {expected_tag}.")
    notes = f"docs/releases/{expected_tag}.md"
    if not (ROOT / notes).is_file():
        raise SystemExit(f"Missing release notes: {notes}")
    stem = f"StudyHub-Local-{expected_tag}-macos-arm64"
    return {"version": version, "tag": expected_tag, "artifact_stem": stem,
            "dmg_name": f"{stem}.dmg", "notes_file": notes}


def validate_bundle_version(info: dict[str, object], version: str) -> None:
    if str(info.get("CFBundleShortVersionString")) != version or str(info.get("CFBundleVersion")) != version:
        raise SystemExit("Packaged app version does not match the configured release version.")


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
    parser.add_argument("--check-only", action="store_true", help="Check source versions without requiring artifacts.")
    parser.add_argument("--github-output", type=Path, help="Append validated metadata to a GitHub Actions output file.")
    args = parser.parse_args()

    metadata = release_metadata(args.tag)
    version = metadata["version"]
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("".join(f"{key}={value}\n" for key, value in metadata.items()))
    if args.check_only:
        print(json.dumps(metadata, indent=2))
        return 0
    if not APP.is_dir():
        raise SystemExit("Packaged app bundle was not found.")
    info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
    validate_bundle_version(info, version)
    architectures = app_architectures(info)
    if architectures != ["arm64"]:
        raise SystemExit(f"Expected an Apple Silicon-only app, found: {architectures}")
    if info.get("CFBundleIdentifier") != "io.studyhublocal.desktop":
        raise SystemExit("Unexpected bundle identifier.")
    if str(info.get("LSMinimumSystemVersion")) != "13.0":
        raise SystemExit("Unexpected minimum macOS version.")

    dmgs = sorted(DMG_DIR.glob("*.dmg"), key=lambda path: path.stat().st_mtime, reverse=True)
    if len(dmgs) != 1:
        raise SystemExit("Expected exactly one generated DMG; use a clean build directory.")
    source = dmgs[0]
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    destination = RELEASE_DIR / metadata["dmg_name"]
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
