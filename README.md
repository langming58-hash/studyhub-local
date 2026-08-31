# StudyHub Local

[![Release](https://img.shields.io/github/v/release/langming58-hash/studyhub-local?label=release)](https://github.com/langming58-hash/studyhub-local/releases/tag/v0.2.0)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/langming58-hash/studyhub-local/actions/workflows/ci.yml/badge.svg)](https://github.com/langming58-hash/studyhub-local/actions/workflows/ci.yml)
![Local-first](https://img.shields.io/badge/localhost--only-local--first-blue)

[简体中文](README.zh-CN.md)

StudyHub Local turns a folder of university course files into a private, searchable study workspace with progress, review queues, notes, and optional source-grounded AI.

![StudyHub Local populated home using synthetic data](docs/assets/screenshots/product-home.png)

## Why StudyHub Local

- **One structure for scattered files.** Browse Course -> Week -> Materials / Exercises without replacing or converting the originals.
- **A study loop, not just a file browser.** Track started and completed material, mark items for review, and return through a local study queue.
- **AI that stays accountable.** Ask within the current file, week, or course and follow citations back to indexed source material. OpenAI is optional.

## Core Capabilities

- Local course and week organization for PDF, Office, text, code, CSV, R, and notebooks
- Filename and extracted-content search with course, week, and material filters
- Material progress, review flags, course/week rollups, practice, and wrong-question records
- Private notes, stars, recent files, and locally stored AI conversations
- Source-grounded AI with narrow context and teacher-question safeguards
- Clean first run, English and Simplified Chinese UI, and no telemetry by default

## Product Tour

Every populated screenshot below was generated from synthetic fixtures under `tests/fixtures/`. No real course material, account data, API key, or private path is shown.

<table>
  <tr>
    <td width="50%"><strong>Course and week progress</strong><br><img src="docs/assets/screenshots/product-course.png" alt="Synthetic course and week progress" width="640"></td>
    <td width="50%"><strong>Local full-text search</strong><br><img src="docs/assets/screenshots/product-search.png" alt="Synthetic local search results" width="640"></td>
  </tr>
  <tr>
    <td width="50%"><strong>Study plan and review queue</strong><br><img src="docs/assets/screenshots/product-study.png" alt="Synthetic study plan and review queue" width="640"></td>
    <td width="50%"><strong>AI answer with source context</strong><br><img src="docs/assets/screenshots/product-ai-citations.png" alt="Synthetic AI answer with source citation" width="640"></td>
  </tr>
  <tr>
    <td width="50%"><strong>Readable text and private notes</strong><br><img src="docs/assets/screenshots/product-notes.png" alt="Synthetic readable text and private note" width="640"></td>
    <td width="50%"><strong>Local-first health and settings</strong><br><img src="docs/assets/screenshots/product-settings.png" alt="Synthetic local-first settings" width="640"></td>
  </tr>
</table>

## Local-First Model

```text
Your StudyLibrary folder (source of truth)
  -> local scanner and text extraction
  -> local SQLite metadata, search, notes, and progress
  -> localhost UI
  -> optional server-side OpenAI Responses API / file search
```

Original files stay in a folder you control. Runtime databases, extracted text, previews, and AI history remain local and are ignored by Git. Vector search, when configured, is a retrieval layer rather than a second authoritative library.

Read [Architecture](docs/ARCHITECTURE.md) and [Study Engine](docs/STUDY_ENGINE.md) for the data model and trust boundaries.

## Quick Start

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
npm install
python3 -m pip install -r requirements.txt
npm run dev
```

Open the printed loopback URL, usually `http://127.0.0.1:8765`, then create a course or select a study-library folder. StudyHub never scans unrelated folders automatically.

Requirements: Git, Node.js/npm, and Python 3. Poppler (`pdftotext` and `pdfinfo`) is recommended for PDF text extraction; LibreOffice is optional for higher-fidelity PowerPoint and Word previews.

The current public release is **v0.2.0**. It is source-only: no unsigned or unnotarized macOS app or DMG is attached. `Start StudyHub Local.command` is a source-install convenience launcher, not a signed desktop release.

## Study Library

Keep real study files outside the repository. A typical structure is:

```text
~/StudyLibrary/
├── CS101 - Programming Fundamentals/
│   ├── Week 01/
│   │   ├── 01 Course Materials/Lecture/
│   │   └── 02 Exercises/Tutorial/
│   └── Week 02/
└── ECON201 - Macroeconomics/
```

Original files remain authoritative and are never converted into unrelated study formats.

## Optional OpenAI

OpenAI is server-side and optional. Create `.env.local` only on your own machine:

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4
```

Never place keys in frontend code, screenshots, logs, issues, or commits. AI starts with the narrowest reasonable scope: current question, file, week, then course. If indexed sources do not support an answer, StudyHub returns a no-source response instead of inventing course content. It never generates new practice questions.

## Clean First Run

Production starts empty: no sample courses, teacher material, test database, extracted text, or credentials are bundled.

| English | 简体中文 |
| --- | --- |
| ![Clean first run in English](docs/assets/screenshots/first-run-en.png) | ![Clean first run in Simplified Chinese](docs/assets/screenshots/first-run-zh-CN.png) |

## Privacy And Security

- Loopback-only by default, with no telemetry by default
- Host, exact same-origin, CSRF, request-size, and filesystem-root checks
- Runtime data, secrets, logs, and academic files rejected by privacy CI
- Active-content previews isolated from the trusted application origin
- Read-only MCP boundary for local integrations

Read [SECURITY.md](SECURITY.md) and [PRIVACY.md](PRIVACY.md) before changing trust boundaries.

## Development

Synthetic fixtures live only under `tests/fixtures/` and must never be bundled into production resources.

```bash
npm run ci
npm run desktop:check
```

See [Development](docs/DEVELOPMENT.md), [Desktop Architecture](docs/DESKTOP_ARCHITECTURE.md), [Roadmap](docs/ROADMAP.md), and [Contributing](CONTRIBUTING.md).

## License

[MIT](LICENSE)
