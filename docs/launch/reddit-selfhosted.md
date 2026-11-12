# StudyHub Local - a local-only study material hub, not a typical LAN-hosted app

I built StudyHub Local, an early-stage open-source tool for organizing course materials and using optional source-grounded AI against a local study folder.

Important clarification for this community: this is local-only by design, not a normal LAN/web self-hosted service. It intentionally refuses non-loopback binding and is designed for a user's own machine rather than remote hosting.

Features:

- Course -> Week -> Materials / Exercises browsing
- local SQLite metadata index
- local search
- clean first run with no bundled sample courses
- local organization, preview, search, notes, and study records without an API key
- optional server-side OpenAI retrieval with citations
- teacher-provided question safety
- read-only MCP endpoint scoped to the local library
- localhost-only bind, Host-header protection, CSRF, filesystem containment, and active-content preview isolation

The source of truth remains your own local study folder. Synthetic fixtures are test-only and excluded from the production app bundle.

Repository: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.3.0-beta.2

Issues, feedback, and PRs are welcome, especially around local-first security boundaries and cross-platform launcher polish.

Beta.2 includes PDF Reader V1, source-linked notes / PDF excerpts, manual FSRS cards, Canvas OAuth / Assignments / Today and local backup export. Apple Silicon, macOS 13+, ad-hoc signed and not notarized. Canvas requires institutional OAuth setup. Full real-GUI backup save/restore/undo acceptance remains incomplete; PR #44 PDF Highlights are excluded. This is a prerelease. See [current English copy](general-en.md) and the [real screenshot provenance](../assets/screenshots/README.md).
