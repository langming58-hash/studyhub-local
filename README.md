# StudyHub Local

A private, local-first study hub for organizing course materials and using AI with source-grounded academic context.

StudyHub Local keeps original course files on your computer as the source of truth. It scans a local folder, builds a small SQLite metadata index, lets you browse by course and week, and optionally connects to the OpenAI API from the server side for source-cited study help.

This project was originally built for a university Canvas workflow. It is not affiliated with or endorsed by any university or learning-management-system provider.

## Features

- Course -> Week -> Course Materials / Exercises organization
- Local file browser with previews, search, starring, upload/copy, and open-original actions
- Exercise categories for Tutorial, Workshop, Quiz, Lab, Practice, and Review material
- SQLite metadata, extracted text, file hashes, and incremental indexing
- Ask GPT interface with narrow context by default: current question, current file, current week, then current course
- Optional OpenAI Responses API and vector-store integration
- Source citations with course, week, file, and page/slide/question when reliably available
- Question safety: StudyHub Local must not generate new practice questions
- Read-only MCP endpoint for local integrations
- Demo mode with synthetic fixtures and no private study files

## Screenshots

Use demo mode before creating screenshots:

```bash
cp .env.example .env.local
npm run dev
```

Only publish screenshots that show synthetic courses such as `TEST1001`, `TEST2001`, or `TEST3001`.

## Architecture

StudyHub Local is intentionally simple:

```text
Local study folder
        |
        v
Filesystem scanner -> SQLite metadata index -> localhost web UI
        |                       |
        v                       v
Extracted text cache      Ask GPT / MCP / search
        |
        v
Optional OpenAI vector store
```

The local folder remains authoritative. SQLite, extracted text, and vector stores are retrieval layers only.

## Quick Start

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
cp .env.example .env.local
npm install
npm run dev
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

`npm install` is intentionally lightweight. The app currently uses Python standard-library backend code and static frontend files.

StudyHub Local is designed for loopback access only. It refuses non-loopback `HOST` values such as `0.0.0.0` at startup and rejects non-loopback HTTP `Host` headers on every request. Do not expose it with ngrok, Cloudflare Tunnel, port forwarding, reverse proxies, or public hosting unless you have performed a separate security review for your own deployment.

## Demo Mode

Demo mode is enabled in `.env.example`:

```text
DEMO_MODE=true
DATABASE_PATH=./data/studyhub.sqlite
```

When `DEMO_MODE=true` and no `STUDY_LIBRARY_PATH` is set, the app scans `./demo-data`. The included demo files are synthetic and safe for public testing.

Demo mode shows:

- Courses and Week 01-12 structure
- Course Materials
- Tutorial, Workshop, Lab, and Quiz examples
- Search
- Ask GPT UI
- Practice questions from synthetic teacher-style files
- A synthetic Wrong Questions record

## Adding Your Own Files

Set your local library path in `.env.local`:

```text
DEMO_MODE=false
STUDY_LIBRARY_PATH=~/StudyLibrary
DATABASE_PATH=./data/studyhub.sqlite
```

Recommended folder structure:

```text
StudyLibrary/
├── TEST1001 - Example Course/
│   ├── Week 01/
│   │   ├── 01 Course Materials/
│   │   │   └── Lecture/
│   │   └── 02 Exercises/
│   │       ├── Tutorial/
│   │       ├── Workshop/
│   │       ├── Quiz/
│   │       └── Lab/
│   └── Week 02/
└── TEST2001 - Another Course/
```

Supported readable formats include PDF, DOCX, PPTX, TXT, CSV, Python, R, and IPYNB where local extraction support is available.

## Optional OpenAI Setup

OpenAI is optional. Without an API key, the app still runs locally with course browsing, metadata search, source previews, practice records, and demo mode.

To enable Ask GPT with the OpenAI API:

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4
```

Then run:

```bash
npm run study:scan
npm run study:ai-sync
```

Rules:

- Keep `OPENAI_API_KEY` only in `.env.local` or your server environment
- Never put API keys in frontend code, screenshots, issues, databases, logs, or GitHub
- Vector-store upload is opt-in and should only be used with materials you are authorized to process
- OpenAI/vector indexing sends selected indexed file content and safe metadata to OpenAI for retrieval
- Safe metadata excludes local absolute paths, local database paths, cache paths, and provider IDs from user-facing responses

## Ask GPT

Ask GPT uses the narrowest reasonable context:

1. Current Question
2. Current File
3. Current Week
4. Current Course

Every course-related answer should be grounded in indexed materials. If no source is found, the app returns:

```text
I couldn't find this in the currently indexed official course materials.
```

## Question Safety

StudyHub Local must never generate new practice questions.

If a user asks for practice questions and none are detected in indexed teacher-provided or demo teacher-style files, the app returns:

```text
No suitable teacher-provided question was found in the indexed official course materials.
```

If an official solution exists, it is labeled `Official Teacher Solution`. If no solution exists, AI reasoning must be labeled separately.

## MCP

The read-only MCP endpoint is available for local integrations. It is designed to:

- Listen on localhost
- Restrict file access to the configured study library
- Deny path traversal
- Reject hostile non-loopback `Host` headers on GET and POST requests
- Require same-origin CSRF protection for HTTP POST requests
- Avoid returning local absolute paths or provider IDs
- Expose read-only tools such as list, search, fetch, and question lookup

Remote MCP exposure is not enabled by default. Do not expose the MCP endpoint through ngrok, Cloudflare Tunnel, port forwarding, a reverse proxy, or public hosting as a convenience shortcut.

## Development

```bash
npm run lint
npm run test
npm run build
npm run ci
```

The test suite uses synthetic fixtures and mocked OpenAI behavior. CI must not require a real `OPENAI_API_KEY`.

## Privacy

StudyHub Local has no telemetry by default. It does not ship with real course materials and does not host or distribute university content.

Never commit:

- Private course files
- Teacher questions or official solutions from real courses
- Personal answers, wrong-question records, or study logs
- Live SQLite databases, extracted text caches, embeddings, or vector metadata
- `.env.local`, API keys, cookies, OAuth tokens, or session files
- Local absolute paths or usernames

For extra local-only deny markers, copy `.privacy.example.json` to `.privacy.local.json`. Keep `.privacy.local.json` untracked.

## Copyright

Users are responsible for ensuring they have the right to store, index, and optionally upload their own course materials. This project does not provide, host, or distribute copyrighted course content and does not bypass login, MFA, DRM, or access controls.

## Roadmap

- Cleaner importer adapters for different learning-management systems
- More robust document extraction adapters
- Optional packaged desktop launcher
- Broader automated UI tests
- Better export and backup tooling for user-owned metadata

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md). Use synthetic fixtures only. Do not attach private course files, API keys, or personal study data to issues or pull requests.

## License

MIT. See [LICENSE](LICENSE).
