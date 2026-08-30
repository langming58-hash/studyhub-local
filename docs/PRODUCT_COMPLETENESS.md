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
| Library | Stable term identity | MISSING | Implement |
| Library | Stable course identity independent of folder/code/name | PARTIAL | Migrate |
| Library | Create, rename, archive, restore, remove course | MISSING | Implement |
| Library | Dynamic weeks/modules | MISSING | Implement |
| Library | Material type metadata | PARTIAL | Normalize |
| Materials | Reference original files in place | PARTIAL | Make default |
| Materials | Native single/multi-file picker | PARTIAL | Implement desktop path import |
| Materials | Batch import and drag/drop | MISSING | Implement |
| Materials | Inbox/unclassified import | MISSING | Implement |
| Materials | Reclassify without moving source | MISSING | Implement |
| Materials | Duplicate path/checksum detection | PARTIAL | Implement explicit decisions |
| Materials | Missing-file state and relink | PARTIAL | Implement relink |
| Materials | Remove metadata without deleting source | MISSING | Implement |
| Demo | Synthetic fixture library | EXISTS | Preserve |
| Demo | Explicit disposable Demo workspace | PARTIAL | Isolate/reset |
| Data | Clear recreatable cache | MISSING | Implement |
| Data | Reset StudyHub-owned state only | MISSING | Implement |
| Data | Metadata backup/restore | MISSING | Implement after CRUD |
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

## Deferred Product Work

P1 study sessions, wrong-question scheduling, manual flashcards, spaced
repetition, weak-topic surfaces, anchored notes, and highlights start only after
P0 migration and packaged validation pass. Keychain BYOK and advanced AI source
selection remain P2. Visual redesign remains a separate P3 pass.
