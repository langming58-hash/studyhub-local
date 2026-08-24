# StudyHub Local - a local-only study material hub, not a typical LAN-hosted app

I built StudyHub Local, an early-stage open-source tool for organizing course materials and using optional source-grounded AI against a local study folder.

Important clarification for this community: this is local-only by design, not a normal LAN/web self-hosted service. It intentionally refuses non-loopback binding and is designed for a user's own machine rather than remote hosting.

Features:

- Course -> Week -> Materials / Exercises browsing
- local SQLite metadata index
- local search
- synthetic Demo Mode that works without an API key
- optional server-side OpenAI retrieval with citations
- teacher-provided question safety
- read-only MCP endpoint scoped to the local library
- localhost-only bind, Host-header protection, CSRF, filesystem containment, and active-content preview isolation

The source of truth remains your own local study folder. The repository ships only synthetic demo data.

Repository: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.5

Issues, feedback, and PRs are welcome, especially around local-first security boundaries and cross-platform launcher polish.
