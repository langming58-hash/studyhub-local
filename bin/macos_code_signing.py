#!/usr/bin/env python3
"""Shared, non-secret helpers for trusted macOS code signing."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    result = run(["file", "-b", str(path)])
    return result.returncode == 0 and "Mach-O" in result.stdout


def macho_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if is_macho(path)]
    return sorted(files, key=lambda path: (len(path.parts), str(path)), reverse=True)


def sign_macho_tree(root: Path, identity: str) -> int:
    if not root.is_dir():
        raise RuntimeError("The nested-code directory does not exist.")
    if not identity or identity == "-":
        raise RuntimeError("A Developer ID Application identity is required.")
    files = macho_files(root)
    if not files:
        raise RuntimeError("No nested Mach-O code was found to sign.")
    for path in files:
        result = run(
            [
                "codesign",
                "--force",
                "--options",
                "runtime",
                "--timestamp",
                "--sign",
                identity,
                str(path),
            ]
        )
        if result.returncode:
            raise RuntimeError(f"Nested code signing failed for {path.relative_to(root)}.")
    return len(files)
