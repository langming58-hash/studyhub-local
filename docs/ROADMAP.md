# Roadmap

Status: **AUTHORITATIVE SCOPE AND OPEN-DECISION MAP**.

StudyHub Local is early-stage. Directions are intentionally not tied to dates.
TARGET items are not shipped features. Architecture and donor rules are in the
[Engineering Constitution](ENGINEERING_CONSTITUTION.md).

## Current Product Baseline

The current product is a local-first desktop study workspace with course and
week organization, local preview/search, notes and study state, source-grounded
optional AI, a read-only MCP surface, and an Apple Silicon unsigned beta.

Original academic files remain user-controlled. The current implementation is
a Python localhost backend, SQLite runtime database, plain web frontend, and
Tauri desktop shell with a packaged Python sidecar.

## Near-Term V1 Direction

- harden stable academic identity, provenance, and user-overlay behavior
- clarify Source/Blob/Material/MaterialVersion boundaries incrementally
- improve study sessions, wrong-question review, anchored notes, and manual
  review-item workflows without inventing teacher content
- improve extraction and graceful capability discovery for PDFs, Office files,
  OCR, and workbook-aware reading
- refine search ranking, filtering, and source navigation
- complete human keyboard and VoiceOver validation
- provide privacy-safe backup/export for user-owned metadata and study history
- improve signed/notarized desktop distribution and dependency handling

Each item requires a focused design and migration PR. This roadmap does not
authorize a broad schema rewrite or product redesign.

## Target Architecture Migration

Migration from the current `files`-centered model is additive:

1. Define domain boundaries and tests before changing persistence.
2. Preserve stable identities, original files, provenance, and user overlays.
3. Introduce Source, Blob, Material, and MaterialVersion semantics in focused
   steps.
4. Separate sync discovery from ingestion and repositories.
5. Keep derived data rebuildable and ephemeral data disposable.
6. Use previewable ChangeSet-style operations for ambiguous or destructive
   changes.

Database migration, donor adoption, Canvas connectivity, and visual redesign
must not be bundled into one change.

## Donor Decisions

- PDF rendering direction: PDF.js behind StudyHub viewer semantics.
- Rich-text direction: Tiptap with StudyHub academic/source-reference behavior.
- Review scheduling direction: `fsrs-rs` / FSRS behind a ReviewScheduler.
- Canvas authority: official Canvas API behind a StudyHub CanvasConnector.
- Persistent background jobs: open decision; Effectum and Apalis SQLite remain
  benchmark candidates. Do not build a custom queue in the meantime.

These are target decisions or candidates, not current installed dependencies.
The canonical registry and licensing rules are in the constitution.

## Explicitly Out Of Scope For V1

- SaaS accounts or hosted academic-file storage
- public deployment of the localhost server
- telemetry or analytics by default
- autonomous submission of quizzes, assignments, or Canvas work
- AI-generated official-looking practice questions
- social, marketplace, or gamification systems
- a mobile rewrite of the desktop product

## Open Decisions

- persistent background-job foundation and recovery semantics
- Keychain-backed credential storage and migration from current local setup
- OCR and media-transcription providers and packaging strategy
- universal/Intel macOS and Windows distribution sequence
- updater, deep-link, notification, and single-instance infrastructure
- versioned backup/restore format for user-owned state

Open decisions remain PROPOSED until an audited decision PR records
alternatives, licensing, trust boundary, ownership, migration, and tests.

## Product Principles

- Keep originals authoritative and user-controlled.
- Keep user intent separate from remote state and derived data.
- Keep synthetic fixtures test-only and out of production bundles.
- Avoid telemetry by default.
- Keep optional AI source-grounded and clearly cited.
- Never generate teacher-style practice questions when no teacher question is
  found.
- Prefer mature infrastructure behind narrow StudyHub interfaces.
