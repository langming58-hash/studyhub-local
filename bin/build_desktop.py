#!/usr/bin/env python3
"""Build the public macOS app and DMG without embedding the builder's home path."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from macos_code_signing import sign_macho_tree


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted", action="store_true", help="Require Developer ID signing for nested code.")
    parser.add_argument("--app-only", action="store_true", help="Build the app bundle without creating a DMG.")
    args = parser.parse_args()

    tauri = ROOT / "node_modules" / ".bin" / "tauri"
    if not tauri.exists():
        raise SystemExit("Tauri CLI is unavailable. Run npm install first.")
    backend_python = ROOT / ".venv-desktop" / "bin" / "python"
    if not backend_python.exists():
        raise SystemExit("Desktop build environment is unavailable. Run npm run desktop:setup first.")

    backend = subprocess.run(
        [str(backend_python), str(ROOT / "bin" / "build_desktop_backend.py")],
        cwd=ROOT,
        check=False,
    )
    if backend.returncode:
        return backend.returncode

    env = os.environ.copy()
    project_cargo_home = ROOT / ".tools" / "cargo"
    project_rustup_home = ROOT / ".tools" / "rustup"
    project_cargo_bin = project_cargo_home / "bin"
    if project_cargo_bin.is_dir():
        env["PATH"] = f"{project_cargo_bin}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("CARGO_HOME", str(project_cargo_home))
        env.setdefault("RUSTUP_HOME", str(project_rustup_home))
    if not shutil.which("cargo", path=env.get("PATH")):
        raise SystemExit("Cargo is unavailable. Install Rust or provide the project-local .tools toolchain.")
    remap = f"--remap-path-prefix={Path.home()}=/build-home"
    existing = env.get("RUSTFLAGS", "").strip()
    env["RUSTFLAGS"] = f"{existing} {remap}".strip()
    env["CARGO_INCREMENTAL"] = "0"
    if args.trusted:
        identity = env.get("APPLE_SIGNING_IDENTITY", "").strip()
        try:
            count = sign_macho_tree(ROOT / "src-tauri" / "backend" / "studyhub-backend", identity)
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        print(f"Developer ID nested-code signing prepared: {count} Mach-O files")
    bundles = "app" if args.app_only else "app,dmg"
    return subprocess.run(
        [str(tauri), "build", "--bundles", bundles],
        cwd=ROOT,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
