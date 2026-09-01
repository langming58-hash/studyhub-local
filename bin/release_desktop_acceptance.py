#!/usr/bin/env python3
"""Static and artifact acceptance checks for public macOS distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "StudyHub Local.app"
RELEASE_DIR = ROOT / ".release"
VERSION = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
DMG_NAME = f"StudyHub-Local-v{VERSION}-macos-arm64.dmg"


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
    capability = (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")

    results = {
        "version_consistent": package["version"] == VERSION
        and config["version"] == VERSION
        and f'version = "{VERSION}"' in cargo
        and f'"version": "{VERSION}"' in server,
        "app_and_dmg_targets": config["bundle"]["targets"] == ["app", "dmg"],
        "apple_silicon_minimum_macos": config["bundle"]["macOS"]["minimumSystemVersion"] == "13.0",
        "default_preview_is_ad_hoc": config["bundle"]["macOS"].get("signingIdentity") == "-",
        "minimal_entitlements_configured": config["bundle"]["macOS"].get("entitlements") == "Entitlements.plist",
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
        "workflow_requires_developer_id": "--require-identity" in workflow and "APPLE_CERTIFICATE" in workflow,
        "workflow_notarizes_app_and_dmg": workflow.count("bin/notarize_macos.py") == 2,
        "workflow_requires_gatekeeper": "--require-notarization" in workflow,
        "workflow_publish_depends_on_trusted_build": "needs: build-trusted-arm64" in workflow,
        "workflow_build_is_read_only": "permissions:\n  contents: read" in workflow,
        "workflow_write_is_publish_only": "publish-prerelease:" in workflow and "contents: write" in workflow,
        "workflow_uses_clear_filename": "StudyHub-Local-v$VERSION-macos-arm64.dmg" in workflow,
    }

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
            }
        )

    failed = [name for name, passed in results.items() if not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Desktop release acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Desktop release acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
