# StudyHub Product Completeness Audit

Status date: 2026-08-30  
Canonical line: `codex/desktop-prototype`  
Audit baseline: `341fae75c12273a52c2e02b6ba79700bc570bec3`

## Product Direction

StudyHub Local is a private, local-first study workspace. Original source files
remain owned by the user and are referenced in place. StudyHub owns metadata,
indexes, previews, notes, and study state only.

The target hierarchy is:

```text
Term -> Course -> Week or Module -> Material Type -> Material
```

Structured-folder scanning remains a fast import path, but it is no longer the
product's only data model.

## Reference Products

This audit uses current official product documentation to identify expected
behaviour without copying another product's branding or interface:

- RemNote connects notes, manual flashcards, and scheduled review.
  <https://help.remnote.com/en/articles/8663109-flashcard-basics>
- Quizlet makes study sessions and progress feedback explicit.
  <https://quizlet.com/features/study-modes>
- Goodnotes supports multi-file import and drag and drop into a library.
  <https://support.goodnotes.com/hc/en-us/articles/7353717816463-Import-files-into-Goodnotes>
- NotebookLM lets users choose sources and grounds answers with citations.
  <https://support.google.com/notebooklm/answer/16215270>
- MyStudyLife groups classes, tasks, and exams, but StudyHub intentionally does
  not become a full calendar replacement. <https://mystudylife.com/media/>
- Finder, Apple Notes, and macOS Settings inform hierarchy, progressive
  disclosure, and restrained desktop interaction rather than branding.

StudyHub deliberately differs from products that generate practice tests:
teacher-style questions must come from indexed teacher-provided material or
clearly synthetic Demo fixtures.

## Capability Matrix

| Area | Capability | Status | P0 decision |
| --- | --- | --- | --- |
| Runtime | Tauri shell with bundled Python sidecar | EXISTS | Preserve |
| Runtime | Python-free and Node-free end-user launch | EXISTS | Preserve |
| Privacy | Localhost-only backend and filesystem containment | EXISTS | Preserve |
| Library | Structured-folder scan | EXISTS | Keep as power import |
| Library | Stable term identity | IMPLEMENTED | Additive migration and CRUD |
| Library | Stable course identity independent of folder/code/name | IMPLEMENTED | Stable ID survives metadata edits |
| Library | Create, rename, archive, restore, remove course | IMPLEMENTED | Metadata-only operations |
| Library | Dynamic weeks/modules | IMPLEMENTED | No fixed Week 01-12 creation |
| Library | Material type metadata | IMPLEMENTED | Structured field and controls |
| Materials | Reference original files in place | IMPLEMENTED | Desktop default |
| Materials | Native single/multi-file picker | IMPLEMENTED | Tauri path import |
| Materials | Batch import and drag/drop | IMPLEMENTED | Desktop paths, bounded folder import |
| Materials | Inbox/unclassified import | IMPLEMENTED | Explicit later classification |
| Materials | Reclassify without moving source | IMPLEMENTED | Metadata-only batch action |
| Materials | Duplicate path/checksum detection | IMPLEMENTED | Open existing, add anyway, cancel |
| Materials | Missing-file state and relink | IMPLEMENTED | Missing state remains visible |
| Materials | Remove metadata without deleting source | IMPLEMENTED | Original stays untouched |
| Demo | Synthetic fixture library | EXISTS | Preserve |
| Demo | Explicit disposable Demo workspace | IMPLEMENTED | Isolated visibility and reset |
| Data | Clear recreatable cache | IMPLEMENTED | Current workspace only |
| Data | Reset StudyHub-owned state only | IMPLEMENTED | Confirmation required |
| Data | Metadata backup/restore | DESIGNED | Defer implementation until format is stable |
| Search | Local metadata and extracted-content search | EXISTS | Preserve |
| Preview | PDF, Office fallback, text/code preview | EXISTS | Preserve |
| Notes | File notes with persistence | EXISTS | Preserve |
| Notes | Page/slide/selection anchors | MISSING | P1 |
| Study | Teacher-provided question extraction | EXISTS | Preserve |
| Study | Wrong-question records | PARTIAL | P1 |
| Study | Structured study sessions | PARTIAL | P1 |
| Study | Manual flashcards and spaced repetition | MISSING | P1 |
| AI | Optional source-grounded Ask AI | EXISTS | Preserve |
| AI | Explicit multi-source selection | PARTIAL | P2 |
| AI | Keychain BYOK | MISSING | DEFER until P0 stabilizes |
| Visual | Current desktop information architecture | EXISTS | Freeze during P0 |
| Visual | Apple-like refinement and dark-mode tokens | PARTIAL | P3 |
| Platform | Mobile companion | DEFER | Frozen experiment |
| Platform | Windows packaging, App Store, auto-update | DEFER | Out of pass |
| Product | Cloud accounts, telemetry, marketplace, SaaS | REJECT | Out of scope |
| AI | AI-generated official-looking questions | REJECT | Never generate |

## Existing Database Audit

The baseline database contains `courses`, `weeks`, `files`, file versions,
document chunks, questions, solutions, notes, stars, attempts, wrong questions,
bookmarks, study sessions, AI conversations/messages, sync events, and AI index
state.

Current identity and migration risks:

- `courses.folder_name` is unique and acts as import identity.
- `courses.code`, `courses.name`, and `courses.path` are overwritten by scans.
- the scanner creates Week 01 through Week 12 for every course.
- `files.original_path` is unique and files are currently owned by a course.
- notes and stars point to integer targets without database foreign keys.
- questions, chunks, versions, AI state, and conversations point to file IDs.
- deleting course rows with cascades would destroy file-linked history.
- the browser upload endpoint writes copies into the configured library.

## Migration Plan

The migration is additive and idempotent:

1. Add stable public IDs and ownership metadata to existing records without
   replacing integer primary keys.
2. Add terms and assign existing courses to a deterministic default term.
3. Mark scanned courses/materials as folder-managed; preserve their row IDs.
4. Add user-managed courses and weeks without requiring filesystem folders.
5. Add material display/classification fields to existing `files` rows while
   preserving original paths and all file foreign-key relationships.
6. Change scans to discover actual weeks/modules and update only folder-managed
   metadata that has not been explicitly overridden by the user.
7. Treat missing and removed records as inactive metadata first; never delete
   original source files.
8. Add migration acceptance tests using a baseline-format synthetic database.

## Data Safety Invariants

- Normal create, rename, archive, move, classify, remove, reset, and restore
  actions never rename, move, overwrite, or delete source files.
- Course, material, note, star, question, study, and AI record IDs survive
  display-name and classification changes.
- A missing source remains visible as missing until relinked or removed from
  StudyHub.
- Backup excludes original files, extracted text, previews, credentials,
  provider IDs, and absolute cache paths.
- Demo mutations use disposable runtime state and never modify packaged fixture
  files.
- Public tests and documentation contain synthetic `TEST` data only.

## Backup And Restore Design

Backup is intentionally designed but not implemented in this pass. Shipping an
unstable export format would create a false recovery guarantee while the P0
schema is still settling.

The future export is a versioned archive containing a schema manifest and
portable JSON for terms, courses, weeks/modules, material metadata, notes,
stars, attempts, wrong-question records, study state, and local AI conversation
history. It must exclude original study files, extracted text, previews,
runtime databases, credentials, provider file/vector IDs, logs, and absolute
cache paths.

Restore must first validate the archive version and schema, show a dry-run
summary, ask the user to relink unavailable source roots, and then import in a
single database transaction after making a local rollback copy. Restore never
overwrites or moves an original source file. Stable IDs are used to reconcile
records; conflicts require an explicit keep/replace/duplicate decision.

## P0 Verification

The synthetic acceptance suite verifies additive legacy migration, term and
course lifecycle, stable identities, dynamic weeks/modules, 23-file folder
import, path and checksum duplicates, classification, cross-course moves,
missing-file relink, cache rebuild, note/star persistence, metadata-only
removal, reset safety, Demo/private visibility, the three first-run choices,
native multi-file selection, drag/drop wiring, and structured material types.

Manual browser validation additionally covered course creation, empty-course
display, custom Module creation, archive/restore, week-scoped batch actions,
refresh persistence, desktop and narrow layouts, and browser console errors.
All data used by these checks is synthetic `TEST` data in isolated temporary
runtime directories.

## Deferred Product Work

P1 study sessions, wrong-question scheduling, manual flashcards, spaced
repetition, weak-topic surfaces, anchored notes, and highlights start only after
P0 migration and packaged validation pass. Keychain BYOK and advanced AI source
selection remain P2. Visual redesign remains a separate P3 pass.
