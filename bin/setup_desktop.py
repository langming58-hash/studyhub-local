#!/usr/bin/env python3
"""Create the reproducible macOS desktop backend build environment."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from desktop_dependencies import BUILD, TARGET, require_platform, require_uv, validate_locks

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.15"
PYTHON_ROOT = ROOT / ".desktop-build" / "python-runtime"
VENV = ROOT / ".venv-desktop"


def run(command: list[str]) -> None:
    env = os.environ.copy()
    env["MACOSX_DEPLOYMENT_TARGET"] = TARGET["macos_minimum"]
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            "uv is required for the desktop build environment. "
            "Install uv, then run npm run desktop:setup again."
        )
    validate_locks(ROOT)
    require_platform()
    require_uv(uv)
    if TARGET["python"] != PYTHON_VERSION:
        raise SystemExit("Desktop runtime and dependency lock Python versions differ.")

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
            "sync",
            "--python",
            str(VENV / "bin" / "python"),
            str(ROOT / BUILD),
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--strict",
            "--python-version",
            PYTHON_VERSION,
            "--python-platform",
            TARGET["platform"],
            "--default-index",
            "https://pypi.org/simple",
        ]
    )
    run([uv, "pip", "check", "--python", str(VENV / "bin" / "python")])
    run([str(VENV / "bin" / "python"), str(ROOT / "bin" / "desktop_dependencies.py"), "--environment"])
    print(f"Desktop build Python: {PYTHON_VERSION}")
    print("Desktop build environment: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
