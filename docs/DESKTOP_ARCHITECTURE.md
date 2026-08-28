# Desktop Prototype Architecture

Status: experimental branch prototype only. No desktop binary has been published.

The Python-free packaging gate is proven on Apple Silicon through a packaged
`.app`, a stripped-PATH launch, and synthetic acceptance tests. A separate clean
Mac or VM has not been tested, so public distribution remains blocked.

StudyHub Local remains a local-first application for course files from any
university. A school or LMS API is not required.

## Current System

```text
User-controlled StudyLibrary
  -> Python scanner and extractors
  -> SQLite metadata, full-text chunks, notes, stars, and study state
  -> localhost HTTP API and static web UI
  -> optional user-owned OpenAI API and vector store
```

The current frontend is plain HTML, CSS, and JavaScript served by `server.py`.
The Python backend owns scanning, SQLite, previews, local search, Ask AI,
optional OpenAI synchronization, and the read-only MCP endpoint.

## Dependency Matrix

| Class | Dependency | Purpose |
| --- | --- | --- |
| Build-time | Node.js and npm | Install KaTeX and run the existing test/build scripts |
| Build-time | Rust toolchain | Compile the Tauri desktop shell |
| Build-time | Tauri CLI and crates | Build the internal macOS `.app` prototype |
| Runtime required | macOS WebKit | Render the existing StudyHub UI in the Tauri window |
| Runtime bundled | PyInstaller one-folder backend | Python 3.13, SQLite, standard library, and `certifi` |
| Runtime bundled | Compiled Tauri shell and static assets | Existing UI and local KaTeX distribution |
| Runtime optional | Poppler `pdftotext` / `pdfinfo` | Searchable PDF extraction and page metadata |
| Runtime optional | LibreOffice | High-fidelity local PPT/DOC visual preview conversion |
| Runtime optional | OpenAI API access | Source-grounded AI explanations and opt-in file search |

`certifi` is a small Python runtime dependency for verified OpenAI HTTPS
requests. Core browsing, local search, notes, stars, and Demo Mode do not need
OpenAI.

End users of the packaged prototype do not need Python, pip, a virtual
environment, Node.js, or npm. Those remain build-time dependencies only.

## Proven Prototype

The Tauri shell deliberately reuses the existing localhost application:

```text
StudyHub Local.app
  -> chooses an OS-assigned loopback port
  -> starts the packaged backend executable with explicit argument arrays
  -> waits for `/api/health`
  -> opens the exact backend origin in a WebView
  -> permits only that exact localhost origin and port
  -> exposes only folder selection, backend restart/retry, and safe diagnostics
  -> sends SIGTERM and waits when the app exits
```

Mutable runtime files are outside the app bundle:

```text
Application Support
  -> SQLite, extracted text, preview cache, logs

Application configuration
  -> selected StudyLibrary and non-secret settings

User-selected StudyLibrary
  -> original course files; never copied into the app bundle
```

Future updates and uninstall flows must treat these as separate ownership
domains. Updating or removing the app must not delete the user-controlled
StudyLibrary. Migration from an existing source install requires an explicit,
tested import plan; the prototype does not silently move or overwrite an
existing database, settings file, notes, indexes, or AI configuration.

The prototype uses Tauri [resource bundling](https://v2.tauri.app/develop/resources/)
for the complete PyInstaller one-folder directory, [native folder
dialogs](https://v2.tauri.app/plugin/dialog/), and [remote-origin capability
rules](https://v2.tauri.app/security/capabilities/). Rust launches the resource
executable as a separately monitored child process. Release builds have no
system-Python fallback; debug builds may still launch source Python.

## Packaging Decision

The selected prototype architecture is:

```text
Tauri application
  -> read-only bundled resources
  -> PyInstaller one-folder backend resource sidecar
  -> writable app data/config directories
  -> user-owned StudyLibrary remains external
```

PyInstaller one-folder was selected for reliability and inspectability:

| Option | Decision |
| --- | --- |
| PyInstaller one-folder | Selected; fast startup, inspectable resources, no extraction-on-launch step |
| PyInstaller one-file | Deferred; adds self-extraction and more startup/process complexity |
| Nuitka | Deferred; a larger build change without a demonstrated need |
| Raw bundled CPython | Rejected for this phase; more manual import, certificate, and path management |

The build uses a pinned uv-managed CPython 3.13.15 runtime. This avoids
builder-specific home paths found in another Python distribution and makes the
artifact privacy scan reproducible. PyInstaller and `certifi` versions are
pinned through `requirements-desktop.txt`.

Tauri remains a good fit because:

- It reuses the existing UI without shipping a second browser engine.
- Rust can own the Python sidecar lifecycle and enforce narrow native commands.
- Native folder selection works on macOS and has a Windows path later.
- The resulting shell is expected to be smaller than Electron.

Electron remains a fallback if backend lifecycle or WebKit
compatibility becomes fragile, but it adds a larger runtime and a second Node
process. A custom Swift wrapper would be small on macOS, but it would duplicate
desktop lifecycle work for Windows and increase platform-specific maintenance.

The internal release build dynamically remaps the builder's home directory from
Rust compiler paths and scans every file in the `.app`. The current synthetic
artifact contains no developer home path, secret, runtime database, or academic
document. Build output is ignored and is not committed.

Build and verify locally:

```bash
npm run desktop:setup
npm run desktop:build
npm run desktop:test:packaged
```

`uv`, Node/npm, Rust, and PyInstaller are builder requirements. They are not
requirements for opening the resulting app.

## Document Support

- PDF visual preview works through the WebView. Searchable extraction degrades
  when Poppler is missing.
- PPTX/DOCX visual preview uses local LibreOffice when available.
- Without LibreOffice, supported Office XML text remains readable/searchable and
  the visual pane explains the missing optional capability.
- Neither optional tool may block application startup.

Finder-launched apps may have a minimal PATH. Tool discovery therefore checks
the configured override first, then PATH, then known locations:

```text
Apple Silicon Homebrew: /opt/homebrew/bin
Intel Homebrew: /usr/local/bin
LibreOffice app: /Applications/LibreOffice.app/Contents/MacOS/soffice
```

The stripped-PATH packaged test proves startup and graceful missing-tool states.
Detection of an installed LibreOffice and Poppler from a real Finder launch is
implemented but still needs a separate clean-machine confirmation.

## AI Boundary

AI remains optional and uses the user's own OpenAI API account. OpenAI API usage
is billed separately; a ChatGPT subscription does not include API billing.
Core features remain available without AI.

The existing Responses API sets `store: false`, while vector-store files remain
provider resources until deleted or expired. The desktop UI must disclose that
enabled AI/indexing may send selected content to OpenAI. OpenAI's current [data
controls documentation](https://developers.openai.com/api/docs/guides/your-data)
is the source for provider-side retention behavior.

The packaged backend includes a verified `certifi` CA bundle and reports only
its availability, never its path. A real API-key request is not part of the
public synthetic artifact test.

Secure macOS Keychain storage is not implemented. The prototype does not copy a
maintainer key and does not fall back to plaintext key storage. AI being
unconfigured is a valid state and does not block local features.

## Acceptance Evidence

Proven with synthetic data on the current Apple Silicon Mac:

- `.app` starts with a PATH containing no Python, Node, npm, Poppler, or
  LibreOffice.
- Process inspection shows the Tauri executable and packaged backend, with no
  system Python or Node child.
- Demo browsing/search and custom-library scan/search/preview work.
- Native folder selection and selected-library persistence work.
- SQLite and notes persist outside the app bundle across restart.
- Multiple occupied localhost ports fall back safely.
- Backend crash shows Retry and privacy-safe Copy Diagnostics actions.
- Normal quit and window close stop the child and release its port.
- The app and sidecar contain no secret, private home path, runtime DB, or
  academic binary in the artifact scan.

## Experimental Or Not Implemented

1. A truly clean physical Mac or VM has not been tested; the current result is
   `SIMULATED / PARTIAL`, not a distribution claim.
2. The build is Apple Silicon only and unsigned. Signing, notarization, DMG, and
   public release are not implemented.
3. Keychain-backed BYOK is not implemented.
4. Poppler and LibreOffice are not bundled; their missing states are graceful.
5. Installed-tool detection from a separate Finder-launched clean Mac remains
   to be confirmed.
6. Human VoiceOver testing remains incomplete.

The future update design must preserve the selected StudyLibrary, notes,
settings, stars, indexes, local database, and AI configuration. Auto-update is
not implemented in this prototype.

Signing, notarization, DMG creation, auto-update, Windows packaging, App Store
work, telemetry, SaaS infrastructure, and public desktop release are explicitly
outside this spike.
