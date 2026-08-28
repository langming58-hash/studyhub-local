# Desktop Prototype Architecture

Status: development spike only. No desktop binary has been published.

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
| Runtime required today | Python 3 | Run the backend during the architecture spike |
| Runtime required | macOS WebKit | Render the existing StudyHub UI in the Tauri window |
| Runtime required | SQLite | Provided by Python's standard library |
| Runtime required | Compiled static assets | Existing UI and local KaTeX distribution |
| Runtime optional | Poppler `pdftotext` / `pdfinfo` | Searchable PDF extraction and page metadata |
| Runtime optional | LibreOffice | High-fidelity local PPT/DOC visual preview conversion |
| Runtime optional | OpenAI API access | Source-grounded AI explanations and opt-in file search |

`certifi` is a small Python runtime dependency for verified OpenAI HTTPS
requests. Core browsing, local search, notes, stars, and Demo Mode do not need
OpenAI.

## Desktop Spike

The Tauri shell deliberately reuses the existing localhost application:

```text
StudyHub Local.app
  -> chooses an OS-assigned loopback port
  -> starts the Python backend with explicit argument arrays
  -> waits for `/api/health`
  -> opens the exact backend origin in a WebView
  -> permits only that exact localhost origin and port
  -> exposes only folder selection and backend restart commands
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

The spike uses Tauri's documented support for [external
binaries](https://v2.tauri.app/develop/sidecar/), [native folder
dialogs](https://v2.tauri.app/plugin/dialog/), and [remote-origin capability
rules](https://v2.tauri.app/security/capabilities/). The final packaged backend
should be an explicit sidecar rather than a system Python command.

## Technology Decision

Tauri is recommended for the next prototype phase.

- It reuses the existing UI without shipping a second browser engine.
- Rust can own the Python sidecar lifecycle and enforce narrow native commands.
- Native folder selection works on macOS and has a Windows path later.
- The resulting shell is expected to be smaller than Electron.

Electron remains a viable fallback if Python sidecar lifecycle or WebKit
compatibility becomes fragile, but it adds a larger runtime and a second Node
process. A custom Swift wrapper would be small on macOS, but it would duplicate
desktop lifecycle work for Windows and increase platform-specific maintenance.

## Backend Packaging Recommendation

Use PyInstaller in one-folder mode first, then register the generated executable
as a Tauri sidecar. One-folder mode is easier to inspect and debug than a
self-extracting one-file binary, and it avoids rewriting the backend. Nuitka may
be benchmarked later if startup or bundle integrity is inadequate. A bundled raw
Python runtime has more dependency and path-management burden.

The current spike intentionally still needs Python on the test machine. It is
not a normal-user package and must not be published as a download.

The internal release build dynamically remaps the builder's home directory from
Rust compiler paths and has a separate artifact privacy scan. This prevents an
unsigned test bundle from carrying a developer home path, secrets, runtime
databases, or academic documents.

## Document Support

- PDF visual preview works through the WebView. Searchable extraction degrades
  when Poppler is missing.
- PPTX/DOCX visual preview uses local LibreOffice when available.
- Without LibreOffice, supported Office XML text remains readable/searchable and
  the visual pane explains the missing optional capability.
- Neither optional tool may block application startup.

## AI Boundary

AI remains optional and uses the user's own OpenAI API account. OpenAI API usage
is billed separately; a ChatGPT subscription does not include API billing.
Core features remain available without AI.

The existing Responses API sets `store: false`, while vector-store files remain
provider resources until deleted or expired. The desktop UI must disclose that
enabled AI/indexing may send selected content to OpenAI. OpenAI's current [data
controls documentation](https://developers.openai.com/api/docs/guides/your-data)
is the source for provider-side retention behavior.

Secure macOS Keychain storage is required before the desktop AI setup can pass.
The spike does not copy a maintainer key and does not fall back to plaintext key
storage.

## Known Blockers Before Tester Build

1. Package Python and `certifi` as a tested architecture-specific sidecar.
2. Implement Keychain-backed BYOK connect, test, and disconnect flows.
3. Decide whether Poppler should be bundled or remain a documented optional tool.
4. Test LibreOffice discovery from a Finder-launched app with a minimal PATH.
5. Complete fresh-machine, crash-recovery, offline, and human VoiceOver testing.

The future update design must preserve the selected StudyLibrary, notes,
settings, stars, indexes, local database, and AI configuration. Auto-update is
not implemented in this prototype.

Signing, notarization, DMG creation, auto-update, Windows packaging, App Store
work, telemetry, SaaS infrastructure, and public desktop release are explicitly
outside this spike.
