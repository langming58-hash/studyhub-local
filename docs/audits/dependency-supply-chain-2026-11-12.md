# Dependency and supply-chain audit — 2026-11-12

Status: **AUDIT EVIDENCE / PROPOSED REMEDIATION**, pending independent review.

Base main: b534bfec9efe3cd3c271285656d9b0d4c621a6c3.
Approved backup-warning PR #48 was squash-merged at this commit; its
[post-merge CI passed](https://github.com/langming58-hash/studyhub-local/actions/runs/43199203895).
This audit does not change the public beta.2 Release, DMG, checksum or tags.
Full human GUI backup save/restore/undo acceptance remains incomplete.

## Method and coverage

Only free tools and public advisory/registry endpoints were used. No billing
commitment, account upgrade or paid service was involved.

- npm 11.16.0: npm audit --package-lock-only --ignore-scripts --json;
  direct, transitive, development and optional dependencies in the npm lock.
- pip-audit 2.10.1, isolated with uv 0.12.3: the existing Python 3.13.15
  desktop environment (42 distributions), a fresh macOS arm64 Python 3.13
  desktop-requirements resolution (46), and a fresh Linux Python 3.12 runtime
  resolution (37). Resolved lists were audited without another dependency
  resolution using --no-deps --disable-pip. No package was skipped.
- cargo-audit 0.23.0: all 530 Cargo.lock packages, including other-platform
  and build dependencies. RustSec database commit:
  5fe96cf51fde2fd4e749d51ac8bdc59edbdce77b.
  No advisory was ignored. The official Apple Silicon tool archive was
  checked against its GitHub asset SHA-256:
  84101204e5b8013c9a8d7251766aebdef3e7fa1a3ef040cde44d65b48b8c4056.
- Cargo target and feature trees: cargo tree --locked --target
  aarch64-apple-darwin, normal dependencies and normal/build dependencies
  separately, plus the reverse Tokio feature tree.
- Both GitHub Actions workflows: action owners/references, composite-action
  metadata, token scope, checkout credentials, PR trust boundary, dependency
  installation, artifact verification and publication gates.

These are source/lockfile and local-environment checks, not a binary SBOM or
machine-code exploit proof for the immutable public DMG. Known-advisory scans
do not establish absence of unknown vulnerabilities or malicious packages.
The audit tools themselves were kept outside application dependencies.

## Results and exposure

Severity below is an **analyst triage estimate**, not an upstream CVSS score.
RustSec supplies no CVSS score for these entries. Package presence alone is
not treated as proof that an affected function is reachable.

| Finding at base | Severity / exploit conditions | Component and current macOS reachability | Proposed disposition |
| --- | --- | --- | --- |
| [RUSTSEC-2026-0385](https://rustsec.org/advisories/RUSTSEC-2026-0385.html), ctutils 0.4.2 | Medium, conditional timing/secret-dependent branching; requires a relevant cryptographic use and observable side channel | Transitive, cross-platform lockfile. Absent from the resolved macOS normal/build tree. No current packaged macOS path identified. | Patch to 0.4.4. |
| [RUSTSEC-2026-0486](https://rustsec.org/advisories/RUSTSEC-2026-0486.html), rustix 1.1.4 | High potential memory-safety impact; affected extended-attribute functions require a zero-length buffer. Upstream classifies this as an unsoundness notice. | Transitive, cross-platform lockfile. Absent from the macOS normal/build tree. No current packaged macOS path identified. | Patch to 1.1.6. |
| [RUSTSEC-2026-0386](https://rustsec.org/advisories/RUSTSEC-2026-0386.html), Tokio 1.53.1 | High potential memory corruption if unsafe file-descriptor lifetimes reach AsyncFd constructors; upstream unsoundness notice | Tauri runtime dependency. The current macOS feature tree enables bytes/default/fs/io-util/rt/rt-multi-thread/sync, not net. AsyncFd is behind the net/Unix gate; no current affected API path identified. | Patch to 1.53.3 without enabling features. |
| [RUSTSEC-2024-0429](https://rustsec.org/advisories/RUSTSEC-2024-0429.html), GLib 0.18.5 | High potential undefined behavior in VariantStrIter; upstream unsoundness notice | Transitive GTK/WebKit dependency in other target branches. Absent from the macOS normal/build tree. | Retain as an explicit warning. A GLib 0.20 migration is a separate upstream/platform change, not a compatible patch here. |
| [RUSTSEC-2024-0370](https://rustsec.org/advisories/RUSTSEC-2024-0370.html), proc-macro-error 1.0.4 | Informational maintenance risk, no demonstrated exploit in this audit | Build-time macro dependency on other platform branches; absent from the macOS normal/build tree | Track upstream; do not force a macro-library replacement. |
| [RUSTSEC-2026-0371](https://rustsec.org/advisories/RUSTSEC-2026-0371.html), zbus_names 4.3.4; [RUSTSEC-2026-0370](https://rustsec.org/advisories/RUSTSEC-2026-0370.html), zvariant 5.15.0; [RUSTSEC-2026-0372](https://rustsec.org/advisories/RUSTSEC-2026-0372.html), zvariant_derive 5.15.0 | Informational maintenance notices; standalone crates merged into a newer upstream major family | D-Bus/runtime and macro dependencies on other target branches; absent from the macOS normal/build tree | Track upstream; no automatic major migration. |
| Mutable Action tags/branch references | Medium supply-chain risk, conditional on upstream ref/account compromise; no compromise was established | Build and future release pipeline, not application runtime | Pin eight action sources to verified commits from their owning repositories, retaining current major lines. |
| Normal CI used npm install | Low reproducibility gap; lockfile can be reconciled instead of rejecting drift | CI/build only | Use npm ci. |
| Python transitives are not locked; certifi has a lower bound | Medium assurance/reproducibility gap, not a confirmed known vulnerability | Build resolution can change runtime converter/parser dependencies; existing and fresh resolutions differ | Follow-up: define and review platform/Python-specific hash-locked resolutions. No Python dependency or resolver migration is bundled into this patch. |

npm reported **zero known vulnerabilities** across 29 lockfile dependencies.
All three pip-audit scans reported **zero known vulnerabilities** and no skips.
This includes development/build packages as well as runtime dependencies.

Runtime distinctions: PDF.js, Prism and KaTeX assets and the selected macOS OCR
helper can ship in the app; the npm Tauri CLI is a development/build tool.
Python document parsers/converters are runtime functionality in the isolated
MarkItDown helper and process user-selected documents. PyInstaller and its
hooks are build tools. The installed-environment inventory is not claimed to
be the exact inventory of the already-published frozen binary.

## Lockfile and workflow review

All non-root npm package entries had integrity data and registry URLs.
All registry Cargo packages had checksums; no Git/path override or alternate
registry dependency was found. Python has no committed transitive lockfile.

Targeted Cargo updates change package versions only for ctutils, rustix and
Tokio. Cargo also normalizes five existing dependency references:
cssparser-macros to the already-locked syn 2, and dirs-sys, errno, tempfile and
winapi-util to their already-locked windows-sys versions. No additional
package version is upgraded or removed. The rustix patch has its generated
dependency-reference adjustment as well. These generated changes remain in
the reviewed lockfile; checks/tests/build now use --locked.

The repository's existing default workflow token permission was already read,
with PR review approval disabled. CI now makes contents: read explicit.
Checkout credential persistence is disabled in all three checkout steps;
public read-only Git fetches and the explicitly scoped publish token remain
available. Build jobs stay read-only and contents: write remains confined to
the tag-push publish job.

No pull_request_target or privileged workflow_run trigger was found. No PR
title/body expression is interpolated into shell code. Publication still
requires a tag push, successful build dependency, exact tag/main checks,
matching artifact metadata, checksum verification and refusal to overwrite an
existing Release. None of these checks is removed or bypassed.

Full-SHA pins follow [GitHub's security guidance](https://docs.github.com/en/actions/reference/security/secure-use).
The eight pinned action commits are resolved from actions/checkout v4,
actions/setup-python v5, actions/setup-node v4, astral-sh/setup-uv v6,
dtolnay/rust-toolchain stable, Swatinem/rust-cache v2, actions/upload-artifact
v4 and actions/download-artifact v4. Version comments remain beside each SHA.
An explicit stable toolchain input preserves the Rust installer behavior.
There is no automatic Action major upgrade.

## Validation and remaining limitations

After the three Rust patches, cargo-audit reports **zero vulnerabilities**,
with **five warnings remaining**: GLib unsoundness and the four maintenance
notices above. The ignore list remains empty. This is not a warning-free audit.

The offline supply-chain acceptance script runs in normal CI and checks
patched crate floors, immutable Action references, read-only CI permissions,
non-persisted checkout credentials, npm ci and locked Rust commands. Synthetic
downgrades, mutable refs, write permissions, unlocked npm installation and
persisted credentials are rejected. Existing application/privacy/release tests
are preserved. These guards are not a replacement for fresh advisory scans.

Python hash locking, exact Rust/uv/runner toolchain pins, Action bundled
JavaScript transitive auditing, a binary SBOM and a compiler/machine-code
reachability proof remain outside this focused patch. A Linux reverse-tree
attempt in offline mode lacked a cached crate; non-macOS findings are
classified conservatively from the full lockfile and exclusion from the
verified macOS tree, not a completed Linux build.

No finding here establishes full human GUI backup/restore/undo acceptance.
No paid scan was required, and no tool was skipped for a paid quota.
