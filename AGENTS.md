# StudyHub Local Agent Rules

## Read Before Substantial Work

- [Engineering Constitution](docs/ENGINEERING_CONSTITUTION.md)
- [Current and Target Architecture](docs/ARCHITECTURE.md)
- [Privacy](docs/PRIVACY.md) and [Security](SECURITY.md)
- [Design and Interaction Constitution](docs/design/DESIGN_SYSTEM_PLAN.md)
- the focused current-state document and acceptance tests for the area changed

Clearly label CURRENT, IMPLEMENTED, TARGET, PROPOSED, and HISTORICAL claims.
Never present target vocabulary or donor candidates as shipped behavior.

## Non-Negotiable Boundaries

- Inspect existing code, docs, tests, and git state before editing.
- Reuse mature infrastructure before building commodity replacements; audit
  license, provenance, maintenance, security, and the adapter boundary first.
- Keep real academic materials and all derived private content outside Git.
- Preserve original source files and formats. SQLite, previews, extraction, and
  vector stores are derived/retrieval layers.
- Never invent teacher-style practice questions.
- Keep LMS access authorized, read-only, and separate from ingestion/domain
  persistence.
- Do not expose secrets, credentials, provider IDs, personal identifiers, local
  absolute paths, runtime databases, caches, logs, or user study state.
- Avoid drive-by refactors. Make the smallest safe, reversible change.
- Do not let external donor APIs leak broadly through StudyHub domain code.

## Verification

- Use synthetic fixtures only.
- Run `python3 bin/privacy_check.py` before staging or pushing.
- Run `npm run ci` for normal PRs.
- Run focused acceptance scripts for changed behavior, including scanner,
  bridge/MCP, Ask AI, preview, security, product, or packaged desktop checks as
  applicable.
- Confirm documentation links and CURRENT/TARGET claims when changing docs.
- Use the repository's privacy-safe maintainer identity / GitHub noreply email
  for public commits.
