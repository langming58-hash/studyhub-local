# Desktop Architecture

Status: **CURRENT packaged desktop architecture for `v0.3.0-beta.1`**.

The Python-free end-user packaging gate is proven on Apple Silicon through a
packaged `.app`, a stripped-PATH launch, synthetic acceptance tests, and a
public DMG prerelease. The current DMG is unsigned and not notarized. A separate
clean physical Mac or VM has not been independently validated, so this remains
an early prerelease rather than a broad compatibility claim.

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
| Build-time | Tauri CLI and crates | Build the macOS `.app` and DMG |
| Runtime required | macOS WebKit | Render the existing StudyHub UI in the Tauri window |
| Runtime bundled | PyInstaller one-folder backend | Python 3.13, SQLite, standard library, and `certifi` |
| Runtime bundled | Compiled Tauri shell and static assets | Existing UI and local KaTeX distribution |
| Runtime optional | Poppler `pdftotext` / `pdfinfo` | Searchable PDF extraction and page metadata |
| Runtime optional | LibreOffice | High-fidelity local PPT/DOC visual preview conversion |
| Runtime optional | OpenAI API access | Source-grounded AI explanations and opt-in file search |

`certifi` is a small Python runtime dependency for verified OpenAI HTTPS
requests. Core browsing, local search, notes, stars, and the clean empty
workspace do not need OpenAI.

End users of the packaged beta do not need Python, pip, a virtual
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
tested import plan; the current desktop package does not silently move or overwrite an
existing database, settings file, notes, indexes, or AI configuration.

## Runtime Profiles

The desktop shell resolves one runtime profile before it starts the backend.
That boundary supplies explicit runtime, data, database, cache, log, and config
paths and removes inherited state-path and provider variables from the child
process.

| Profile | Application identity | Persistence behavior |
| --- | --- | --- |
| Production | `StudyHub Local` / `io.studyhublocal.desktop` | Uses the existing application data and config locations without migration or renaming. Release and DMG builds use this profile. |
| Development | `StudyHub Dev` / `io.studyhublocal.desktop.dev` | Uses separate OS data/config locations, a separate database/cache/log tree, and separate WebView data. `npm run desktop:dev` selects this profile. |
| Demo/Test | `StudyHub Demo` / `io.studyhublocal.desktop.demo`, or an explicit temporary test root | Uses only its isolated runtime. It does not inherit an external StudyLibrary, OpenAI key, or vector-store identifier, and native external-file selection is disabled. |

Development and Demo/Test never fall back to Production persistence. The
existing `STUDYHUB_DESKTOP_TEST_ROOT` hook remains available to acceptance tests
and always selects Demo/Test semantics. Reset operates only on the database and
cache resolved for the active profile.

Production compatibility is deliberate: the production identifier and its OS
locations are unchanged, so this work does not move, copy, rename, or reset an
existing database, settings file, StudyLibrary selection, notes, or study state.
Direct `python3 server.py` behavior remains available as the explicit source
workflow and is not silently migrated into a desktop profile.

Frontend storage is scoped with the desktop identity. Development and Demo/Test
use separate WebView data directories, covering UI preferences and workspace
references stored in `localStorage`. The frontend currently uses no
`sessionStorage`, IndexedDB, or cookie-based persistence. Browser storage is not
an authority for backend paths: the shell supplies those paths explicitly.

The desktop package uses Tauri [resource bundling](https://v2.tauri.app/develop/resources/)
for the complete PyInstaller one-folder directory, [native folder
dialogs](https://v2.tauri.app/plugin/dialog/), and [remote-origin capability
rules](https://v2.tauri.app/security/capabilities/). Rust launches the resource
executable as a separately monitored child process. Release builds have no
system-Python fallback; debug builds may still launch source Python.

## Packaging Decision

The selected desktop architecture is:

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
artifact privacy scan reproducible. PyInstaller is pinned and `certifi` has a
minimum version constraint through the requirements files.

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
Rust compiler paths and scans every file in the `.app`. Test fixtures are
injected from `tests/fixtures/` during acceptance and are not bundled. The
artifact must contain no developer home path, secret, runtime database,
academic document, or test course code. Build output is ignored and is not
committed.

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

## Native CredentialStore Foundation

The desktop shell now includes a narrow Rust `CredentialStore` foundation for
future optional integrations. The adopted storage dependency is `keyring`
4.2.0, licensed MIT OR Apache-2.0. Its native backends are macOS Keychain,
Windows Credential Manager, and Linux Secret Service through the crate's
platform support. Automated tests use fake and Demo/Test-denied stores only;
they do not inspect or mutate the user's real Keychain.

Implemented behavior:

- typed internal credential slots with stable profile namespaces
- store/replace, configured check, trusted native retrieval, and delete/revoke
- sanitized failure categories for missing, backend unavailable, access
  denied, and operation failed
- Production namespace `io.studyhublocal.desktop.credentials`
- Development namespace `io.studyhublocal.desktop.dev.credentials`
- Demo/Test denial that never touches real native credential storage

Trust boundary:

- raw credential retrieval is not exposed as a Tauri command
- no localhost HTTP, MCP, diagnostics, browser storage, SQLite, logs, or
  frontend bundle can return raw credentials
- a reviewed internal credential-handoff broker now models typed,
  child-authorized, restart-invalidated access for backend work
- live Unix/macOS private parent/backend credential transport is implemented
  through an inherited anonymous Unix stream and an internal Python
  `CredentialClient`

## Secure Credential Handoff Boundary

StudyHub now has an internal Rust handoff boundary for future trusted backend
use of stored credentials. It is intentionally narrower than a general secret
API:

- only typed StudyHub credential slots are addressable
- an explicitly authorized backend child identity is required
- backend restart creates a new authorization and invalidates stale handles
- malformed or unsupported requests are rejected before credential-store access
- failures are categorized without raw OS error or secret material
- WebView commands, localhost HTTP routes, MCP tools, diagnostics, logs,
  SQLite, and browser storage still cannot retrieve raw credentials

This boundary is covered with fake credential stores and synthetic child
participants. It is not an OAuth flow, account-management UI, Canvas password
flow, content-sync engine, or Canvas file downloader.

The desktop shell also implements the first live private parent/backend
credential transport on Unix/macOS. For each backend launch, the Tauri parent
creates an anonymous bidirectional Unix stream pair, keeps one endpoint, and
passes only the other endpoint to the exact spawned backend child as an
inherited file descriptor. The environment contains only non-secret bootstrap
metadata: the descriptor number, protocol version, and transport kind. No
credential, bearer capability, session secret, socket path, command-line
argument, localhost route, or temporary file is used for the handoff.

The parent creates a fresh OS-random session authority for every backend
process and validates a minimal versioned protocol before satisfying the only
implemented operation: `canvas_default` credential use for the trusted backend
action. Backend restart or retry creates a new channel and session; stale
sessions fail closed, and PID reuse alone cannot authorize a request. The
Python backend has an internal `CredentialClient` for this private channel, but
it is not an HTTP handler and does not expose credentials to WebView code,
MCP, diagnostics, SQLite, logs, or browser storage.

The first Canvas use of this boundary is implemented for authenticated
current-user validation and read-only course discovery. The stored value is a
versioned Canvas connection record containing a normalized HTTPS Canvas origin
and access token. Request input cannot override that bound origin, pagination
and redirects are same-origin guarded, and discovery results remain metadata
only rather than StudyHub academic-domain rows.

Current platform scope: the live transport is implemented for Unix/macOS.
Windows remains not implemented for this boundary until a separately reviewed
safe equivalent is selected.

Unsigned/ad-hoc package limitation: real macOS Keychain behavior across app
renames, executable changes, updates, and user approval prompts has not been
claimed by CI. A Development-only ignored smoke test exists for manual
validation of the Development namespace:

```bash
STUDYHUB_RUN_DEV_KEYCHAIN_SMOKE=1 cargo test --manifest-path src-tauri/Cargo.toml development_keychain_smoke_test_opt_in -- --ignored
```

That test must not be run against Production credentials and is not part of
normal CI.

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

The CredentialStore foundation does not automatically protect or migrate
existing OpenAI configuration. Existing OpenAI API keys and vector-store IDs
remain in the current environment/settings workflow for compatibility. The
desktop package does not copy a maintainer key and does not fall back to
plaintext key storage. AI being unconfigured is a valid state and does not
block local features.

Production and Development OpenAI settings remain in their own configured
settings environment. Demo/Test ignores inherited or configured OpenAI keys and
provider identifiers. Diagnostics expose only the profile name, never paths,
provider identifiers, or credential values.

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
- Synthetic environment-isolation acceptance creates three temporary roots,
  proves database/config/cache/log separation and reset scoping, and verifies
  that no real OS application directory or StudyLibrary is inspected.

## Experimental Or Not Implemented

1. A truly clean physical Mac or VM has not been tested; the current result is
   based on packaged and isolated synthetic acceptance rather than independent
   hardware coverage.
2. The public build is Apple Silicon only, unsigned, and not notarized. A DMG
   prerelease exists, but Developer ID signing and Apple notarization are not on
   `main`.
3. Canvas OAuth, institution Developer Key provisioning, account management,
   Canvas content synchronization, file/material download, Windows credential
   transport, and OpenAI key migration are not implemented.
4. Poppler and LibreOffice are not bundled; their missing states are graceful.
5. Installed-tool detection from a separate Finder-launched clean Mac remains
   to be confirmed.
6. Human VoiceOver testing remains incomplete.

The future update design must preserve the selected StudyLibrary, notes,
settings, stars, indexes, local database, and AI configuration. Auto-update is
not implemented in the current desktop package.

Developer ID signing, notarization, auto-update, Windows packaging, App Store
work, telemetry, and SaaS infrastructure are not implemented on `main`.

The [Engineering Constitution](ENGINEERING_CONSTITUTION.md) governs future
infrastructure selection, domain boundaries, and migration claims.
