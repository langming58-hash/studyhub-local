#!/usr/bin/env python3
"""Offline lock regressions; optional real uv hash-rejection check on macOS."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import desktop_dependencies as deps


def rejects(operation) -> bool:
    try:
        operation()
    except (ValueError, KeyError):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", action="store_true")
    args = parser.parse_args()
    runtime, build = deps.validate_locks()
    sample = "fsrs==6.3.2 --hash=sha256:" + "1" * 64
    checks = {
        "desktop_hash_locks_match_inputs": bool(runtime) and bool(build),
        "runtime_versions_match_build": all(build.get(n) == v for n, v in runtime.items()),
        "pyinstaller_is_build_only": "pyinstaller" in build and "pyinstaller" not in runtime,
        "valid_exact_hashed_pin_accepted": deps.parse_lock(sample) == {"fsrs": "6.3.2"},
    }
    for name, source in {
        "unhashed_pin": "fsrs==6.3.2",
        "whitespace_without_hash": "fsrs==6.3.2   ",
        "unpinned_range": "fsrs>=6.3.2 --hash=sha256:" + "1" * 64,
        "wildcard_version": "fsrs==6.* --hash=sha256:" + "1" * 64,
        "invalid_hash": "fsrs==6.3.2 --hash=sha256:123",
        "duplicate_pin": sample + "\n" + sample,
        "external_index": "--extra-index-url https://example.invalid/simple\n" + sample,
        "empty_lock": "# no packages\n",
    }.items():
        checks["rejects_" + name] = rejects(lambda source=source: deps.parse_lock(source))

    originals = {name: (deps.ROOT / name).read_bytes() for name in (*map(Path, deps.INPUTS), deps.RUNTIME, deps.BUILD, deps.MANIFEST)}

    def changed_rejected(changes: dict[Path, bytes], refresh_hashes: bool = False) -> bool:
        with tempfile.TemporaryDirectory(prefix="studyhub-lock-regression-") as raw:
            root = Path(raw)
            for name, data in originals.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(changes.get(name, data))
            if refresh_hashes:
                metadata = json.loads((root / deps.MANIFEST).read_text())
                metadata["locks"] = {str(name): deps.digest(root / name) for name in (deps.RUNTIME, deps.BUILD)}
                (root / deps.MANIFEST).write_text(json.dumps(metadata))
            return rejects(lambda: deps.validate_locks(root))

    checks["rejects_input_drift"] = changed_rejected({Path("requirements.txt"): originals[Path("requirements.txt")] + b"\n# changed\n"})
    checks["rejects_lock_byte_drift"] = changed_rejected({deps.RUNTIME: originals[deps.RUNTIME] + b"\n# changed\n"})
    metadata = json.loads(originals[deps.MANIFEST])
    metadata["target"]["python"] = "3.14.0"
    checks["rejects_target_drift"] = changed_rejected({deps.MANIFEST: json.dumps(metadata).encode()})
    wrong_runtime = originals[deps.RUNTIME].replace(b"fsrs==6.3.2", b"fsrs==0.0.0")
    checks["rejects_shared_version_drift"] = changed_rejected({deps.RUNTIME: wrong_runtime}, True)
    checks["rejects_direct_pin_violation"] = changed_rejected({
        deps.RUNTIME: wrong_runtime,
        deps.BUILD: originals[deps.BUILD].replace(b"fsrs==6.3.2", b"fsrs==0.0.0"),
    }, True)
    checks["exact_environment_accepted"] = deps.inventory_matches(dict(build), build)
    checks["rejects_extra_distribution"] = not deps.inventory_matches({**build, "synthetic-extra": "1.0.0"}, build)
    checks["rejects_missing_distribution"] = not deps.inventory_matches({n: v for n, v in build.items() if n != "fsrs"}, build)
    checks["rejects_environment_version_drift"] = not deps.inventory_matches({**build, "fsrs": "0.0.0"}, build)
    with patch.object(deps.platform, "system", return_value="Linux"):
        checks["rejects_unsupported_build_platform"] = rejects(deps.require_platform)
    setup = (deps.ROOT / "bin/setup_desktop.py").read_text()
    workflow = (deps.ROOT / ".github/workflows/release-desktop.yml").read_text()
    checks["setup_requires_hashes_binary_sync"] = all(value in setup for value in ('"sync"', '"--require-hashes"', '"--only-binary"', '"--strict"', "validate_locks(ROOT)", "require_uv(uv)"))
    checks["backend_checks_environment_before_freezing"] = "verify_environment(ROOT)" in (deps.ROOT / "bin/build_desktop_backend.py").read_text()
    checks["macos_ci_uses_locked_python"] = '.venv-desktop/bin" >> "$GITHUB_PATH"' in workflow and "python -m pip install -r requirements.txt" not in workflow
    checks["lock_changes_trigger_macos_ci"] = '"requirements/locks/**"' in workflow
    checks["uv_installer_version_pinned"] = 'version: "' + deps.UV_VERSION + '"' in workflow

    if args.installer:
        deps.verify_environment()
        for name, tag, expected in (
            ("macos13_arm64", "cp313-cp313-macosx_13_0_arm64", True),
            ("pure_python", "py3-none-any", True),
            ("macos14_only", "cp313-cp313-macosx_14_0_arm64", False),
            ("intel_only", "cp313-cp313-macosx_13_0_x86_64", False),
            ("wrong_python", "cp314-cp314-macosx_13_0_arm64", False),
        ):
            checks["wheel_target_" + name] = deps.wheel_compatible("Tag: " + tag + "\n") == expected
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit("uv is required for installer acceptance")
        deps.require_uv(uv)
        with tempfile.TemporaryDirectory(prefix="studyhub-hash-rejection-") as raw:
            tmp = Path(raw)
            subprocess.run([uv, "venv", "--python", sys.executable, str(tmp / "venv")], check=True, capture_output=True)
            bad = tmp / "bad.txt"
            bad.write_text("fsrs==" + runtime["fsrs"] + " --hash=sha256:" + "0" * 64 + "\n")
            result = subprocess.run([
                uv, "pip", "sync", str(bad), "--python", str(tmp / "venv/bin/python"),
                "--require-hashes", "--only-binary", ":all:",
                "--default-index", "https://pypi.org/simple",
            ], check=False, capture_output=True, text=True)
            checks["uv_rejects_wrong_artifact_hash"] = result.returncode != 0 and "hash mismatch" in result.stderr.lower()
            if not checks["uv_rejects_wrong_artifact_hash"]:
                print("Hash rejection did not fail for the expected reason:", result.stderr)
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    print(f"Python lock acceptance: {'PASS' if all(checks.values()) else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
