# Show HN: StudyHub Local - a local-first study hub with source-grounded AI

Hi HN,

I built StudyHub Local, an early-stage open-source project for organizing course materials on your own machine. It is designed around a simple Course -> Week -> Materials / Exercises structure, with a clean first run, local search, optional server-side OpenAI integration, source citations, teacher-provided question safety, and a read-only MCP endpoint for local integrations.

The backend is intentionally small: Python standard library plus a SQLite metadata index and static frontend files. Local organization, preview, search, notes, and study records work without an OpenAI API key.

The privacy model is the main design constraint:

- local files remain authoritative
- no telemetry by default
- localhost-only by default
- private course files stay outside the repository
- OpenAI/vector indexing is optional and explicit
- active HTML/SVG previews are isolated as untrusted content

Repository: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.2.0

Issues, feedback, and PRs are welcome. I am especially interested in feedback on the local-first architecture, security boundaries, document extraction, and onboarding flow.
