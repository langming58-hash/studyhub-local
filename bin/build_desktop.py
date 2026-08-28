#!/usr/bin/env python3
"""Build the internal desktop prototype without embedding the builder's home path."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tauri = ROOT / "node_modules" / ".bin" / "tauri"
    if not tauri.exists():
        raise SystemExit("Tauri CLI is unavailable. Run npm install first.")

    env = os.environ.copy()
    remap = f"--remap-path-prefix={Path.home()}=/build-home"
    existing = env.get("RUSTFLAGS", "").strip()
    env["RUSTFLAGS"] = f"{existing} {remap}".strip()
    env["CARGO_INCREMENTAL"] = "0"
    return subprocess.run(
        [str(tauri), "build", "--bundles", "app"],
        cwd=ROOT,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
