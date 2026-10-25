# Architecture

Status: **AUTHORITATIVE CURRENT/TARGET MAP**.

This document separates observable architecture on `main` from agreed target
direction. Engineering policy, reuse rules, and donor decisions live in the
[Engineering Constitution](ENGINEERING_CONSTITUTION.md).

## CURRENT: Runtime Architecture

StudyHub Local is a local-first study library with a localhost web application
and a packaged Tauri desktop shell.

```text
User-controlled StudyLibrary
  -> filesystem scanner and local import services
  -> SQLite metadata, versions, chunks, questions, notes, and study state
  -> local search, preview, study, AI, CLI, and read-only MCP surfaces
  -> optional OpenAI Responses API and vector store
```

With no configured folder, StudyHub creates an empty managed workspace. It does
not seed sample courses or scan unrelated user directories.

### Current Components

- `server.py`: localhost HTTP server, SQLite schema, scanner/import services,
  extraction, preview routing, search, study state, optional OpenAI sync and
  requests, and read-only MCP.
- `static/`: plain HTML, CSS, and JavaScript frontend served by the backend.
- `src-tauri/`: desktop process lifecycle, native folder selection, narrow
  capabilities, packaged-resource wiring, and the internal native-only
  CredentialStore foundation.
- `desktop-shell/`: startup/failure surface used by the desktop shell.
- `tests/fixtures/`: synthetic acceptance inputs; never production resources.
- `data/`, `cache/`, and `logs/`: local runtime state, ignored by Git.

The packaged architecture and its current distribution limits are documented
in [Desktop Architecture](DESKTOP_ARCHITECTURE.md).

### Current Data Model

The current SQLite model includes terms, courses, weeks/modules, files, file
versions, document chunks, questions, solutions, notes, stars, attempts, wrong
questions, bookmarks, study sessions, AI conversations/messages, sync events,
AI index state, and app settings.

StudyHub is now in an additive Strangler transition for the academic-content
domain. The current product still uses legacy `files` and `file_versions` as
the operational compatibility state for scanner/import/API/UI reads. Alongside
that compatibility state, Phase 1 has implemented a persisted domain
foundation:

- `schema_migrations` for ordered, recorded schema changes.
- Source persistence for structured local origin/provenance projection.
- Blob persistence for byte/content identity.
- Material persistence for the stable academic object.
- MaterialVersion persistence for content/version history.
- Compatibility reconciliation from legacy `files` and `file_versions`.

This foundation is additive. Current scanner/import/API/UI reads continue to
use the legacy tables while the new domain tables are kept coherent at existing
boundaries.

Phase 2 has implemented a local ingestion boundary for current filesystem
inputs. The local scanner, manual file import, and course-folder import now
feed accepted local candidates through the same internal ingestion helper for
byte identity, compatibility-row persistence, extraction, local indexing,
question/solution extraction, and MaterialVersion/domain projection
linking for the ingested candidate. Callers still own transaction commit
policy, batch/full reconciliation, filesystem discovery, and missing-file
reconciliation. This is a local-input boundary only; it is not a remote sync
engine.

Stable IDs and additive metadata support term/course/material management, but
the local scanner still performs filesystem discovery, course/week inference,
and missing-file reconciliation. Extraction and indexing implementations remain
the current Python backend implementations behind that boundary. This is
current truth, not the final module boundary.

Phase 2 also implements the first narrow Canvas connector foundation:
origin-bound connection records, Development-only manual token enrollment for
local testing, authenticated current-user validation, and read-only current-user
course discovery. Production end-user Canvas authentication is not implemented.
Discovery results can now be persisted into an additive Canvas CourseOffering
foundation: stable origin-bound remote identity, remote-authoritative
last-known course metadata, and separate user-owned selected/deselected state.
Selection preview/apply is local-only and does not mutate Canvas, download
content, create local Course/Term rows, or ingest materials.

For selected Canvas CourseOfferings, the connector can now list course-file
metadata through the read-only Canvas Files API and normalize it into a remote
manifest. A deterministic, non-mutating SyncPlan preview compares that manifest
with a persisted last-applied metadata baseline and classifies files as `ADD`,
`POTENTIAL_CONTENT_CHANGE`, `METADATA_CHANGE`, `UNCHANGED`, or advisory
`MISSING_FROM_REMOTE_DISCOVERY`. Preview does not update the baseline. Temporary
download, preview, and thumbnail URLs are discarded before normalization and
never enter SQLite, plan output, stable identity, or revision fingerprints.

Explicit Canvas file apply is now implemented for selected CourseOfferings.
Apply recomputes the plan, rejects stale revisions, re-reads each selected File
through the course-scoped API, streams supported bytes into profile-isolated
retained storage without forwarding the Canvas bearer to download origins, and
then uses the existing ingestion core. Canvas Sources are
`REMOTE_AUTHORITATIVE`; local scanner Sources remain `USER_OWNED`. A focused
`course_offering_local_bindings` projection supplies the current legacy UI with
a deterministic Canvas-managed Course and `Canvas Files` learning unit without
claiming that target-domain Course and CourseOffering are the same entity.
Baseline state advances only after successful ingestion.
The normal file lookup, preview, and read-only MCP resolver accept a retained
Canvas path only when the row is explicitly `canvas_remote`, the requested path
exactly matches its persisted locator, and that locator remains inside the
profile-owned Canvas remote-content root. Explicit sync apply is serialized
through one backend-process lock covering fresh planning through caller commit;
this is not a cross-process locking guarantee. User-edited compatibility Course
presentation fields remain protected by the existing `metadata_locked` overlay
while CourseOffering metadata continues to refresh independently.

Successfully committed Canvas file transitions now also write an immutable,
structured `academic_change_events` fact inside the same caller-owned apply
transaction as ingestion, MaterialVersion advancement, and baseline
advancement. Event kind reflects the actual committed result: first
acquisition, a new byte version, or metadata-only advancement. Unchanged and
advisory missing items create no academic event. Deterministic event identity
chains each remote item's predecessor event to the resulting revision, so exact
retries are idempotent while repeated state cycles remain distinct historical
transitions. A MaterialVersion identifies a content state; an
AcademicChangeEvent identifies a committed transition. Separate
`academic_change_event_state` rows
hold explicit user-owned seen state; reads never create or update that state.
The offline `/api/whats-new` read surface returns bounded safe labels, stable
references, accurate scoped counts, and a local file ID where available. Home
shows a compact unseen summary, while the recoverable `#/whats-new` Home
sub-workspace provides explicit Unread/All views and local material navigation.
Opening or rendering an event never acknowledges it. `/api/whats-new/seen`
accepts only explicitly selected bounded opaque event IDs and remains under the
normal same-origin and CSRF mutation boundary. The six-item primary navigation
is unchanged. Legacy `sync_events` remains the operational local-scan log and
is not overloaded or migrated.

Phase 2 also implements the `studyhub-backup-v1` export boundary for durable
user-owned state. It projects allowlisted fields from one coherent SQLite read
snapshot, reached through a dedicated read-only/query-only connection for HTTP
and CLI entry points, into canonical JSON members, validates the bounded ZIP,
and atomically publishes it under the active profile's `exports/` directory. Original and
Canvas-retained files, remote/provider facts, derived content, credentials,
absolute paths, and AI history without durable portable identities are
excluded. The browser route accepts no destination
path and returns safe metadata only.

The desktop restore boundary uses a native single-file picker to copy
an archive into the active profile's private `restore-inbox`; browser and HTTP
surfaces receive only an opaque staging ID. Python reuses the authoritative
archive validator over one bounded immutable archive snapshot, so bytes planned
are exactly the bytes validated. It reads the current database through the
query-only backup connection, and constructs an ephemeral deterministic
`RestorePlan`. Preview
does not initialize, migrate, scan, ingest, relink, write user state, or contact
network/credential services. It explains safe matches, candidate user-state
updates, conflicts, missing-source relinks, and unsupported v1 omissions.
Its revision privately binds relevant current semantic values and multiplicity,
while excluding ignored timestamps, row IDs, runtime roots, and local paths.
Explicit relink preview now extends that read-only boundary. A native single-
file picker creates a profile-private EPHEMERAL descriptor and exposes only an
opaque capability to the WebView. Python verifies capability ownership,
recomputes the current canonical plan, rejects stale revisions, and compares
the candidate's streamed SHA-256 and size with the validated backup source.
`RELINK_READY` is a revision-bound preview state: preview never changes a
Source, Material, file row, original byte, or index, but it can authorize one
explicit `RELINK` decision for the exact current capability.
The base plan revision binds the immutable archive and relevant local-state
snapshot; each source candidate has its own revision against that base.
Previously ready opaque capabilities are revalidated and accumulated when the
user selects another source, so selection order does not invalidate unrelated
candidates or weaken stale-state protection.

Restore Apply reuses the canonical planner under a process-local apply lock and
a caller-owned SQLite `BEGIN IMMEDIATE` transaction. It requires the exact approved plan
revision and a complete set of narrow `KEEP_LOCAL`/`SKIP`/`RELINK` decisions,
applies only supported user-owned state, rechecks explicitly inspected source
candidates, and commits once. Exact relink reuses the canonical ingestion/domain
projection by reference and consumes its private capability only after commit.
The same transaction now records a private, versioned Restore operation only
when Apply makes at least one reversible durable mutation. Its closed ChangeSet
contains exact pre/post images for allowlisted user-owned, compatibility, and
canonical-domain rows plus safe relative identity for a Restore-created text
cache artifact. Derived FTS, chunks, questions, solutions, and course/week
counts are intentionally excluded from the inverse contract. A failed or no-op
Restore leaves no operation record. Failures roll back atomically.

This recorder is a foundation, not an Undo feature: there is no Undo Preview or
Undo Apply API yet. Automatic source search, backup-wins overwrite decisions,
post-commit Undo, scheduled/cloud backup, and original-file backup are not
implemented. The canonical contract is
[Backup Export Format](BACKUP_FORMAT.md).

Backup archives remain untrusted after ZIP and checksum verification. Preview
and Apply share one semantic v1 validator for exact field types, bounds, enums,
hashes, controls, and safe relative paths. Material-overlay Apply performs a
narrow in-transaction synchronization of the existing `files` row, existing
Phase-1 `materials` projection, and derived local search metadata. Explicit
relink may create the single missing canonical Source/Blob/MaterialVersion graph
or update the existing user-owned Source locator while preserving stable
Material identity. It does not run global reconciliation or migrate schema.

Not implemented yet: production Canvas OAuth, remote Term mapping, remote
deletion semantics, notifications, automatic/background sync, SourceAnchor, Evidence,
DerivedArtifact/ProcessingRecipe, Entity Resolution, the Assessments target
model, background jobs, and target-domain UI reads.

### Current Source And Preview Rules

For the CURRENT local-library workflow, a user-owned original under the
configured StudyLibrary remains user-controlled and authoritative for that
source. SQLite, extracted text, previews, full-text indexes, and vector
resources are retrieval or derived layers and can be rebuilt.

Visual preview and readable extraction are separate:

- PDFs and images can be previewed directly.
- text, code, CSV, notebooks, and active web formats use escaped readable text.
- PowerPoint and Word may use local LibreOffice conversion to cached PDF.
- cached derivatives never replace the original file.

See [Preview Matrix](design/PREVIEW_MATRIX.md) for current format behavior.

### Current Trust Boundaries

- HTTP binds to loopback only by default.
- Filesystem operations are contained under the configured StudyLibrary.
- Academic files are untrusted input; active web content is not rendered as a
  same-origin document.
- Mutating browser routes use Host, exact-origin, and CSRF protections.
- MCP is read-only and exposes safe IDs and academic metadata rather than local
  absolute paths or provider IDs.
- CredentialStore is an internal Rust boundary only. It uses typed slots,
  runtime-profile namespaces, native OS credential storage for Production and
  Development, and Demo/Test denial. It has no frontend, localhost HTTP, MCP,
  diagnostics, SQLite, browser-storage, or Tauri raw-secret retrieval surface.
  Its internal handoff broker provides typed child-authorized access for
  trusted backend work and invalidates stale child authorizations after restart. The
  desktop shell now provides a Unix/macOS private inherited stream transport to
  the exact spawned Python backend child for one narrow internal
  `canvas_default` credential operation. The current Canvas connector can use
  that boundary for read-only identity validation, course discovery, and
  selected-offering file discovery and explicit file acquisition when an
  approved mechanism has stored a valid connection record. Bearer credentials
  remain confined to same-origin Canvas API requests and are not attached to
  ephemeral file-download URLs or redirects.
- OpenAI is optional, server-side, and scoped to indexed source material.
- Practice questions come from indexed teacher-provided material only. The app
  must not invent practice questions.

Detailed handling is in [Privacy](PRIVACY.md) and [Security](../SECURITY.md).

## TARGET: Academic Domain

The target model continues the Strangler transition by moving behavior from
legacy compatibility tables toward explicit domain services and repositories:

```text
Institution
  -> Term / StudyPeriod
  -> Course
  -> CourseOffering
  -> Assessment / Material

Source -> Blob -> Material -> MaterialVersion
MaterialVersion -> SourceAnchor -> Evidence
Question / Concept -> ReviewItem / AcademicAction / AcademicEvent
DerivedArtifact -> rebuildable output with provenance
ChangeSet -> previewable and reversible domain change
```

Required distinctions:

- **Source** identifies origin, authority, and acquisition context.
- **Blob** identifies stored bytes independently of academic classification.
- **Material** is the stable academic object presented to the user.
- **MaterialVersion** records content evolution without erasing history.
- **Course** describes a reusable catalog identity; **CourseOffering** is a
  particular term/institution occurrence.
- Official remote deadlines remain separate from personal target dates.
- Remote state remains separate from user-owned overlays.
- Derived data remains separate from user-owned data.

Source, Blob, Material, and MaterialVersion now have an implemented additive
persistence foundation. The remaining target concepts in this section are not
implemented by that foundation unless explicitly listed in the current data
model above.

## TARGET: Ownership Model

Apply these semantics at the datum, field, or artifact layer rather than
requiring every normalized entity to have exactly one ownership class. For
example, an Assessment may combine remote-authoritative title and due-date
fields with a user-owned personal target, notes, and review state.

| Ownership | Meaning | Examples |
| --- | --- | --- |
| `REMOTE_AUTHORITATIVE` | Mirrored provider facts | LMS title, official due date, remote revision |
| `USER_OWNED` | User intent and durable personal work | notes, classifications, personal targets, review state |
| `DERIVED` | Rebuildable computation | extracted text, previews, embeddings, inferred links |
| `EPHEMERAL` | Disposable processing state | temporary downloads, conversion workspaces, retry state |

Remote sync never silently overwrites user intent. Derived data never becomes
the sole copy of user-owned information. Ephemeral data is always safe to
delete. Retrieved remote content may be retained as local Blob or
MaterialVersion data with provenance, without changing the authority of the
remote provider facts or files.

## TARGET: Sync And Ingestion

Sync and ingestion are separate boundaries:

```text
Remote system
  -> connector
  -> remote changes
  -> SyncPlan
  -> domain normalization
  -> ingestion/domain services
  -> repositories
```

Connectors discover provider state and produce remote changes. They must not
write domain tables directly.

Ingestion turns acquired content into StudyHub content:

```text
Acquire -> Identify -> Persist Blob -> Extract -> Normalize
        -> Resolve -> Store -> Index -> Link
```

Every stage must retain provenance and support retry/rebuild without damaging
the authoritative source, retained local copy, or user overlay. Canvas-specific
target boundaries are in [Canvas Import](CANVAS_IMPORT.md).

The current local ingestion helper is an implemented prerequisite for this
target direction. It handles current local scanner/import candidates only.
`SyncPlan`, remote connector polling, remote deletion handling, SourceAnchor,
Evidence, and background-job orchestration remain not implemented.

## TARGET: Adapter Boundaries

Commodity implementations stay behind narrow StudyHub-owned interfaces. Likely
boundaries include `PdfRenderer`, `ReviewScheduler`,
`TranscriptionProvider`, `EmbeddingProvider`, `CanvasConnector`,
`CredentialStore`, and `DownloadManager`.

These interface names are architectural direction, not current APIs. Adopt
only when a real implementation needs the boundary. Donor types must not leak
across the product. See the constitution's
[Donor Registry](ENGINEERING_CONSTITUTION.md#donor-registry).

## Migration Direction

Evolution from CURRENT to TARGET is additive and evidence-driven:

1. Protect current behavior with synthetic migration and acceptance tests.
2. Introduce domain types and repositories at existing boundaries before
   changing storage.
3. Preserve stable IDs, provenance, notes, study state, and user overlays.
4. Separate Source/Blob/Material concerns without moving or rewriting original
   files.
5. Extract sync and ingestion stages incrementally.
6. Treat derived data as rebuildable and migration-safe.
7. Use Preview -> Explain -> Apply -> Undo for ambiguous or destructive
   changes.

Do not combine this migration with visual redesign, infrastructure replacement,
or external connector implementation.

## Architecture Invariants

- User-owned imported originals remain user-controlled and authoritative for
  those user-owned sources.
- Remote provider facts and files remain remote-authoritative; retained local
  Blob or MaterialVersion copies preserve provenance and offline utility.
- Derived data never replaces either authoritative source.
- No normal metadata action renames, overwrites, moves, or deletes user-owned
  originals.
- User intent survives rescans, remote sync, reclassification, and rebuilds.
- Runtime state remains outside the public repository and packaged resources.
- Real academic content never appears in public fixtures, screenshots, logs,
  documentation, or Git history.
- Local features work without OpenAI.
- External providers cross explicit trust boundaries.
- TARGET terminology is never presented as IMPLEMENTED without code and tests.
