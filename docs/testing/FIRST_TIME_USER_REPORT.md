# First-Time User Acceptance Report

Date: 2026-08-25
Repository: https://github.com/langming58-hash/studyhub-local
Release under test: v0.1.5

## Scope

This was a true first-time-user retest against the public repository only.

The test intentionally did not use:

- any existing private StudyHub checkout
- any private study library
- any existing `.env.local`
- any existing SQLite database
- any existing OpenAI key or vector store configuration
- the default port used by a private local instance

All user-library tests used synthetic temporary fixtures only.

## Retest Focus

This retest rechecked the P2 findings from the previous public acceptance pass:

| Finding | Result |
| --- | --- |
| Hide stale empty courses after switching StudyLibrary | PASS |
| Tighten Ask GPT no-match behavior | PASS |
| Make Notes discoverable and persistent in the UI | PASS |
| Improve plain-text official solution parsing | PASS |
| Sanitize solution metadata responses | PASS |

The privacy regression discovered during retest was fixed: internal text-cache paths are no longer exposed in solution payloads.

## Core UI Acceptance

| Area | Result | Notes |
| --- | --- | --- |
| Primary navigation | PASS | Reduced to Home, Courses, Search, Study, AI, Settings. |
| Home | PASS | Study-first layout with Continue, latest courses, recent files, and study queue. System metrics and progress bars are no longer the main experience. |
| Courses | PASS | Canonical library browser with active courses, week navigation, starred files, Scan Library, and Add Material. |
| Course -> Week navigation | PASS | Week-level files are grouped as Course Materials, Exercises, and My Work / Review. |
| Search | PASS | Single prominent search input with progressive filters and clean file-row results. |
| Study | PASS | Practice, Wrong Questions, and Exam Review are consolidated into one workspace. |
| AI | PASS | Existing source-grounded AI workspace, conversation history, context scoping, and citation UI remain available. |
| Settings | PASS | Library Health, AI status, Privacy, and Advanced diagnostics are available without exposing secrets or provider IDs. |
| File preview | PASS | Original preview, extracted text, details, notes, Open Original, and Ask AI remain available. |
| Notes | PASS | File notes are visible from the file workspace and persist after restart. |

## Demo Mode Safety

| Check | Result |
| --- | --- |
| Synthetic-only courses | PASS |
| No real university course codes | PASS |
| No private filesystem paths in UI/API responses | PASS |
| No API keys or provider IDs exposed | PASS |
| OpenAI status shown without exposing keys | PASS |
| Vector store status shown without exposing IDs | PASS |
| No generated practice questions when sources are absent | PASS |

## Ask GPT Behavior

No API key was configured for this first-time-user retest. Expected behavior is local source-backed preview mode plus strict no-source handling.

| Scenario | Result |
| --- | --- |
| Ask about indexed synthetic material | PASS |
| Ask for a real synthetic tutorial question | PASS |
| Ask for nonexistent/generated practice question | PASS |
| Empty/no-match source scope | PASS |
| No unrelated teacher questions surfaced on no-match | PASS |
| No generated practice questions | PASS |
| Solution payload contains only allowlisted metadata | PASS |

## Custom Study Library

A synthetic custom StudyLibrary was created with supported files, unsupported files, duplicate-style questions, Unicode filenames, and an empty-library case.

| Check | Result |
| --- | --- |
| Initial synthetic custom-library scan | PASS |
| Unsupported extension ignored safely | PASS |
| Unicode/spaces filename preserved | PASS |
| Incremental rescan skips unchanged files | PASS |
| Demo -> custom StudyLibrary switch hides stale empty Demo courses | PASS |
| Notes create/reload persistence | PASS |
| Plain-text official solution heading parsing | PASS |

## Visual And Interaction Retest

Headless Chrome was used against Demo Mode on localhost. Screenshots were captured at:

```text
1600
1440
1280
1024
900
768
```

Additional click-path checks covered:

- Home
- Courses
- latest week
- file preview drawer
- extracted text
- notes UI
- Search with results
- Study tab switching
- AI workspace
- Settings
- 150% zoom horizontal-overflow sanity

Result: PASS.

## CI And Fresh Clone

Local public checkout:

```text
npm run ci: PASS
```

Fresh isolated clone:

```text
git clone: PASS
npm install: PASS
npm run ci: PASS
Demo Mode startup: PASS
```

The fresh clone started with no inherited `.env.local`, cache, logs, runtime database, private StudyHub checkout, or private study library.

## Final Verdict

```text
Clean isolated environment: PASS
Existing private StudyHub used: NO
Existing private StudyLibrary used: NO
Existing .env.local used: NO
Existing database used: NO
Public repo clone: PASS
Install from README: PASS
First run Demo Mode: PASS
Course/week browsing: PASS
File preview/open original: PASS
Search: PASS
Ask GPT no-key fallback: PASS
Ask GPT no-match scoping: PASS
No generated practice questions: PASS
Notes discoverability: PASS
Notes persistence: PASS
Plain-text solution parsing: PASS
Solution metadata sanitization: PASS
Security/privacy basics: PASS
Responsive sanity: PASS
Accessibility sanity: PASS
npm run ci: PASS
Fresh clone: PASS
Private repo unchanged: YES
Overall first-time-user readiness: PASS
```

No P0, P1, or P2 issues remain open from this retest.
