#!/usr/bin/env python3
"""Build the Python backend as a self-contained one-folder macOS sidecar."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PARENT = ROOT / "src-tauri" / "backend"
OUTPUT_DIR = OUTPUT_PARENT / "studyhub-backend"
BUILD_ROOT = ROOT / ".desktop-build" / "pyinstaller"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is unavailable. Run npm run desktop:setup first.")

    OUTPUT_PARENT.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studyhub-backend-source-", dir="/private/tmp") as raw_stage:
        stage = Path(raw_stage)
        staged_server = stage / "server.py"
        shutil.copy2(ROOT / "server.py", staged_server)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--name",
            "studyhub-backend",
            "--distpath",
            str(OUTPUT_PARENT),
            "--workpath",
            str(BUILD_ROOT / "work"),
            "--specpath",
            str(BUILD_ROOT / "spec"),
            "--collect-data",
            "certifi",
            "--noconfirm",
            "--clean",
            "--noupx",
            str(staged_server),
        ]
        result = subprocess.run(command, cwd=ROOT, check=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / ".gitkeep").touch()
    executable = OUTPUT_DIR / "studyhub-backend"
    if result.returncode != 0 or not executable.is_file():
        print("Packaged backend build failed.", file=sys.stderr)
        return result.returncode or 1
    print("Packaged backend: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
