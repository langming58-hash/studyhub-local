# Desktop Phase 2 Acceptance Report

Date: 2026-08-28

Branch: `codex/desktop-prototype`

Scope: internal Apple Silicon macOS prototype. No release, DMG, signing,
notarization, or merge to `main` was performed.

## Test Boundary

All StudyLibrary content was synthetic. Tests used only the repository's
`TEST1001`, `TEST2001`, and `TEST3001` fixtures plus temporary runtime folders.
No private StudyLibrary, private database, local API key, provider ID, or real
academic file was used.

## Packaging

```text
Backend: PyInstaller 6.22.2 one-folder
Python runtime: uv-managed CPython 3.13.15, arm64
HTTPS CA bundle: certifi 2026.7.22
Shell: Tauri 2, arm64
App size in test build: approximately 41 MB
```

The complete one-folder backend is a read-only Tauri resource. The release
shell launches its executable directly and has no system-Python fallback.
Mutable data is written to Tauri's app data/config locations, outside the app
bundle. Original StudyLibrary files remain external and unchanged.

## Automated Results

`npm run desktop:test:packaged` passed:

- packaged sidecar and compiled resources present
- stripped-PATH backend launch
- packaged/frozen health signal
- loopback-only dynamic port
- three consecutive occupied-port fallback
- Demo Mode scan, courses, and search
- custom synthetic library scan, rescan, search, and text preview
- note creation and restart persistence
- verified CA bundle present
- missing Poppler and LibreOffice remain nonfatal
- clean SIGTERM shutdown and reopen
- artifact privacy scan

The artifact privacy scan passed across 105 files. It found no home-directory
path, secret pattern, API/provider ID, private configuration, runtime database,
or academic document.

## Manual Packaged-App Results

The `.app` was launched with a PATH pointing to an empty directory. Process
inspection showed only the Tauri app and its packaged backend; no system Python
or Node process was used. Demo Mode, native folder selection, a custom synthetic
library, search, preview, settings persistence, note persistence, backend crash
fallback, retry, diagnostics copying, normal quit, window close, and port
release passed.

Diagnostics contained only app version, OS, architecture, packaged status, and
a safe error category. They contained no filesystem path or credential.

## Fresh-Machine Status

```text
Truly clean physical/VM Mac: NOT TESTED
Current gate: SIMULATED / PARTIAL
```

The stripped-PATH test is strong evidence that end users do not need Python,
pip, Node, npm, Poppler, or LibreOffice for core Demo/local-text workflows. A
separate clean Apple Silicon Mac running macOS 13 or newer is still required
before a tester distribution claim.
