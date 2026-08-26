# Architecture

StudyHub Local is a local-first study library.

## Components

- `server.py`: localhost HTTP server, scanner, SQLite schema, search, AI request handling, OpenAI sync, and read-only MCP endpoint.
- `static/`: static frontend served by the local backend.
- `demo-data/`: synthetic fixtures for public demos and tests.
- `data/`: runtime SQLite files, ignored by Git.
- `cache/`: extracted text and generated preview cache, ignored by Git.
- `logs/`: local logs, ignored by Git.

## Data Flow

```text
Configured study folder
  -> filesystem scanner
  -> SQLite metadata and FTS index
  -> local web UI / CLI / MCP
  -> optional OpenAI Responses API and vector store
```

The original file in `STUDY_LIBRARY_PATH` remains the source of truth. The database and vector store can be rebuilt.

## Preview Pipeline

StudyHub keeps visual previews separate from extracted readable text. PDF and
image files are previewed directly. Text, code, CSV, notebook, and active web
formats are shown as escaped readable text. PowerPoint and Word files use an
optional local LibreOffice headless conversion to cached PDF derivatives when
LibreOffice is available. The generated PDFs live only in runtime cache and do
not replace the original files.

If an Office visual preview cannot be created, the main pane shows a clear
unavailable state and keeps extracted text in the Readable Text tab.

See [Preview Matrix](design/PREVIEW_MATRIX.md) for the current file-type policy.

## Localhost Boundary

The server listens on `127.0.0.1` by default. Do not deploy it on a public host without a separate security review.

## Question Safety

Practice questions are retrieved from indexed source files only. The app must not invent new practice questions.
