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
HELPER_OUTPUT = OUTPUT_PARENT / "studyhub-markitdown"
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
        shutil.copy2(ROOT / "reader_extractors.py", stage / "reader_extractors.py")
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
            "--exclude-module",
            "markitdown",
            "--exclude-module",
            "pypdfium2",
            "--exclude-module",
            "PIL",
            "--exclude-module",
            "lxml",
            "--exclude-module",
            "openpyxl",
            "--exclude-module",
            "pptx",
            "--exclude-module",
            "pdfminer",
            "--exclude-module",
            "pdfplumber",
            "--exclude-module",
            "mammoth",
            "--exclude-module",
            "magika",
            "--exclude-module",
            "onnxruntime",
            "--exclude-module",
            "cryptography",
            "--noconfirm",
            "--clean",
            "--noupx",
            str(staged_server),
        ]
        result = subprocess.run(command, cwd=ROOT, check=False)

        helper_command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--name",
            "studyhub-markitdown",
            "--distpath",
            str(OUTPUT_PARENT),
            "--workpath",
            str(BUILD_ROOT / "helper-work"),
            "--specpath",
            str(BUILD_ROOT / "helper-spec"),
            "--collect-all",
            "markitdown",
            "--collect-all",
            "magika",
            "--noconfirm",
            "--clean",
            "--noupx",
            str(ROOT / "bin" / "markitdown_helper.py"),
        ]
        helper_result = subprocess.run(helper_command, cwd=ROOT, check=False)

    executable = OUTPUT_DIR / "studyhub-backend"
    if result.returncode != 0 or not executable.is_file():
        print("Packaged backend build failed.", file=sys.stderr)
        return result.returncode or 1
    if helper_result.returncode != 0 or not HELPER_OUTPUT.is_file():
        print("Packaged MarkItDown helper build failed.", file=sys.stderr)
        return helper_result.returncode or 1
    print("Packaged backend: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
