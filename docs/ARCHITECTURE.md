# Architecture

StudyHub Local is a local-first study library.

## Components

- `server.py`: localhost HTTP server, scanner, SQLite schema, search, AI request handling, OpenAI sync, and read-only MCP endpoint.
- `static/`: static frontend served by the local backend.
- `demo-data/`: synthetic fixtures for public demos and tests.
- `data/`: runtime SQLite files, ignored by Git.
- `cache/`: extracted text cache, ignored by Git.
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

## Localhost Boundary

The server listens on `127.0.0.1` by default. Do not deploy it on a public host without a separate security review.

## Question Safety

Practice questions are retrieved from indexed source files only. The app must not invent new practice questions.
