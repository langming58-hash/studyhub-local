# First-Time User Acceptance Report

Date: 2026-08-25
Repository: https://github.com/langming58-hash/studyhub-local
Release under test: v0.1.5

## Scope

This report covers the public first-time-user retest plus the follow-up first-run polish pass.

The test intentionally did not use:

- any existing private StudyHub checkout
- any private study library
- any existing `.env.local`
- any existing SQLite database
- any existing OpenAI key or vector store configuration
- the default port used by a private local instance

All user-library tests used synthetic temporary fixtures only.

## First-Run Polish Update

The follow-up pass focused on whether a new student can understand and begin using StudyHub without developer knowledge.

| Area | Result | Notes |
| --- | --- | --- |
| Fresh first launch | PASS | With no `.env.local`, StudyHub starts in Demo Mode instead of failing on a missing local folder. |
| 10-second comprehension | PASS | Home now explains that StudyHub is a private study hub, that demo files are synthetic, that real files stay local, and that OpenAI is optional. |
| Demo Mode clarity | PASS | Demo Mode appears as contextual onboarding, not a blocking setup wizard. |
| Own-library setup | PASS | Settings exposes a visible study-folder form and writes only local `.env.local` configuration. The API response does not echo absolute paths. |
| Dependency preflight | PASS | StudyHub surfaces actionable notices for PDF text support, missing/empty study folders, local save problems, OpenAI optional setup, and CA-bundle readiness. |
| Error recovery | PASS | Recovery cards explain what happened, what is affected, and what to do next. Technical details stay behind disclosure controls. |
| Port conflict | PASS | Startup selects the next local port and prints an understandable localhost message. |
| Missing original file | PASS | Open Original reports that the file is missing and suggests scanning again. |
| Restart/continue | PASS | Last-file continuation and AI conversation state remain available after reload. |

Normal student-facing UI was also checked for avoidable implementation language. Terms such as internal chunks, source file IDs, retrieval internals, and cache paths do not appear in the ordinary Home/Courses/Search/Study/AI journey.

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

## Accessibility Sanity

This was not a full manual VoiceOver audit and should not be treated as a WCAG claim.

Chrome's accessibility tree and keyboard focus behavior were checked for:

- global navigation
- first-run onboarding actions
- file preview drawer controls
- readable-text panel
- notes controls
- AI composer
- AI history drawer

Result: PARTIAL PASS. Controls had meaningful names and keyboard focus was reachable. A full human screen-reader pass remains recommended before claiming formal accessibility compliance.

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
First-run onboarding: PASS
Dependency preflight: PASS
Error recovery: PASS
Responsive sanity: PASS
Accessibility sanity: PARTIAL PASS
npm run ci: PASS
Fresh clone: PASS
Private repo unchanged: YES
Overall first-time-user readiness: PASS
```

No P0, P1, or P2 issues remain open from this retest.
