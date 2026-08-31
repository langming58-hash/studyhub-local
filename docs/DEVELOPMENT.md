# Development

## Setup

```bash
cp .env.example .env.local
npm install
npm run dev
```

## Checks

```bash
npm run lint
npm run test
npm run build
npm run ci
```

## Fixture Policy

Use only synthetic fixture files under `tests/fixtures/` or a temporary test
directory. Fixtures may be injected by tests, but must never be added to Tauri
resources or any production runtime. Do not use real course materials, teacher
questions, official solutions, private answers, or screenshots that show
personal data.

Run `python3 bin/i18n_acceptance.py` when changing interface copy. English and
Simplified Chinese catalogs must keep identical key sets.

## Branches

Use short feature branches and focused pull requests. Include privacy/security impact in every PR.
