# Study Engine

Status: **AUTHORITATIVE CURRENT STUDY ENGINE V1 CONTRACT**.

Study Engine is a local, user-owned workflow over canonical `Material`
identity. It answers what to continue, what needs review, and how progress
rolls up without requiring Canvas, OpenAI, credentials, or any network access.

## Durable State

Migration 7 adds one `material_study_state` row per canonical Material. The
progress enum is `NOT_STARTED`, `IN_PROGRESS`, or `COMPLETED`.
`needs_review` is independent: a completed Material may still require review.
The row also stores first-started, completed, and last-studied timestamps plus
the explicit portable fields needed to reconstruct an FSRS card.

Study state is `USER_OWNED`. Paths and filenames are not identity authority.
Course and Learning Unit progress are derived at read time and are not stored
as summary percentages.

## ReviewScheduler

StudyHub adopts `fsrs` 6.3.2 (`py-fsrs` upstream), licensed MIT, behind the
StudyHub-owned `ReviewScheduler` adapter. The package supports Python 3.10+
and has been exercised with the packaged Python 3.14 runtime. Only the local
scheduler is installed; optimizer extras and their numerical/ML dependencies
are excluded.

The adapter reconstructs a card from explicit SQLite fields, applies one of
`Again`, `Hard`, `Good`, or `Easy` at a caller-supplied review time, and writes
the resulting portable fields transactionally. Fuzzing is disabled so fixed
state and time produce deterministic acceptance results. No opaque Python
object is serialized.

## Deterministic Queue

The queue uses one bounded query and stable tie-breakers. Its precedence is:

1. due FSRS reviews, oldest due first;
2. explicit `needs_review` Materials;
3. `IN_PROGRESS` Materials, most recently studied first;
4. `NOT_STARTED` Materials in stable academic order.

FSRS review does not replace the independent explicit-review concept. A
completed review clears the explicit flag for that action, and the next FSRS
due state remains durable.

## Product Surface

The Study destination defaults to Overview and preserves the existing
Practice, Wrong Questions, and Exam Review modes. Overview shows the queue,
Continue studying, Needs review, Recently studied, Course rollups, and Learning
Unit rollups. Material surfaces expose progress controls and a Needs Review
toggle. Search remains FTS5-based and applies a small deterministic study-state
ordering boost after textual rank; there are no embeddings or AI ranking.

## Backup And Recovery

The current writer emits `studyhub-backup-v2`. V2 adds
`material_study_state` records to `data/study_records.json`. The reader remains
strict and explicitly supports legacy v1 archives; v1 has no Study Engine
state and therefore cannot manufacture it.

V2 Study state participates in the existing frozen recovery contract:

```text
Export -> Preview -> Apply -> ChangeSet -> Undo
```

Preview classifies state as create, match, or conflict. Explicit
`RESTORE_BACKUP` applies validated archive values. The closed reversible
contract captures the exact pre/post row, so Undo restores the prior local
Study state. Later overlapping Study work fails the existing postimage CAS
safety check. Old Restore journals whose schema fingerprint predates migration
7 remain unsupported and fail closed; they are not weakened or rewritten.

## Privacy And Isolation

Study state stays in the active runtime profile's SQLite database and backup
only. It is excluded from logs, diagnostics, static assets, and the read-only
MCP surface. Browser responses contain only the safe state required by the
Study UI and never include credentials or absolute paths. Demo/Test remains in
its isolated disposable profile.

Automated tests use synthetic Materials and temporary roots only. They do not
read a real StudyLibrary, call Canvas/OpenAI/CredentialStore, or mutate source
files.

The populated gallery fixture under `tests/fixtures/product-gallery/` contains
only explicitly synthetic CS101, ECON201, and STAT210 text materials. It exists
for product screenshots and visual QA and is excluded from packaged resources.

## Not Implemented Here

Study Engine V1 does not add assignments/Today, notifications, streaks,
gamification, flashcard authoring, optimizer training, background jobs, vector
search, Reader changes, OCR, or production Canvas connection UI.
