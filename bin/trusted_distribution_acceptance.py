#!/usr/bin/env python3
"""Regression checks for the fail-closed macOS trusted-distribution pipeline."""

from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

from notarize_macos import accepted_result


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    entitlements_path = ROOT / "src-tauri" / config["bundle"]["macOS"]["entitlements"]
    entitlements = plistlib.loads(entitlements_path.read_bytes())
    workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text(encoding="utf-8")
    build = (ROOT / "bin" / "build_desktop.py").read_text(encoding="utf-8")
    nested = (ROOT / "bin" / "macos_code_signing.py").read_text(encoding="utf-8")
    verifier = (ROOT / "bin" / "verify_macos_trusted.py").read_text(encoding="utf-8")
    privacy = (ROOT / "bin" / "privacy_check.py").read_text(encoding="utf-8")
    artifact_privacy = (ROOT / "bin" / "desktop_artifact_privacy.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    required_secrets = {
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "KEYCHAIN_PASSWORD",
        "APPLE_TEAM_ID",
        "APPLE_API_ISSUER",
        "APPLE_API_KEY",
        "APPLE_API_PRIVATE_KEY",
    }
    results = {
        "existing_beta_version_unchanged": package["version"] == "0.3.0-beta.1",
        "existing_beta_remains_honestly_unsigned": "Unsigned beta, not notarized" in readme,
        "minimal_entitlements": entitlements == {},
        "debug_entitlement_absent": "com.apple.security.get-task-allow" not in str(entitlements),
        "local_preview_remains_ad_hoc": config["bundle"]["macOS"].get("signingIdentity") == "-",
        "trusted_build_requires_explicit_mode": "--trusted" in build and "APPLE_SIGNING_IDENTITY" in build,
        "nested_code_uses_hardened_runtime": "--options" in nested and '"runtime"' in nested and '"--timestamp"' in nested,
        "workflow_has_all_secret_boundaries": all(name in workflow for name in required_secrets),
        "workflow_tag_is_trusted_only": "build-trusted-arm64:" in workflow and "if: github.ref_type == 'tag'" in workflow,
        "workflow_pr_is_preview_only": "validate-unsigned-arm64:" in workflow and "github.ref_type != 'tag'" in workflow,
        "workflow_notarizes_app_and_dmg": workflow.count("bin/notarize_macos.py") == 2,
        "workflow_staples_and_checks_gatekeeper": "--require-notarization" in workflow and "verify_macos_trusted.py" in workflow,
        "workflow_publish_is_gated": "needs: build-trusted-arm64" in workflow,
        "workflow_cleans_ephemeral_credentials": "Remove ephemeral Apple credentials" in workflow,
        "workflow_registers_cleanup_before_decode": workflow.index("APPLE_CERTIFICATE_PATH=$CERTIFICATE_PATH")
        < workflow.index("base64 --decode"),
        "workflow_does_not_echo_certificate": 'echo "$APPLE_CERTIFICATE"' not in workflow,
        "notary_acceptance_is_strict": accepted_result('{"status":"Accepted"}')
        and not accepted_result('{"status":"Invalid"}')
        and not accepted_result("not json"),
        "verifier_rejects_adhoc": "Signature=adhoc" in verifier,
        "verifier_requires_developer_id": "Authority=Developer ID Application:" in verifier,
        "verifier_requires_notarized_gatekeeper": "Notarized Developer ID" in verifier,
        "credential_file_extensions_rejected": all(ext in privacy for ext in (".p12", ".p8", ".cer", ".keychain-db")),
        "certifi_ca_exception_is_exact": 'parts[-2:] == ("certifi", "cacert.pem")' in artifact_privacy,
        "history_credential_scan_in_ci": "git_history_secret_scan.py" in workflow,
    }
    failed = [name for name, passed in results.items() if not passed]
    print(json.dumps(results, indent=2, sort_keys=True))
    if failed:
        print("Trusted distribution acceptance failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Trusted distribution acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
