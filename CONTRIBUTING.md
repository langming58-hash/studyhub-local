# Contributing

Thanks for helping improve StudyHub Local.

Before substantial implementation, read the
[Engineering Constitution](docs/ENGINEERING_CONSTITUTION.md),
[Architecture](docs/ARCHITECTURE.md), [Privacy](docs/PRIVACY.md), and
[Security](SECURITY.md). Design changes also follow the
[Design and Interaction Constitution](docs/design/DESIGN_SYSTEM_PLAN.md).

## Setup

```bash
cp .env.example .env.local
npm install
npm run dev
```

## Before Opening a PR

```bash
npm run ci
```

## Pull Request Expectations

- Keep changes focused.
- Distinguish CURRENT behavior from TARGET or PROPOSED architecture.
- Reuse mature infrastructure before implementing commodity replacements;
  document license and adapter boundaries for new donor code.
- Include tests for behavior changes.
- Use synthetic fixtures only.
- Describe privacy and security impact.
- Do not include real course materials, teacher questions, official solutions, private answers, `.env.local`, API keys, cookies, logs, SQLite databases, extracted text, vector-store metadata, or local absolute paths.

## Issues

Do not attach private course files, API keys, personal study data, screenshots with identifying information, or copyrighted academic materials.
