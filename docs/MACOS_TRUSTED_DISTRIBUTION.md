# macOS Trusted Distribution

This document is for StudyHub Local maintainers. It contains no signing credentials.

## Current status

`v0.3.0-beta.1` is an immutable, unsigned and unnotarized Apple Silicon prerelease. Do not replace its tag, DMG, checksum, or release notes.

The repository now has a fail-closed Developer ID and notarization pipeline, but the required Apple identity and GitHub Actions secrets are not configured. Until the complete trusted pipeline passes, README and release pages must continue to describe public artifacts as unsigned and not notarized.

## Apple membership

Direct distribution trusted by Gatekeeper requires the paid Apple Developer Program. Apple currently lists the membership as **99 USD per membership year**, or local currency where available:

- <https://developer.apple.com/programs/whats-included/>
- <https://developer.apple.com/programs/enroll/>

A free Apple developer account cannot notarize a direct-download app.

## Actions the Account Holder must complete

1. Enroll in the Apple Developer Program as an individual or organization.
2. In Certificates, Identifiers & Profiles, create a **Developer ID Application** certificate. Apple requires the Account Holder role for this certificate type: <https://developer.apple.com/help/account/certificates/create-developer-id-certificates/>.
3. Install the certificate in the login Keychain and confirm that it appears under **My Certificates** with its private key.
4. Export that certificate and private key as a password-protected `.p12`. Keep the file and password private.
5. In App Store Connect, create a Notary API key with the minimum suitable developer access. Record the Issuer ID and Key ID, and securely retain the downloaded `.p8` private key. Apple only offers the private-key download once.
6. Add the required values directly to the public repository's **Settings -> Secrets and variables -> Actions** page. Never paste them into an issue, pull request, commit, screenshot, chat, or log.

Required GitHub Actions secret names:

```text
APPLE_CERTIFICATE
APPLE_CERTIFICATE_PASSWORD
KEYCHAIN_PASSWORD
APPLE_TEAM_ID
APPLE_API_ISSUER
APPLE_API_KEY
APPLE_API_PRIVATE_KEY
```

`APPLE_CERTIFICATE` is the base64-encoded `.p12`. `APPLE_API_PRIVATE_KEY` is the complete private `.p8` content. `KEYCHAIN_PASSWORD` is a random CI-only password, not the Mac login password.

Do not send any secret value to Codex or another person. After the secrets are configured, report only:

```text
Apple signing secrets configured.
```

## Trusted tag flow

For a future prerelease such as `v0.3.0-beta.2`, the tag must exactly match the version committed in `package.json`, Tauri config, Cargo metadata, and the backend. The release workflow then performs:

```text
normal CI
-> import certificate into an ephemeral Keychain
-> verify one matching Developer ID Application identity
-> sign bundled backend and nested Mach-O code with hardened runtime + timestamp
-> sign and verify the app
-> notarize and staple the app
-> create and Developer ID sign the DMG
-> notarize and staple the DMG
-> Gatekeeper assessment
-> packaged acceptance and privacy scans
-> Git history credential scan
-> SHA-256
-> GitHub prerelease publication
```

Any failure prevents publication. The ephemeral certificate, API key, and Keychain are removed from the runner even when the job fails.

Apple's current requirements are documented at:

- <https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution>
- <https://developer.apple.com/documentation/security/customizing-the-notarization-workflow>
- <https://v2.tauri.app/distribute/sign/macos/>

## Release claims

Use **Developer ID signed** only after the downloaded artifact reports a Developer ID Application authority and passes strict recursive code-signature verification.

Use **Notarized by Apple** only after `notarytool` returns `Accepted`, tickets are stapled and validated, and Gatekeeper reports a notarized Developer ID source.

Never describe an ad-hoc signature as a trusted distribution signature.
