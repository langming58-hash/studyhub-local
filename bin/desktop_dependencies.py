#!/usr/bin/env python3
"""Generate and verify the supported desktop's hash-locked Python environments."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.15"
UV_VERSION = "0.12.3"
TARGET = {"python": PYTHON_VERSION, "platform": "aarch64-apple-darwin", "macos_minimum": "13.0", "uv": UV_VERSION}
INPUTS = ("requirements.txt", "requirements-desktop.txt")
DIRECTORY = Path("requirements/locks")
RUNTIME = DIRECTORY / "runtime-macos-arm64-py313.txt"
BUILD = DIRECTORY / "desktop-macos-arm64-py313.txt"
MANIFEST = DIRECTORY / "desktop-macos-arm64-py313.json"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_lock(source: str) -> dict[str, str]:
    """Accept only uv's exact-pin/hash output, not general pip input syntax."""
    result = {}
    for line in source.replace("\\\n", " ").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*([\w.-]+)==([\w.+!-]+)\s+(.+?)\s*", line)
        hashes = match[3].split() if match else []
        if not hashes or not all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", value) for value in hashes):
            raise ValueError("Lock entry must have one exact version and SHA-256 hashes")
        name = canonical(match[1])
        if name in result:
            raise ValueError("Duplicate locked distribution: " + name)
        result[name] = match[2]
    if not result:
        raise ValueError("Empty dependency lock")
    return result


def validate_locks(root: Path = ROOT) -> tuple[dict[str, str], dict[str, str]]:
    metadata = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if metadata["schema_version"] != 1 or metadata["target"] != TARGET:
        raise ValueError("Desktop lock target/tool version drift")
    expected_inputs = {name: digest(root / name) for name in INPUTS}
    expected_locks = {str(name): digest(root / name) for name in (RUNTIME, BUILD)}
    if metadata["inputs"] != expected_inputs or metadata["locks"] != expected_locks:
        raise ValueError("Dependency inputs or hash locks changed; regenerate and review")
    runtime, build = (parse_lock((root / name).read_text(encoding="utf-8")) for name in (RUNTIME, BUILD))
    if not {"certifi", "fsrs", "markitdown"} <= runtime.keys():
        raise ValueError("Missing desktop runtime roots")
    if "pyinstaller" in runtime or "pyinstaller" not in build:
        raise ValueError("Runtime/build dependency roles changed")
    if any(build.get(name) != version for name, version in runtime.items()):
        raise ValueError("Runtime/build version divergence")
    for filename, pins in ((INPUTS[0], runtime), (INPUTS[1], build)):
        for line in (root / filename).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith(("#", "-r ")):
                continue
            # The project's intent files use numeric == pins and one >= floor.
            match = re.fullmatch(r"([\w.-]+)(?:\[[\w,.-]+\])?(==|>=)([\d.]+)", line)
            if match is None:
                raise ValueError("Unsupported direct requirement; review lock validation")
            actual = pins.get(canonical(match[1]), "")
            valid = actual == match[3] if match[2] == "==" else (
                bool(re.fullmatch(r"\d+(?:\.\d+)*", actual))
                and tuple(map(int, actual.split("."))) >= tuple(map(int, match[3].split(".")))
            )
            if not valid:
                raise ValueError("Locked direct requirement does not satisfy input: " + match[1])
    return runtime, build


def require_uv(uv: str) -> None:
    version = subprocess.check_output([uv, "--version"], text=True).split()[1]
    if version != UV_VERSION:
        raise ValueError("Desktop dependency tooling requires uv " + UV_VERSION)


def require_platform() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Desktop Python locks support macOS arm64 only")
    macos = platform.mac_ver()[0].split(".")
    if not macos[0] or int(macos[0]) < 13:
        raise ValueError("Desktop Python locks require macOS 13 or later")


def inventory_matches(actual: dict[str, str], expected: dict[str, str]) -> bool:
    return actual == expected


def wheel_compatible(wheel_metadata: str) -> bool:
    # packaging is itself hash-locked. Do not duplicate wheel compatibility rules.
    from packaging.tags import compatible_tags, cpython_tags, mac_platforms, parse_tag

    platforms = list(mac_platforms((13, 0), "arm64"))
    supported = set(cpython_tags((3, 13), ["cp313"], platforms))
    supported.update(compatible_tags((3, 13), "cp313", platforms))
    actual = set()
    for line in wheel_metadata.splitlines():
        if line.startswith("Tag: "):
            actual.update(parse_tag(line.removeprefix("Tag: ")))
    return bool(actual & supported)


def verify_numpy_native_privacy(distribution) -> int:
    """Reject upstream native payloads before freezing; never print matched bytes."""
    from desktop_artifact_privacy import PATTERNS

    native = [member for member in distribution.files or () if member.suffix in {".so", ".dylib"}]
    if not native:
        raise ValueError("NumPy native payload inventory is missing")
    for member in native:
        data = distribution.locate_file(member).read_bytes()
        for category, pattern in PATTERNS.items():
            if pattern.search(data):
                raise ValueError("NumPy native privacy failure: " + category + "; " + member.name)
    return len(native)


def verify_environment(root: Path = ROOT) -> None:
    _, expected = validate_locks(root)
    require_platform()
    if sys.implementation.name != "cpython" or tuple(sys.version_info[:3]) != tuple(map(int, PYTHON_VERSION.split("."))):
        raise ValueError("Desktop build requires CPython " + PYTHON_VERSION)
    distributions = list(importlib.metadata.distributions())
    actual = {canonical(d.metadata["Name"]): d.version for d in distributions}
    if len(actual) != len(distributions):
        raise ValueError("Duplicate distributions in desktop build environment")
    if not inventory_matches(actual, expected):
        changed = sorted(name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))
        raise ValueError("Desktop dependency environment drift: " + ", ".join(changed))
    for distribution in distributions:
        if not wheel_compatible(distribution.read_text("WHEEL") or ""):
            raise ValueError("Wheel is incompatible with the locked desktop target: " + distribution.metadata["Name"])
        if canonical(distribution.metadata["Name"]) == "numpy":
            count = verify_numpy_native_privacy(distribution)
            print(f"NumPy native privacy preflight: PASS ({count} files)")
    print(f"Desktop dependency environment: PASS ({len(expected)} distributions)")


def generate(root: Path = ROOT, constraints: Path | None = None) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("uv is required to generate desktop locks")
    require_uv(uv)
    require_platform()
    python = root / ".venv-desktop/bin/python"
    if not python.is_file():
        raise ValueError("Run npm run desktop:setup before regenerating locks")
    with tempfile.TemporaryDirectory(prefix="studyhub-python-lock-") as temporary:
        tmp = Path(temporary)
        preferred = constraints
        if preferred is None and (root / BUILD).exists():
            preferred = tmp / "preferred.txt"
            preferred.write_text("\n".join(f"{name}=={version}" for name, version in parse_lock((root / BUILD).read_text()).items()) + "\n")
        env = os.environ.copy()
        env["MACOSX_DEPLOYMENT_TARGET"] = TARGET["macos_minimum"]

        def compile_lock(source: str, output: Path, preference: Path | None) -> None:
            command = [
                uv, "pip", "compile", source, "--python-version", PYTHON_VERSION,
                "--python", str(python),
                "--python-platform", TARGET["platform"], "--only-binary", ":all:",
                "--generate-hashes", "--no-header", "--no-annotate",
                "--default-index", "https://pypi.org/simple", "--output-file", str(output),
            ]
            if preference is not None:
                command.extend(["--constraint", str(preference.resolve())])
            subprocess.run(command, cwd=root, env=env, check=True, stdout=subprocess.DEVNULL)

        build, runtime = tmp / "build.txt", tmp / "runtime.txt"
        compile_lock("requirements-desktop.txt", build, preferred)
        shared = tmp / "shared.txt"
        shared.write_text("\n".join(f"{name}=={version}" for name, version in parse_lock(build.read_text()).items()) + "\n")
        compile_lock("requirements.txt", runtime, shared)
        (root / DIRECTORY).mkdir(parents=True, exist_ok=True)
        for destination, source in ((BUILD, build), (RUNTIME, runtime)):
            (root / destination).write_bytes(source.read_bytes())
        metadata = {
            "schema_version": 1, "target": TARGET,
            "inputs": {name: digest(root / name) for name in INPUTS},
            "locks": {str(name): digest(root / name) for name in (RUNTIME, BUILD)},
        }
        (root / MANIFEST).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_pins, build_pins = validate_locks(root)
    print(f"Desktop locks generated: runtime={len(runtime_pins)}, build={len(build_pins)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--constraints", type=Path, help="Explicit reviewed version constraints for generation")
    parser.add_argument("--environment", action="store_true", help="Also verify the current desktop build environment")
    args = parser.parse_args()
    try:
        if args.generate:
            generate(constraints=args.constraints)
        else:
            if args.constraints:
                parser.error("--constraints requires --generate")
            runtime, build = validate_locks()
            print(f"Desktop hash locks: PASS (runtime={len(runtime)}, build={len(build)})")
        if args.environment:
            verify_environment()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
