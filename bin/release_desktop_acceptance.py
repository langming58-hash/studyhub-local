#!/usr/bin/env python3
"""Static and artifact acceptance checks for public macOS distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import prepare_macos_release as release
from desktop_artifact_privacy import mounted_app


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"
RELEASE_DIR = ROOT / ".release"
VERSION = release.release_metadata()["version"]
DMG_NAME = f"StudyHub-Local-v{VERSION}-macos-arm64.dmg"


def version_guard_checks() -> dict[str, bool]:
    results = {}
    try:
        release.release_metadata("v0.0.0-beta.0")
    except SystemExit:
        results["mismatched_tag_rejected"] = True
    else:
        results["mismatched_tag_rejected"] = False
    # Exercise each declaration independently; stale lockfiles or backup metadata
    # must fail before a build can be named or published.
    for name in release.versions():
        changed = release.versions()
        changed[name] = "0.0.0-beta.0"
        with patch.object(release, "versions", return_value=changed):
            try:
                release.release_metadata()
            except SystemExit:
                results[f"mismatch_rejected_{name}"] = True
            else:
                results[f"mismatch_rejected_{name}"] = False
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        info = {"CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION}
        info[key] = "0.0.0-beta.0"
        try:
            release.validate_bundle_version(info, VERSION)
        except SystemExit:
            results[f"mismatch_rejected_{key}"] = True
        else:
            results[f"mismatch_rejected_{key}"] = False
    with tempfile.TemporaryDirectory(prefix="studyhub-release-metadata-") as directory:
        output = Path(directory) / "output"
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "prepare_macos_release.py"),
             "--check-only", "--tag", f"v{VERSION}", "--github-output", str(output)],
            capture_output=True, text=True, check=False,
        )
        results["validated_workflow_outputs"] = result.returncode == 0 and all(
            f"{key}={value}\n" in output.read_text()
            for key, value in release.release_metadata().items()
        )
    return results


def bundle_inventory(app: Path) -> dict[str, str]:
    return {str(path.relative_to(app)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in app.rglob("*") if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", action="store_true")
    args = parser.parse_args()

    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text(encoding="utf-8")
    frontend = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    capability = (ROOT / "src-tauri" / "capabilities" / "studyhub-production.json").read_text(encoding="utf-8")

    results = {
        "version_consistent": package["version"] == VERSION
        and config["version"] == VERSION
        and f'version = "{VERSION}"' in cargo
        and f'"version": "{VERSION}"' in server,
        "app_and_dmg_targets": config["bundle"]["targets"] == ["app", "dmg"],
        "apple_silicon_minimum_macos": config["bundle"]["macOS"]["minimumSystemVersion"] == "13.0",
        "unsigned_beta_is_honest": config["bundle"]["macOS"].get("signingIdentity") == "-",
        "icon_configured": "icons/icon.icns" in config["bundle"].get("icon", []),
        "readme_download_link": f"releases/tag/v{VERSION}" in readme,
        "readme_install_boundary": all(term in readme for term in ("Apple Silicon", "macOS 13", "Unsigned beta", "Open Anyway")),
        "readme_zh_install_boundary": all(term in readme_zh for term in ("Apple Silicon", "macOS 13", "未签名", "仍要打开")),
        "readme_does_not_require_terminal": "Terminal is not required" in readme,
        "settings_public_links": all(key in frontend for key in ("latestRelease", "repository", "reportIssue")),
        "public_links_scoped": '"identifier": "opener:allow-open-url"' in capability
        and "github.com/langming58-hash/studyhub-local" in capability,
        "workflow_runs_full_ci": "npm run ci" in workflow,
        "workflow_runs_packaged_acceptance": "desktop:test:packaged" in workflow,
        "workflow_scans_dmg": "desktop_artifact_privacy.py --dmg" in workflow,
        "workflow_verifies_checksum": "shasum -a 256 -c" in workflow,
        "workflow_publishes_prerelease": "--prerelease" in workflow and "--verify-tag" in workflow,
        "workflow_build_is_read_only": "permissions:\n  contents: read" in workflow,
        "workflow_write_is_publish_only": "publish-prerelease:" in workflow and "contents: write" in workflow,
        "workflow_uses_validated_metadata": '--check-only --github-output "$GITHUB_OUTPUT"' in workflow
        and "steps.release.outputs.dmg_name" in workflow
        and "needs.build-macos-arm64.outputs.artifact_stem" in workflow,
        "workflow_publish_requires_tag_push": "github.event_name == 'push' && github.ref_type == 'tag'" in workflow,
        "workflow_requires_exact_main": '$(git rev-parse origin/main)' in workflow,
        "workflow_has_no_stale_beta_paths": "v0.3.0-beta.1" not in workflow,
    }
    results.update(version_guard_checks())

    if args.artifacts:
        dmg = RELEASE_DIR / DMG_NAME
        checksum = dmg.with_suffix(dmg.suffix + ".sha256")
        info_path = APP / "Contents" / "Info.plist"
        expected = checksum.read_text(encoding="ascii").split()[0] if checksum.is_file() else ""
        actual = hashlib.sha256(dmg.read_bytes()).hexdigest() if dmg.is_file() else ""
        info = plistlib.loads(info_path.read_bytes()) if info_path.is_file() else {}
        executable = APP / "Contents" / "MacOS" / str(info.get("CFBundleExecutable") or "")
        lipo = subprocess.run(["lipo", "-archs", str(executable)], text=True, capture_output=True, check=False)
        results.update(
            {
                "app_artifact_exists": APP.is_dir(),
                "dmg_artifact_exists": dmg.is_file(),
                "checksum_matches": bool(expected) and expected == actual,
                "artifact_arm64_only": lipo.returncode == 0 and lipo.stdout.strip() == "arm64",
                "artifact_bundle_identifier": info.get("CFBundleIdentifier") == "io.studyhublocal.desktop",
                "artifact_minimum_macos": str(info.get("LSMinimumSystemVersion")) == "13.0",
                "artifact_icon_declared": bool(info.get("CFBundleIconFile")),
                "artifact_exact_version": info.get("CFBundleShortVersionString") == VERSION
                and info.get("CFBundleVersion") == VERSION,
            }
        )
        mounted, mount_point = mounted_app(dmg)
        try:
            results["dmg_matches_verified_app_bytes"] = bundle_inventory(mounted) == bundle_inventory(APP)
        finally:
            subprocess.run(["hdiutil", "detach", mount_point], check=False, capture_output=True)

    failed = [name for name, passed in results.items() if not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Desktop release acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Desktop release acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
