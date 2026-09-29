#!/usr/bin/env python3
"""Start the source backend in an isolated development workspace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV_ROOT = ROOT / ".studyhub-dev"

INHERITED_STATE = (
    "DATABASE_PATH",
    "DEMO_MODE",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_VECTOR_STORE_ID",
    "STUDYHUB_CACHE_DIR",
    "STUDYHUB_CONFIG_PATH",
    "STUDYHUB_DATA_DIR",
    "STUDYHUB_DESKTOP_TEST_ROOT",
    "STUDYHUB_LOG_DIR",
    "STUDYHUB_RUNTIME_DIR",
    "STUDYHUB_RUNTIME_PROFILE",
    "STUDY_LIBRARY_PATH",
)


def development_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in INHERITED_STATE:
        env.pop(key, None)
    runtime = DEV_ROOT / "runtime"
    config = DEV_ROOT / "config" / "settings.env"
    env.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": "development",
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "DATABASE_PATH": str(runtime / "data" / "studyhub.sqlite"),
            "STUDYHUB_CONFIG_PATH": str(config),
        }
    )
    return env


def main() -> int:
    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "server.py"), "serve", *sys.argv[1:]]
    try:
        return subprocess.call(command, cwd=ROOT, env=development_environment())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
