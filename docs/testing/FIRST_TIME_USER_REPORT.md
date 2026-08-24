# First-Time User Acceptance Report

Date: 2026-08-24
Repository: https://github.com/langming58-hash/studyhub-local
Tested public commit: 6dfcea5046ca7be85ceded4c2ef19ecfc559d419
Release under test: v0.1.4

## Scope

This was a true first-time-user acceptance test against the public repository only.

The test intentionally did not use:

- any existing private StudyHub checkout
- any private study library
- any existing `.env.local`
- any existing SQLite database
- any existing OpenAI key or vector store configuration
- the default port used by the user's private local instance

All user-library tests used synthetic temporary fixtures only.

## Environment

- macOS desktop environment
- Python 3.14
- npm available
- Chrome used for visual review
- Local test ports: 8876, 8877, 8878, 8879

One harness-specific issue occurred: the Codex sandbox could not bind localhost sockets without an elevated local command approval. This is not expected for a normal user running the project directly in Terminal.

## First Impression

Within the first 10 seconds, the README made the core purpose understandable: StudyHub Local is a local-first course material browser with course/week organization, local search, practice/wrong-question views, and optional source-grounded AI.

The privacy posture is visible early: Demo Mode is synthetic, OpenAI is optional, localhost is the default, and runtime/private files are excluded from Git.

Potential nontechnical-user friction:

- The user still has to understand copying `.env.example` to `.env.local`.
- The difference between ChatGPT Plus and OpenAI API billing is documented, but users may still expect their ChatGPT subscription to configure Ask GPT automatically.
- Port changes are documented in support material, but not in the shortest Quick Start path.

## Clean Install

Commands used from a clean public clone:

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
cp .env.example .env.local
npm install
python3 server.py serve --port 8876
```

Result:

- Clone succeeded.
- No `.env.local`, runtime database, cache, logs, or local study-library directory existed before setup.
- `npm install` completed with 0 vulnerabilities.
- First normal scan indexed the bundled synthetic Demo Mode data.
- The app opened on localhost using a non-private port.

Approximate time from GitHub page to first usable local app: 5 minutes.

## Core UI Acceptance

| Area | Result | Notes |
| --- | --- | --- |
| Home | PASS | Shows StudyHub Local, indexed-file count, and synthetic courses. |
| This Week | PASS | Current-week summaries render from indexed demo data. |
| Courses | PASS | Lists only synthetic `TEST1001`, `TEST2001`, and `TEST3001` in Demo Mode. |
| Course -> Week navigation | PASS | Week-level file grouping works through API and UI. |
| Materials / Exercises | PASS | Files expose material categories and exercise types. |
| Search | PASS | Search returns source-labelled matching files. Empty query/no-match behavior is safe. |
| Ask GPT without API key | PASS | Clearly returns a local source-backed preview rather than pretending GPT is configured. |
| Practice | PASS | Uses indexed teacher-provided synthetic questions. |
| Wrong Questions | PASS | Synthetic wrong-question view exists and is visible. |
| Starred | PASS | Starred file persisted after server restart. |
| Settings | PASS | OpenAI and vector store states show Not configured without exposing keys. |
| File preview | PASS WITH FRICTION | Extracted text is readable, but the main preview area for text files can show a generic file icon. |
| Notes | FAIL | No obvious Notes navigation or add-note UI was discoverable, even though a backend notes API/table exists. |
| Responsive layout | PASS WITH FRICTION | Narrow desktop/mobile-like widths remain usable, but the fixed sidebar consumes much of the viewport. |

## Demo Mode Safety

| Check | Result |
| --- | --- |
| Synthetic-only courses | PASS |
| No real university course codes | PASS |
| No private filesystem paths in UI/API responses | PASS |
| No API keys or provider IDs exposed | PASS |
| OpenAI status shown as Not configured | PASS |
| Vector store status shown as Not configured | PASS |
| No generated practice questions when sources are absent | PASS |

## Ask GPT Behavior

No API key was configured. The expected first-time-user behavior is therefore local source-backed preview mode.

Tested:

- Ask about indexed synthetic Week 04 material.
- Ask for a real synthetic Tutorial question.
- Ask for a nonexistent/generated practice question.

Results:

- Existing material returned with course/week/file sources.
- Teacher-provided synthetic questions were returned from indexed files.
- A nonexistent practice-question request returned: `I couldn't find this in the currently indexed official course materials.`
- No new practice questions were generated.

Friction found:

- Duplicate files with the same question number can both appear in Ask GPT results, making the answer ambiguous.
- A simple `Official Teacher Solution` heading in a plain text fixture was not parsed as a structured official solution.
- In one broad no-match prompt with empty context, local preview still surfaced available teacher questions even though no matching source chunks were found. It did not fabricate content, but the fallback is broader than a first-time user may expect.

## Custom Study Library

A synthetic user library was created with:

- one course-material text file
- one tutorial question file
- one duplicate question file
- one Unicode filename with spaces
- one unsupported `.bin` file
- one empty-library case

Results:

| Check | Result | Notes |
| --- | --- | --- |
| Scan synthetic custom library | PASS | 4 supported files indexed. |
| Unsupported extension | PASS | `.bin` was not indexed and did not crash scanning. |
| Unicode/spaces filename | PASS | Search found the file and preserved its filename. |
| Incremental rescan | PASS | Second scan reported 4 unchanged files and did not re-index them. |
| Empty library | PASS | Returned 0 files without crashing. |
| Wrong path | PASS WITH FRICTION | Failed safely, but CLI output is a Python traceback rather than a friendly setup message. |
| Switching from Demo Mode to custom library | PASS WITH FRICTION | Previous Demo courses remained as empty course rows with `file_count: 0` after files were removed. |

## Security And Privacy Checks

| Check | Result |
| --- | --- |
| Loopback-only bind | PASS |
| Non-loopback `HOST=0.0.0.0` refused | PASS |
| Hostile Host header rejected | PASS |
| Cross-port localhost Origin rejected | PASS |
| Exact same-origin POST accepted with valid CSRF | PASS |
| Missing/invalid CSRF rejected | PASS |
| Security headers present on API responses | PASS |
| Runtime/private files excluded from Git | PASS |
| Privacy scanner passes | PASS |
| No OpenAI key in frontend/runtime output | PASS |

## Installation Support

README and `docs/launch/INSTALL_SUPPORT.md` cover the main first-time-user path:

- clone
- copy `.env.example`
- install
- run Demo Mode
- optionally point to a local study library
- optionally configure OpenAI API
- use a different port if needed

The install path is workable for technical users. A less technical student could still benefit from a shorter "I just want to try it" section and friendlier CLI errors for missing library paths.

## CI And Fresh Clone

Local public checkout:

```text
npm run ci: PASS
```

Second fresh public clone:

```text
git clone: PASS
npm install: PASS
npm run ci: PASS
normal Demo Mode startup: PASS
```

The second fresh clone started with no inherited `.env.local`, cache, logs, runtime database, or local study-library directory.

## Findings

### P0

None.

### P1

None.

### P2

1. Notes are not discoverable or usable from the UI.
   - Reproduction: start the app, inspect the primary navigation and normal file/practice flows.
   - Expected: if notes are a supported feature, a first-time user can create or view notes from the UI.
   - Actual: no obvious Notes section or add-note control was found, although backend notes support exists.
   - Suggested files: `static/app.js`, `static/styles.css`, `server.py`.

2. Switching from Demo Mode to a custom library leaves stale empty Demo courses.
   - Reproduction: run Demo Mode, then switch `.env.local` to a custom synthetic library using the same local database and rescan.
   - Expected: inactive courses are hidden or clearly marked.
   - Actual: old Demo courses remain in `/api/courses` with `file_count: 0`.
   - Suggested files: `server.py`, course-list rendering in `static/app.js`.

3. Plain-text official solution headings are not reliably structured.
   - Reproduction: scan a tutorial file containing `Official Teacher Solution` followed by an answer.
   - Expected: the answer is attached as an official solution or at least not merged into a question body.
   - Actual: the heading was merged into a question body and `solutions` returned empty.
   - Suggested files: `server.py`, question/solution extraction tests.

4. Ask GPT local fallback can be too broad when a prompt has no matching source chunks.
   - Reproduction: ask a no-match prompt with empty context after indexing tutorial questions.
   - Expected: no-source response or clearly scoped results only.
   - Actual: local preview returned available teacher questions even though `sources` was empty.
   - Suggested files: `server.py`, Ask GPT local fallback tests.

### P3

1. Text-file preview has a generic icon in the main preview pane even though extracted text is available in the side panel.
2. Narrow/mobile-like layout remains usable but feels cramped because the sidebar keeps a large fixed width.
3. Missing-library CLI output is safe but developer-oriented because it prints a traceback.
4. The shortest Quick Start could mention `--port` for users who already have another local service on the default port.

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
OpenAI not configured behavior: PASS
Vector Store not configured behavior: PASS
Course/week browsing: PASS
File preview/open original: PASS WITH FRICTION
Search: PASS
Ask GPT no-key fallback: PASS
No generated practice questions: PASS
Synthetic custom StudyLibrary: PASS WITH FRICTION
Persistence after restart: PASS
Security/privacy basics: PASS
Responsive sanity: PASS WITH FRICTION
Accessibility sanity: PASS WITH FRICTION
npm run ci: PASS
Second fresh clone: PASS
Private repo unchanged: YES
Overall first-time-user readiness: PASS WITH FRICTION
```

StudyHub Local is broadly ready for first public users, with no blocking privacy/security/install failures found in this pass. The main remaining work is polish around notes discoverability, stale empty courses after library switching, and question/solution parsing edge cases.
