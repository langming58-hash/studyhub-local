# Privacy And Data Ownership

Status: **AUTHORITATIVE PRIVACY MODEL**.

StudyHub Local is designed for private, local-first study workflows. This
document describes current handling and the ownership invariants future
architecture must preserve. Security controls are documented separately in
[SECURITY.md](../SECURITY.md).

## CURRENT: Stays Local By Default

- original course files
- SQLite metadata and local application state
- extracted text and preview cache
- notes, stars, attempts, review state, and wrong-question records
- local logs

These paths stay outside public Git and must not be uploaded to issues or pull
requests. User-owned imported originals remain in a user-controlled
StudyLibrary and are authoritative for those user-owned sources.

## CURRENT: Optional External Processing

OpenAI integration is optional and server-side only. When enabled, selected
extracted content and safe academic metadata may be sent to OpenAI and uploaded
to a vector store for retrieval. Users are responsible for confirming that
they may process the selected material with a cloud provider.

For a user-imported local source, the local original remains authoritative.
Provider resources, embeddings, and vector indexes used for optional AI are
derived retrieval layers. Local filesystem paths, cache paths, database paths,
API keys, provider file IDs, and vector-store IDs must not be returned through
public-facing responses.

StudyHub includes no analytics or telemetry by default.

## Runtime Environment Isolation

Production, Development, and Demo/Test persistence are separate trust domains.
Production retains the existing `io.studyhublocal.desktop` application identity
and existing OS data/config locations. Development and Demo/Test use distinct
application identities, databases, settings, caches, logs, managed workspaces,
and WebView storage. They do not fall back to Production state.

The desktop shell removes inherited database, runtime, StudyLibrary, and OpenAI
provider variables before launching the backend, then supplies explicit paths
for the selected profile. Demo/Test additionally rejects external library/file
selection and ignores inherited or configured OpenAI keys and provider IDs.
Tests use temporary synthetic roots and reset only their active temporary
profile. No profile selection moves or rewrites original academic files.

The native CredentialStore foundation follows the same profile boundary.
Production uses the `io.studyhublocal.desktop.credentials` namespace,
Development uses `io.studyhublocal.desktop.dev.credentials`, and Demo/Test
uses an explicit denied backend that never touches real OS credential storage.
No fallback, migration, or cross-profile lookup is allowed.

## Ownership Classes

Future domain work applies these semantics at the datum, field, or artifact
layer. A normalized entity may contain more than one ownership class. For
example, an Assessment may combine remote-authoritative fields with a
user-owned overlay.

### `REMOTE_AUTHORITATIVE`

Facts mirrored from an external authority, such as an LMS title, official due
date, source URL, or remote revision. Synchronization may refresh these facts,
but it must not overwrite a user-owned overlay.

### `USER_OWNED`

Durable user intent and work, including notes, annotations, personal target
dates, classifications, review state, and local conversation history. Derived
or remote data must never become the only copy of this information.

### `DERIVED`

Rebuildable results such as extracted text, visual previews, thumbnails,
indexes, embeddings, inferred links, and search ranking state. Deleting derived
data may reduce capability temporarily but must not destroy original content or
user intent.

### `EPHEMERAL`

Temporary downloads, conversion workspaces, request state, and retry artifacts.
Ephemeral data must always be safe to delete.

This classification is a TARGET architecture rule. Current tables do not all
carry an explicit ownership field.

## Trust Boundaries

Local trusted data includes:

- runtime database
- academic files
- notes and annotations
- review and progress state
- local indexes
- credentials and credential handles

External boundaries include LMS providers, cloud AI, Zotero or other cloud
providers, and future APIs. Crossing a boundary requires explicit user intent,
minimal data transfer, provenance, clear failure behavior, and a documented
deletion/revocation path where applicable.

Secrets should be represented by handles and platform credential storage where
practical. They must never appear in frontend bundles, public logs, fixtures,
screenshots, issues, commits, SQLite exports, or diagnostic payloads.
The implemented native CredentialStore boundary is trusted-native-only: it can
store, replace, check, retrieve for internal native use, and revoke typed
credential slots. No frontend, localhost HTTP, MCP, diagnostics, browser
storage, SQLite, or public API may retrieve raw credentials.
The internal credential-handoff broker keeps that boundary narrow for future
backend work: only typed slots and an authorized child identity can reach the
native retrieval path, stale child authorizations are invalidated on restart,
and malformed or unsupported requests fail before credential-store access. The
desktop shell now connects the exact spawned Python backend child over a
private inherited Unix stream for the single internal `canvas_default`
credential operation. Credentials still do not pass through environment
variables, command-line arguments, disk files, SQLite, logs, diagnostics,
WebView JavaScript, localhost HTTP, or MCP.

The implemented Canvas discovery boundary stores a versioned connection record
behind that slot: normalized HTTPS Canvas origin plus access token. The
Development profile has a narrow manual-token configuration command for local
testing, while Production rejects manual-token enrollment before touching
credential storage. Status/remove commands remain non-secret management
operations. No frontend, HTTP route, MCP tool, diagnostic, log, or SQLite row
may read the raw token. The backend uses the record only for read-only identity
validation and current-user course discovery against the bound origin.

The implemented Canvas CourseOffering selection foundation persists only a
non-secret authority ID derived from the normalized Canvas origin, remote
course IDs as strings, last-known remote course metadata, and user-owned
selection state. It does not store the Canvas bearer token, the full credential
record, credential-session authority, local filesystem paths, or Canvas content
in SQLite. User selection is separate from remote-authoritative metadata and is
not cleared by a metadata refresh or one missing discovery result.

The Canvas file sync foundation persists a last-applied baseline of bounded,
non-secret file metadata for selected CourseOfferings. Current preview
manifests are not persisted. Signed download URLs, preview URLs, thumbnail
URLs, verifiers, and access tokens remain excluded from SQLite, public
responses, fingerprints, diagnostics, logs, and MCP.

After explicit apply, validated Canvas bytes may be retained under the
profile-isolated StudyHub data root for offline use. Their Source remains
`REMOTE_AUTHORITATIVE`, records the opaque remote file identity and a non-secret
authority identifier, and keeps `remote_url` empty. Its local locator is private
runtime state and is never returned through public APIs or MCP. A file missing
from one discovery response remains advisory and does not delete retained
bytes, provenance, versions, baselines, or user selection.

Existing local preview/read and read-only MCP content resolution may read an
already acquired Canvas copy by safe file ID. The resolver accepts only the
exact persisted path of an explicit `canvas_remote` row inside the dedicated
profile-owned Canvas remote-content root. It does not grant general access to
the data directory, cache directory, Canvas credentials, signed URLs, or sync
controls.

Committed Canvas file changes may create a local academic journal fact with an
allowlisted event kind, bounded course/material label snapshots, stable
StudyHub references, changed-field names, and existing content/version hashes.
It never stores credentials, Canvas/download URLs, local paths, raw provider
payloads, extracted document text, or backend session capabilities. Explicit
seen state is stored separately as user-owned data and is never reset by a
provider refresh. The offline What's New API exposes only safe stable IDs,
labels, timestamps, seen status, and a navigation-safe local file ID when one
exists.

The implemented `studyhub-backup-v1` exporter applies ownership at the field
and artifact layer rather than dumping SQLite tables. It includes allowlisted
user-owned metadata and study history only. It excludes original and retained
academic bytes, remote-authoritative provider facts, derived text/indexes,
runtime databases, credentials, provider IDs, signed URLs, logs, and absolute
paths. Export never calls Canvas, OpenAI, or CredentialStore and never reads
another runtime profile. Recognizable secrets and local paths embedded in
portable user text are redacted as defense in depth. See
[Backup Export Format](BACKUP_FORMAT.md) for the exact v1 contract and known
omissions. AI history is explicitly excluded until it has honest durable
portable identities.

Restore preview is implemented as a read-only comparison boundary. Tauri
stages a user-selected backup copy under the active profile and gives the
frontend an opaque ID; the localhost API cannot accept an arbitrary path. The
staged copy is not statically served, exposed through MCP, logged, or bundled.
Preview revalidates the archive, reads the current database query-only, and may
hash only the explicitly referenced StudyLibrary-relative candidate needed for
comparison. It does not scan the library, contact Canvas/OpenAI/CredentialStore,
or mutate persistent user state. Safe preview responses omit absolute paths,
raw provider IDs, database IDs, and hidden technical hashes. Restore Apply uses
the same validated in-memory archive snapshot and canonical planner inside one
`BEGIN IMMEDIATE` transaction. It accepts no paths or replacement values,
applies only allowlisted user-owned state, rechecks explicit source candidates,
and makes no Canvas, OpenAI, or CredentialStore call. It never writes academic
source files. Automatic relinking, backup-wins replacement, and post-commit Undo
are not implemented.

Archive hashes prove integrity, not trust. The shared Preview/Apply validator
rejects semantically invalid field types, oversized text, unsafe controls,
invalid enums and hashes, absurd numeric metadata, and unsafe source-relative
paths before planning or database mutation. Restored material organization is
synchronized only into the existing compatibility/domain rows and existing
local search metadata; derived text is not imported from the archive, and no
source bytes or remote-authoritative facts are changed.

## Public Data Boundary

Public examples, fixtures, tests, screenshots, documentation, and demo assets
use synthetic data only. Never publish:

- real academic files or extracted content
- teacher questions or official solutions
- private answers, notes, or wrong-question records
- runtime databases, caches, previews, or logs
- local absolute paths, usernames, or personal email addresses
- API keys, cookies, OAuth/session data, credential files, or provider IDs

Production academic data must never enter Git history, even temporarily.

## Public Identity Boundary

Project documentation must not become a directory of the maintainer's personal
accounts unless that linkage is an intentional public professional choice.
Direct social-post URLs, private messages, login state, account eligibility,
verification details, and exact personal publication timestamps belong outside
the repository.

## Local Privacy Markers

The public privacy checker is generic and contains no user-specific names,
course codes, institution names, or local paths.

For machine-specific deny markers, copy `.privacy.example.json` to
`.privacy.local.json`. That local file is ignored by Git and must never be
committed.

## Invariants

- Remote synchronization never silently overwrites user intent.
- User-owned imported originals remain authoritative for their source.
- Remote provider facts/files remain remote-authoritative; StudyHub may retain
  local Blob or MaterialVersion copies with provenance for offline use.
- Previews, extraction, AI indexes, and other derived data never replace either
  authoritative source.
- User-owned data survives cache clearing and derived-data rebuilds.
- Ephemeral data is safely disposable.
- Normal metadata actions do not rename, move, overwrite, or delete user-owned
  originals.
- Optional cloud features remain explicit and local features remain useful
  without them.
- Every public PR passes the repository privacy checks with synthetic inputs.
- Development and Demo/Test never implicitly read Production persistence or
  credentials; release builds always resolve to Production.
