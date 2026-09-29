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
