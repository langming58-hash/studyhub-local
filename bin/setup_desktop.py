#!/usr/bin/env python3
"""Create the reproducible macOS desktop backend build environment."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.15"
PYTHON_ROOT = ROOT / ".desktop-build" / "python-runtime"
VENV = ROOT / ".venv-desktop"


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            "uv is required for the desktop build environment. "
            "Install uv, then run npm run desktop:setup again."
        )

    run(
        [
            uv,
            "python",
            "install",
            PYTHON_VERSION,
            "--install-dir",
            str(PYTHON_ROOT),
            "--no-bin",
        ]
    )
    candidates = sorted(PYTHON_ROOT.glob(f"cpython-{PYTHON_VERSION}-macos-*-none/bin/python3"))
    if len(candidates) != 1:
        raise SystemExit("The pinned desktop Python runtime could not be located.")

    run([uv, "venv", "--clear", "--python", str(candidates[0]), str(VENV)])
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(VENV / "bin" / "python"),
            "-r",
            str(ROOT / "requirements-desktop.txt"),
        ]
    )
    print(f"Desktop build Python: {PYTHON_VERSION}")
    print("Desktop build environment: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
