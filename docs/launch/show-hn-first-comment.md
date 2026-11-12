# Show HN First Comment

I originally built StudyHub Local because course PDFs, slides, tutorials, labs, and code files were spread across too many folders.

The design gradually became local-first:

- the study folder remains the source of truth
- SQLite and extracted text are just indexes
- OpenAI is optional
- without an API key the main app still works
- AI responses are intended to stay source-grounded
- MCP access is read-only
- the HTTP service intentionally refuses non-loopback exposure

The biggest engineering rabbit hole ended up being privacy/security rather than the UI: filesystem containment, CSRF, DNS rebinding protection, exact same-origin handling, active HTML/SVG preview isolation, and keeping provider IDs/private paths out of user-facing responses.

It is still early-stage. I would especially appreciate feedback on:

1. onboarding
2. document extraction
3. search quality
4. the local-first architecture
5. whether the source-grounded workflow is actually useful

The gallery uses a separate synthetic library; the app itself opens empty and works without an OpenAI API key.

Beta.2 includes PDF Reader V1, source-linked notes / PDF excerpts, manual FSRS cards, Canvas OAuth / Assignments / Today and local backup export. Apple Silicon, macOS 13+, ad-hoc signed and not notarized. Canvas requires institutional OAuth setup. Full real-GUI backup save/restore/undo acceptance remains incomplete; PR #44 PDF Highlights are excluded. This is a prerelease. See [current English copy](general-en.md) and the [real screenshot provenance](../assets/screenshots/README.md).
