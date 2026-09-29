# Development

Substantial work must follow the
[Engineering Constitution](ENGINEERING_CONSTITUTION.md) and preserve the
CURRENT/TARGET distinction in [Architecture](ARCHITECTURE.md).

## Setup

```bash
npm install
npm run dev
```

`npm run dev` starts the source backend with the Development profile under the
ignored `.studyhub-dev/` tree. It does not read the Production desktop database,
settings, selected StudyLibrary, cache, or logs. Configure a synthetic or
development-only library through that running instance.

Desktop development is also isolated and visibly identified:

```bash
npm run desktop:dev
```

This command applies `src-tauri/tauri.dev.conf.json`, which gives the debug app
its own application identity, OS data/config roots, and WebView storage. Use
`npm run desktop:demo` only for synthetic Demo/Test work; external folder/file
selection and inherited OpenAI/provider configuration are disabled there.

Direct `python3 server.py` remains the explicit legacy/source workflow and
continues to use repository-local `.env.local` and runtime defaults. It is not
what `npm run dev` invokes.

## Checks

```bash
npm run lint
npm run test
npm run build
npm run ci
python3 bin/environment_isolation_acceptance.py
```

The isolation acceptance test uses temporary synthetic roots only. Never point
it at a real application-support directory, StudyLibrary, database, or config.

## Fixture Policy

Use only synthetic fixture files under `tests/fixtures/` or a temporary test
directory. Fixtures may be injected by tests, but must never be added to Tauri
resources or any production runtime. Do not use real course materials, teacher
questions, official solutions, private answers, or screenshots that show
personal data.

Run `python3 bin/i18n_acceptance.py` when changing interface copy. English and
Simplified Chinese catalogs must keep identical key sets.

## Branches

Use short feature branches and focused pull requests. Include privacy/security
impact in every PR. Infrastructure adoption also records alternatives,
licensing, the StudyHub-owned adapter boundary, migration impact, and relevant
acceptance tests.
