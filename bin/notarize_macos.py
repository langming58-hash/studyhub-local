#!/usr/bin/env python3
"""Submit an app or DMG to Apple's notary service, then staple and validate it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


REQUIRED_ENV = ("APPLE_API_ISSUER", "APPLE_API_KEY", "APPLE_API_KEY_PATH")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def accepted_result(output: str) -> bool:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False
    return str(payload.get("status", "")).lower() == "accepted"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit("Notarization credentials require user action: " + ", ".join(missing))
    key_path = Path(os.environ["APPLE_API_KEY_PATH"]).expanduser().resolve()
    if not key_path.is_file():
        raise SystemExit("The App Store Connect API private-key file is unavailable.")
    if args.preflight:
        print("Notarization credential preflight: AVAILABLE")
        return 0

    artifact = Path(args.artifact).expanduser().resolve()
    if not artifact.exists() or (artifact.is_dir() and artifact.suffix != ".app"):
        raise SystemExit("A macOS app or DMG artifact is required.")

    with tempfile.TemporaryDirectory(prefix="studyhub-notary-") as temporary:
        submission = artifact
        if artifact.is_dir():
            submission = Path(temporary) / f"{artifact.name}.zip"
            packaged = run(["ditto", "-c", "-k", "--keepParent", str(artifact), str(submission)])
            if packaged.returncode:
                raise SystemExit("Could not prepare the app for notarization.")
        result = run(
            [
                "xcrun",
                "notarytool",
                "submit",
                str(submission),
                "--key",
                str(key_path),
                "--key-id",
                os.environ["APPLE_API_KEY"],
                "--issuer",
                os.environ["APPLE_API_ISSUER"],
                "--wait",
                "--output-format",
                "json",
            ]
        )
    if result.returncode or not accepted_result(result.stdout):
        raise SystemExit("Apple notarization was not accepted. No release artifact may be published.")
    stapled = run(["xcrun", "stapler", "staple", "-v", str(artifact)])
    if stapled.returncode:
        raise SystemExit("Apple accepted the submission, but ticket stapling failed.")
    validated = run(["xcrun", "stapler", "validate", "-v", str(artifact)])
    if validated.returncode:
        raise SystemExit("The stapled notarization ticket could not be validated.")
    print(f"Apple notarization and stapling: ACCEPTED ({artifact.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
