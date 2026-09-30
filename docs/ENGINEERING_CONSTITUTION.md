# Engineering Constitution

Status: **AUTHORITATIVE** for substantial engineering and design decisions.

This document defines how StudyHub Local evolves. It does not claim that the
target architecture is already implemented. Current runtime truth lives in
[Architecture](ARCHITECTURE.md), [Desktop Architecture](DESKTOP_ARCHITECTURE.md),
and the code and acceptance tests.

## Status Language

Use these labels whenever a document could be mistaken for runtime truth:

- **CURRENT**: observable behavior on `main`.
- **IMPLEMENTED**: present in code and covered by appropriate verification.
- **TARGET**: an agreed architectural direction, not necessarily implemented.
- **PROPOSED**: under evaluation and not an adopted decision.
- **HISTORICAL**: retained evidence from an earlier audit or migration.

Never describe TARGET or PROPOSED concepts as shipped features.

## Change Rules

1. Inspect the repository, current docs, tests, trust boundaries, and nearby
   implementation before changing anything.
2. Make the smallest coherent change that satisfies the evidence.
3. Preserve current runtime behavior unless the PR explicitly changes it.
4. Keep domain changes, infrastructure adoption, visual redesign, migrations,
   and release engineering in focused PRs.
5. Do not perform drive-by refactors.
6. Use synthetic public fixtures only. Private academic content never enters
   Git, public screenshots, issues, logs, or tests.
7. Run the relevant acceptance suites and privacy check before proposing a
   merge.

## Reuse Before Build

Commodity infrastructure follows this sequence:

```text
DISCOVER -> AUDIT -> SELECT -> PIN -> WRAP -> INTEGRATE -> SKIN -> TEST -> ATTRIBUTE
```

Preference order:

```text
official implementation
> mature dependency
> adapter around a permissive implementation
> small permissively licensed transplant
> custom implementation
```

"Lightweight custom implementation" is not enough reason to replace a mature
dependency. A proposal must explain the required capability, evaluated
alternatives, maintenance and security cost, licensing, integration boundary,
and exit strategy.

### License Gate

- MIT, Apache, BSD, ISC, and CC0 candidates may continue through normal audit.
- GPL, AGPL, BSL, fair-source, and non-commercial implementations are
  reference-only by default unless an explicit licensing decision approves a
  compatible use.
- Source with no license must not be copied.
- Publicly visible source is not automatically reusable source.
- Preserve required notices and attribution for every adopted dependency or
  transplanted implementation.

Dependency manifests and lockfiles are authoritative for actually adopted
versions. Do not invent a pin in documentation. A separate `reuse.lock.json` is
not currently needed: the donor entries below are target decisions or
candidates, not newly adopted dependencies.

## Product Ownership Boundary

StudyHub owns academic semantics and user intent. It should normally reuse
mature implementations for commodity infrastructure.

StudyHub-owned concepts include:

- Academic Model and Academic Entity Resolution
- Source and Blob separation
- provenance, SourceAnchor, and Evidence
- content ownership and DerivedArtifact
- AcademicEvent and AcademicAction
- ContextPack and StudyPack
- ChangeSet semantics
- ingestion stages and sync normalization
- Capability Registry and Command Registry
- trust boundaries

StudyHub should normally not reimplement PDF rendering, rich-text editing,
spaced-repetition algorithms, OCR, speech recognition, media conversion,
credential storage, file watching, retry primitives, persistent background
queues, SQLite, full-text or vector indexing, calendar grids, desktop updater
infrastructure, notifications, deep links, single-instance handling, or
standard UI primitives.

External APIs should sit behind narrow StudyHub-owned interfaces such as
`PdfRenderer`, `ReviewScheduler`, `TranscriptionProvider`,
`EmbeddingProvider`, `CanvasConnector`, `CredentialStore`, and
`DownloadManager`. These names describe TARGET boundaries; they are not a claim
that matching interfaces exist today. Adapters expose only capabilities the
product uses and must not spread donor-specific types through domain code.

Avoid speculative enterprise abstractions. Add an interface when a real
boundary or test seam requires it.

## Donor Registry

This registry records architectural direction, not installed packages. An
entry becomes an adopted dependency only through a separate audited PR and the
normal manifest/lockfile.

| Capability | Status | Preferred foundation or authority | Mode | StudyHub owns |
| --- | --- | --- | --- | --- |
| PDF rendering | TARGET DECISION, not implemented | PDF.js | Dependency behind an adapter | MaterialViewer behavior, SourceAnchor integration, academic navigation |
| Rich text | TARGET DECISION, not implemented | Tiptap | Dependency with narrow extensions | Academic/source-reference extensions and persistence semantics |
| Spaced repetition | TARGET DECISION, not implemented | `fsrs-rs` / FSRS ecosystem | Dependency behind `ReviewScheduler` | ReviewItem, academic-learning integration, user state |
| Canvas | TARGET BOUNDARY, not implemented | Official Canvas API is authoritative | Connector plus audited references | CanvasConnector, SyncPlan, normalization, provenance, change detection, ingestion |
| Credential storage | IMPLEMENTED FOUNDATION | `keyring` 4.2.0, native OS credential stores | Dependency behind internal `CredentialStore` boundary | typed slots, runtime-profile namespaces, trust boundary, sanitized failure categories |
| Background jobs | OPEN DECISION | Effectum and Apalis SQLite are benchmark candidates | No selection in Phase 0 | Job semantics, privacy boundary, progress and recovery UX |

Canvas-related open-source projects may inform endpoint coverage,
authentication strategies, discovery, pagination, and workflow behavior only
after license review. Incompatible or restrictive implementations remain
reference-only. Do not copy them. Do not implement a custom persistent queue
while the background-job decision remains open.

## Domain Vocabulary

The TARGET model preserves these distinctions:

```text
Source != Material != Blob
Material != MaterialVersion
Course != CourseOffering
Official remote due date != personal target date
Remote state != user overlay
Derived data != user-owned data
```

Target vocabulary includes `Institution`, `Term`, `Course`, `CourseOffering`,
`StudyPeriod`, `Source`, `Blob`, `Material`, `MaterialVersion`, `Assessment`,
`Question`, `Concept`, `SourceAnchor`, `Evidence`, `ReviewItem`,
`AcademicAction`, `AcademicEvent`, `DerivedArtifact`, and `ChangeSet`.

These terms are architectural targets. The current SQLite model uses terms,
courses, weeks, files, file versions, chunks, questions, notes, study state,
and AI history. See [Architecture](ARCHITECTURE.md) for the explicit gap.

## Data Ownership And Trust

Apply these four semantics at the datum, field, or artifact layer. Do not force
an entire normalized entity into exactly one class: an Assessment, for example,
may contain remote-authoritative fields and a user-owned overlay.

- **REMOTE_AUTHORITATIVE**: facts mirrored from an institution or provider.
- **USER_OWNED**: notes, annotations, targets, classifications, review state,
  and other user intent.
- **DERIVED**: extracted text, previews, indexes, embeddings, and computed links.
- **EPHEMERAL**: temporary downloads, conversion workspaces, transient caches,
  and retry state.

Invariants:

- Remote synchronization never silently overwrites user intent.
- Derived data never becomes the only copy of user-owned information.
- Ephemeral data is always safe to delete.
- User-owned imported originals remain authoritative for their user-owned
  source.
- Remote provider facts and files remain remote-authoritative; locally retained
  Blob or MaterialVersion copies preserve provenance without changing that
  authority.
- Derived data never replaces either authoritative source.
- Original academic files remain outside the public repository.
- Production academic data never appears in public fixtures, screenshots,
  tests, README examples, public logs, or Git history.
- Credentials use handles or platform credential storage where practical and
  never enter public logs or fixtures.

Local trusted data includes the database, academic files, notes, annotations,
review state, local indexes, and credentials. LMS providers, cloud AI, Zotero
or cloud providers, and other APIs cross explicit external trust boundaries.
See [Privacy](PRIVACY.md) and [Security](../SECURITY.md).

## Sync And Ingestion Boundary

TARGET remote synchronization:

```text
Remote system
  -> connector
  -> remote changes
  -> SyncPlan
  -> domain normalization
  -> ingestion/domain services
  -> repositories
```

Connectors must not write domain tables directly. Sync discovers remote
changes; ingestion turns acquired content into StudyHub content.

TARGET ingestion stages:

```text
Acquire -> Identify -> Persist Blob -> Extract -> Normalize
        -> Resolve -> Store -> Index -> Link
```

The CURRENT local filesystem scanner combines several of these concerns in
`server.py`. Future extraction must be incremental and behavior-preserving,
with tests around provenance, identity, user overlays, and rebuildability.

## Change Safety

Use this interaction pattern where a change is destructive, ambiguous, or has
wide academic impact:

```text
Preview -> Explain -> Apply -> Undo
```

Prefer explicit ChangeSet-like semantics over low-information confirmation
dialogs. A preview should identify affected entities, preserved user data,
derived data that will be rebuilt, conflicts, and a recovery path.

## Experience Constitution

The visual direction is **Precision Academic Desktop**:

- high information density and low visual noise
- content before decoration; hierarchy before cards
- desktop-native behavior when it is better
- keyboard-first interaction and progressive disclosure
- calm, precise, structured, professional, restrained presentation
- typography, alignment, spacing, thin borders, subtle surfaces, and limited
  semantic color

Avoid giant rounded cards, decorative gradients, glow, excessive
glassmorphism, rainbow course cards, pill-heavy interfaces, oversized
mobile-style controls, emoji interface icons, gamification, confetti, and
decorative motion. Color communicates state, not decoration. Donor components
must lose donor product skin and use StudyHub tokens and primitives.

Target interaction language:

| Shortcut | Meaning |
| --- | --- |
| `Cmd+K` | Commands |
| `Cmd+Shift+F` | Global Search |
| `Cmd+,` | Settings |
| `Space` | Quick Look |
| `Esc` | Close contextual layer |

Command, Search, and Ask remain different semantic concepts even when they
share UI primitives. Secondary actions normally belong in context menus,
inspectors, selection state, hover affordances, or the command palette rather
than permanent row-level button walls.

Detailed design rules live in
[Design and Interaction Constitution](design/DESIGN_SYSTEM_PLAN.md). This Phase
0 PR does not redesign any screen.

## Scope And Migration

Near-term work hardens the local desktop product, current academic model,
source safety, study workflows, accessibility, and packaging. It does not turn
StudyHub into SaaS, a public file host, a telemetry product, or an autonomous
LMS agent.

Target-domain migration is incremental and additive. Preserve stable IDs,
source provenance, user overlays, notes, study history, user-owned imported
originals, and provenance for locally retained remote content.
Separate architecture extraction, schema migration, infrastructure adoption,
and visual redesign into independently reversible changes. See
[Roadmap](ROADMAP.md) for scope and open decisions.

## Documentation Governance

Use this authority map:

| Concern | Canonical source |
| --- | --- |
| Engineering rules, reuse, donor decisions | This document |
| Current and target application architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Current packaged desktop behavior | [DESKTOP_ARCHITECTURE.md](DESKTOP_ARCHITECTURE.md) |
| Privacy and ownership handling | [PRIVACY.md](PRIVACY.md) |
| Security controls | [SECURITY.md](../SECURITY.md) |
| Current product navigation | [CURRENT_IA.md](design/CURRENT_IA.md) |
| Design and interaction rules | [DESIGN_SYSTEM_PLAN.md](design/DESIGN_SYSTEM_PLAN.md) |
| Current preview behavior | [PREVIEW_MATRIX.md](design/PREVIEW_MATRIX.md) |
| Product scope and open decisions | [ROADMAP.md](ROADMAP.md) |
| Historical P0 audit evidence | [PRODUCT_COMPLETENESS.md](PRODUCT_COMPLETENESS.md) |

Do not create a competing source of truth. Historical reports should be
clearly labeled and link to the current authority. Obsolete proposals should
be removed or reduced to a pointer. Update the authority map when ownership of
a decision changes.
