#!/usr/bin/env python3
"""Create and Developer ID sign a drag-to-Applications DMG from a stapled app."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command[0]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--signing-identity", required=True)
    args = parser.parse_args()

    app = Path(args.app).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit("A packaged .app bundle is required.")
    if not args.signing_identity or args.signing_identity == "-":
        raise SystemExit("A Developer ID Application identity is required.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="studyhub-dmg-") as temporary:
        staging = Path(temporary) / "StudyHub Local"
        staging.mkdir()
        run(["ditto", str(app), str(staging / app.name)])
        (staging / "Applications").symlink_to("/Applications")
        run(
            [
                "hdiutil",
                "create",
                "-volname",
                "StudyHub Local",
                "-srcfolder",
                str(staging),
                "-ov",
                "-format",
                "UDZO",
                str(output),
            ]
        )
    run(
        [
            "codesign",
            "--force",
            "--timestamp",
            "--sign",
            args.signing_identity,
            str(output),
        ]
    )
    run(["codesign", "--verify", "--strict", "--verbose=2", str(output)])
    print(f"Trusted DMG prepared: {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
