# Canvas Import

Status: **TARGET BOUNDARY / NOT IMPLEMENTED**.

StudyHub Local does not currently include a public universal Canvas downloader
or automatic Canvas synchronization. The local folder and scanner work without
an LMS connection.

## Authority And Safety

- The official Canvas API is the API authority.
- Access only accounts and materials the user is authorized to access.
- Do not bypass login, MFA, DRM, course permissions, or access controls.
- Do not submit assignments, quizzes, or other coursework.
- Authentication and credential storage require an explicit security design.
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

Before any connector implementation:

1. audit official API capabilities and current authentication requirements
2. define credential storage and revocation
3. define Source, Blob, Material, MaterialVersion, and CourseOffering mapping
4. define SyncPlan and conflict semantics
5. select or reject a background-job foundation through a separate decision
6. add synthetic connector, pagination, retry, provenance, and migration tests
7. verify no real Canvas data, credentials, or academic content enters public
   fixtures, logs, screenshots, or Git history

No Canvas implementation is authorized by this Phase 0 documentation change.
