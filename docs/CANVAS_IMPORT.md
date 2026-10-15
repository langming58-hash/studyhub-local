# Canvas Import

Status: **AUTHENTICATED CONNECTOR / COURSE SELECTION / FILE SYNC-PLAN PREVIEW
IMPLEMENTED; PRODUCTION AUTH ENROLLMENT AND CONTENT ACQUISITION NOT IMPLEMENTED**.

StudyHub Local does not currently include a public universal Canvas downloader
or automatic Canvas synchronization. The local folder and scanner work without
an LMS connection.

## Authority And Safety

- The official Canvas API is the API authority.
- Access only accounts and materials the user is authorized to access.
- Do not bypass login, MFA, DRM, course permissions, or access controls.
- Do not submit assignments, quizzes, or other coursework.
- Development-only manual Canvas access-token enrollment is implemented as a
  narrow native desktop boundary for local developer/integration testing.
  Production manual-token enrollment is not supported and fails closed.
- According to Canvas OAuth documentation, manual token generation is for
  developer testing before OAuth; StudyHub must not ask general end users to
  manually generate tokens as its production authentication flow. A
  multi-user/distributed StudyHub workflow requires compliant OAuth or an
  approved institution-authorized authentication mechanism.
- When a connection record is configured by an approved mechanism, the token is
  stored with its normalized HTTPS Canvas origin in the native CredentialStore
  and is never returned to frontend JavaScript, localhost HTTP, MCP,
  diagnostics, SQLite, logs, or browser storage.
- OAuth, institution Developer Key provisioning, Canvas passwords, account
  management, production connection UX, and token refresh flows are not
  implemented.
- The Python backend reuses the private Unix/macOS parent/backend credential
  transport for the single internal `canvas_default` connection record. Canvas
  API requests use the bound origin only; request input cannot override the
  bearer-token destination.
- Existing open-source Canvas clients may be audited for endpoint coverage,
  authentication strategy, discovery, pagination, and workflow behavior.
- Incompatible, restrictive, non-commercial, or unlicensed implementations
  remain reference-only and must not be copied.

## Target Connector Boundary

```text
Canvas API
  -> CanvasConnector
  -> remote changes
  -> SyncPlan
  -> academic normalization
  -> ingestion/domain services
  -> repositories
```

`CanvasConnector` owns provider-specific requests, pagination, rate limits,
remote identifiers, and remote revision discovery. It must not write StudyHub
domain tables directly.

StudyHub owns SyncPlan, provenance, change detection, user overlays,
normalization, conflict behavior, and ingestion.

The generic local ingestion boundary prerequisite is implemented for current
filesystem scanner/import inputs. Canvas authenticated identity validation,
current-user course discovery, persistent CourseOffering remote identity,
user-owned CourseOffering selection, read-only selected-offering file metadata
discovery, and deterministic file SyncPlan preview are implemented. Canvas
content acquisition, SyncPlan apply, Canvas-specific content provenance
mapping, local Course/CourseOffering mapping, and Canvas content ingestion
remain not implemented.

## Implemented Discovery Boundary

Implemented Canvas calls are limited to:

- `GET /api/v1/users/self` for current-user validation.
- `GET /api/v1/courses` for current-user course discovery.
- `GET /api/v1/courses/:course_id/files` for selected CourseOffering file
  metadata discovery.

StudyHub requests `Accept: application/json+canvas-string-ids` and treats
Canvas remote IDs as opaque strings. The connector uses `Authorization: Bearer`
headers, never query-string or form access tokens. Pagination follows Canvas
`Link` headers by `rel="next"`, treats next URLs as opaque, rejects
cross-origin next links, and enforces page/item limits.

Discovery results can be used to persist last-known Canvas CourseOffering
metadata and user-owned selection state. The browser submits only opaque remote
course IDs and selection intent; StudyHub re-reads authoritative metadata from
`CanvasConnector` before persisting anything. This never writes to Canvas and
does not write into `courses`, `terms`, `sources`, `materials`,
`material_versions`, `files`, or `file_versions`.

Current selection APIs:

- `POST /api/canvas/course-selection/preview`: validates requested remote IDs
  against current discovery and returns a non-mutating plan.
- `POST /api/canvas/course-selection/apply`: revalidates current discovery,
  checks the preview revision, and transactionally updates local selected state.
- `GET /api/canvas/selected-courses`: lists locally remembered selected
  offerings with last-known metadata. This is offline remembered state, not a
  fresh Canvas sync.

Current file-planning API:

- `POST /api/canvas/sync-preview`: accepts only optional StudyHub
  CourseOffering stable IDs, verifies each offering is selected for the bound
  Canvas authority, discovers file metadata, and returns a deterministic plan.

The normalized manifest retains opaque string file/course IDs, bounded names,
content type, byte size, Canvas timestamps, and lock/visibility state. Canvas
`url`, `preview_url`, and `thumbnail_url` values are ephemeral and are not
persisted, logged, fingerprinted, or returned. The connector follows official
Canvas pagination semantics: case-insensitive `Link` headers, opaque
`rel="next"` URLs, same-origin enforcement, loop detection, and page/item/body
limits. Canvas 401, 403, 429, and server failures remain sanitized categories.

The additive `canvas_file_sync_baselines` table represents metadata from the
last successfully applied future content sync. It is not a record of the most
recent preview. Preview never writes this table. Its internal write helper is
not an HTTP endpoint, uses a nested savepoint, and leaves commit or rollback to
the caller. No current user workflow marks a baseline as applied.

Plan classifications are:

- `ADD`: present remotely with no last-applied baseline.
- `POTENTIAL_CONTENT_CHANGE`: stable remote identity with changed size or
  remote update/modified timestamp signals. This is not a content hash claim.
- `METADATA_CHANGE`: safe metadata changed without a content-change signal.
- `UNCHANGED`: normalized current metadata matches the baseline.
- `MISSING_FROM_REMOTE_DISCOVERY`: advisory ambiguity only; it never deletes a
  baseline or local academic data and does not claim permanent remote deletion.

Stable remote file identity is scoped by provider, Canvas authority,
CourseOffering, and opaque remote file ID. Deterministic baseline, manifest,
and plan revisions use canonical sorting and safe normalized metadata only;
they exclude credentials, generated timestamps, random values, database row
IDs, and temporary URLs.

## Sync Is Not Ingestion

Sync discovers remote state and produces an observable plan. Ingestion turns
acquired content into StudyHub content:

```text
Acquire -> Identify -> Persist Blob -> Extract -> Normalize
        -> Resolve -> Store -> Index -> Link
```

Remote authoritative fields remain separate from user-owned classifications,
notes, target dates, and review state. A Canvas refresh must never silently
overwrite user intent.

## Change Safety

Destructive, ambiguous, conflicting, identity-changing, or broad-impact
changes should use:

```text
Preview -> Explain -> Apply -> Undo
```

A plan should state what will be added or updated, which user overlays are
preserved, what content crosses an external boundary, and how a failed or
partial sync recovers. Safe idempotent additions and refreshes may execute
automatically according to the user's sync policy, while remaining observable,
auditable, and recoverable.

## Implementation Gate

Before Canvas content acquisition or sync apply:

1. audit official API capabilities and current authentication requirements
2. define account selection UX beyond the single default connection
3. define Source, Blob, Material, MaterialVersion, and CourseOffering mapping
4. define stale-plan, apply, recovery, and conflict semantics
5. select or reject a background-job foundation through a separate decision
6. add synthetic connector, pagination, retry, provenance, and migration tests
7. verify no real Canvas data, credentials, or academic content enters public
   fixtures, logs, screenshots, or Git history

Canvas file/material download, Source/Blob/Material creation, SyncPlan apply,
assignments/modules/pages sync, local Course/Term mapping, remote deletion
semantics, background sync, and conflict handling are not implemented by the
file-manifest planning foundation.
