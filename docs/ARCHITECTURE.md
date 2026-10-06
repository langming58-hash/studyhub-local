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
engine or Canvas connector.

Stable IDs and additive metadata support term/course/material management, but
the local scanner still performs filesystem discovery, course/week inference,
and missing-file reconciliation. Extraction and indexing implementations remain
the current Python backend implementations behind that boundary. This is
current truth, not the final module boundary.

Not implemented yet: Canvas/remote connectors, SourceAnchor, Evidence,
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
  Its internal handoff broker models typed child-authorized access for future
  backend work and invalidates stale child authorizations after restart, but no
  live Python credential handoff or Canvas connector is implemented.
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
