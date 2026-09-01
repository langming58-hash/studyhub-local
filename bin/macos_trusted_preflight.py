#!/usr/bin/env python3
"""Report trusted-distribution readiness without exposing Apple credentials."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_SECRET_NAMES = (
    "APPLE_CERTIFICATE",
    "APPLE_CERTIFICATE_PASSWORD",
    "KEYCHAIN_PASSWORD",
    "APPLE_TEAM_ID",
    "APPLE_API_ISSUER",
    "APPLE_API_KEY",
    "APPLE_API_PRIVATE_KEY",
)
IDENTITY_RE = re.compile(r'^\s*\d+\)\s+([0-9A-Fa-f]+)\s+"([^"]+)"')


def command_available(command: list[str]) -> bool:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return result.returncode == 0


def developer_identities(keychain: str | None = None) -> list[tuple[str, str]]:
    command = ["security", "find-identity", "-v", "-p", "codesigning"]
    if keychain:
        command.append(keychain)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    identities: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        match = IDENTITY_RE.match(line)
        if match and match.group(2).startswith("Developer ID Application:"):
            identities.append((match.group(1), match.group(2)))
    return identities


def identity_matches_team(identity: str, team_id: str) -> bool:
    return bool(team_id and identity.rstrip().endswith(f"({team_id})"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ci-secrets", action="store_true")
    parser.add_argument("--require-identity", action="store_true")
    parser.add_argument("--expected-team", default=os.environ.get("APPLE_TEAM_ID", ""))
    args = parser.parse_args()

    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    entitlements_path = ROOT / "src-tauri" / str(config["bundle"]["macOS"].get("entitlements", ""))
    entitlements_valid = False
    if entitlements_path.is_file():
        try:
            entitlements = plistlib.loads(entitlements_path.read_bytes())
            entitlements_valid = isinstance(entitlements, dict) and not entitlements
        except plistlib.InvalidFileException:
            entitlements_valid = False

    keychain = os.environ.get("SIGNING_KEYCHAIN_PATH") or None
    identities = developer_identities(keychain)
    missing_secrets = [name for name in CI_SECRET_NAMES if not os.environ.get(name)]
    tools = {
        "codesign": shutil.which("codesign") is not None,
        "security": shutil.which("security") is not None,
        "spctl": shutil.which("spctl") is not None,
        "notarytool": command_available(["xcrun", "--find", "notarytool"]),
        "stapler": command_available(["xcrun", "--find", "stapler"]),
    }
    team_matches = (
        len(identities) == 1
        and (not args.expected_team or identity_matches_team(identities[0][1], args.expected_team))
    )

    print(f"Developer ID Application identity: {'AVAILABLE' if identities else 'NOT AVAILABLE'}")
    print(f"Developer ID Application identity count: {len(identities)}")
    print(f"Apple signing tools: {'AVAILABLE' if all(tools.values()) else 'NOT AVAILABLE'}")
    print(f"Hardened runtime configuration: {'AVAILABLE' if entitlements_valid else 'NOT AVAILABLE'}")
    print(f"CI credential set: {'AVAILABLE' if not missing_secrets else 'NEEDS USER ACTION'}")
    if missing_secrets:
        print("Missing GitHub Actions secret names: " + ", ".join(missing_secrets))
    if identities and args.expected_team:
        print(f"Developer ID team match: {'AVAILABLE' if team_matches else 'NOT AVAILABLE'}")

    readiness_available = (
        all(tools.values())
        and entitlements_valid
        and len(identities) == 1
        and team_matches
        and not missing_secrets
    )
    print(f"Trusted distribution readiness: {'AVAILABLE' if readiness_available else 'NEEDS USER ACTION'}")

    failed = []
    if not all(tools.values()):
        failed.append("required Apple command-line tools")
    if not entitlements_valid:
        failed.append("minimal entitlements configuration")
    if args.require_ci_secrets and missing_secrets:
        failed.append("GitHub Actions Apple credentials")
    if args.require_identity and (len(identities) != 1 or not team_matches):
        failed.append("one matching Developer ID Application identity")
    if failed:
        print("Trusted distribution preflight: NEEDS USER ACTION (" + ", ".join(failed) + ")", file=sys.stderr)
        return 1
    print("Trusted distribution preflight checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
