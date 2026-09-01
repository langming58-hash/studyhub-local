#!/usr/bin/env python3
"""Fail closed unless an artifact is Developer ID signed and, when requested, notarized."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from macos_code_signing import macho_files


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def details(path: Path) -> str:
    result = run(["codesign", "-dvvv", str(path)])
    return result.stdout + result.stderr


def signed_by_developer_id(path: Path, team_id: str, require_runtime: bool = True) -> bool:
    verified = run(["codesign", "--verify", "--strict", "--verbose=2", str(path)])
    info = details(path)
    return (
        verified.returncode == 0
        and "Signature=adhoc" not in info
        and "Authority=Developer ID Application:" in info
        and f"TeamIdentifier={team_id}" in info
        and "Timestamp=" in info
        and (not require_runtime or bool(re.search(r"flags=.*\bruntime\b", info)))
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--dmg")
    parser.add_argument("--expected-team", required=True)
    parser.add_argument("--require-notarization", action="store_true")
    args = parser.parse_args()

    app = Path(args.app).expanduser().resolve()
    if not app.is_dir():
        raise SystemExit("The packaged app was not found.")
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    results: dict[str, bool | int] = {
        "bundle_identifier": info.get("CFBundleIdentifier") == "io.studyhublocal.desktop",
        "minimum_macos": str(info.get("LSMinimumSystemVersion")) == "13.0",
        "app_deep_signature": run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]).returncode == 0,
        "app_developer_id": signed_by_developer_id(app, args.expected_team),
    }
    entitlements = run(["codesign", "-d", "--entitlements", ":-", str(app)])
    entitlement_text = entitlements.stdout + entitlements.stderr
    results["debug_entitlement_absent"] = "com.apple.security.get-task-allow" not in entitlement_text

    nested = macho_files(app / "Contents")
    results["nested_macho_count"] = len(nested)
    results["nested_developer_id"] = bool(nested) and all(
        signed_by_developer_id(path, args.expected_team) for path in nested
    )

    dmg: Path | None = None
    if args.dmg:
        dmg = Path(args.dmg).expanduser().resolve()
        results["dmg_developer_id"] = dmg.is_file() and signed_by_developer_id(
            dmg, args.expected_team, require_runtime=False
        )

    if args.require_notarization:
        results["app_ticket_stapled"] = run(["xcrun", "stapler", "validate", "-v", str(app)]).returncode == 0
        app_gatekeeper = run(["spctl", "-a", "-vvv", "--type", "execute", str(app)])
        app_assessment = app_gatekeeper.stdout + app_gatekeeper.stderr
        results["app_gatekeeper"] = app_gatekeeper.returncode == 0 and "Notarized Developer ID" in app_assessment
        if dmg is None:
            results["dmg_ticket_stapled"] = False
            results["dmg_gatekeeper"] = False
        else:
            results["dmg_ticket_stapled"] = run(["xcrun", "stapler", "validate", "-v", str(dmg)]).returncode == 0
            dmg_gatekeeper = run(
                ["spctl", "-a", "-vvv", "-t", "open", "--context", "context:primary-signature", str(dmg)]
            )
            results["dmg_gatekeeper"] = dmg_gatekeeper.returncode == 0

    failed = [name for name, passed in results.items() if isinstance(passed, bool) and not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Trusted macOS verification failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Trusted macOS verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
