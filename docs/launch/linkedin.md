# LinkedIn Draft

I released StudyHub Local, an early-stage open-source local-first study material hub.

The project grew out of a practical problem: course PDFs, slides, tutorials, labs, quizzes, and code files tend to scatter across many places. StudyHub Local keeps the source of truth in a local study folder, scans it into a SQLite metadata index, and provides a localhost web UI for course/week browsing, search, practice records, and optional source-grounded AI.

The engineering focus has been privacy boundaries rather than cloud-first convenience:

- no telemetry by default
- Demo Mode works without an OpenAI API key
- server listens on localhost only
- private course files stay outside the repository
- OpenAI integration is optional and server-side
- AI answers must cite indexed source material
- practice questions are not generated
- MCP access is read-only and scoped to the configured local library

Repository: https://github.com/langming58-hash/studyhub-local

Issues, feedback, and PRs are welcome. I would especially appreciate feedback on local-first education tooling, retrieval UX, and privacy/security tradeoffs.
