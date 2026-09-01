#!/usr/bin/env python3
"""Run Cargo checks with the project-local toolchain when present."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    cargo_home = ROOT / ".tools" / "cargo"
    rustup_home = ROOT / ".tools" / "rustup"
    cargo_bin = cargo_home / "bin"
    if cargo_bin.is_dir():
        env["PATH"] = f"{cargo_bin}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("CARGO_HOME", str(cargo_home))
        env.setdefault("RUSTUP_HOME", str(rustup_home))
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if not cargo:
        raise SystemExit("Cargo is unavailable. Install Rust or run the desktop toolchain setup.")
    return subprocess.run(
        [cargo, "check", "--manifest-path", str(ROOT / "src-tauri" / "Cargo.toml")],
        cwd=ROOT,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
