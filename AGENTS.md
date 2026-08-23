# StudyHub Local Agent Rules

## Non-Negotiable Boundaries

- Keep real study materials outside the repository.
- Treat the configured `STUDY_LIBRARY_PATH` as the only local study-library root.
- Local course files are the source of truth.
- SQLite, extracted text, and vector stores are retrieval layers only.
- NEVER INVENT PRACTICE QUESTIONS.
- Do not commit, push, upload, summarize into Git, or otherwise expose teacher files, extracted text, private answers, wrong-question records, SQLite runtime data, cache files, logs, or secrets.
- Do not convert normal lecture notes, worksheets, PDFs, Word documents, or slides into programming formats unless the original exercise genuinely requires code.
- Preserve original source file formats.
- Learning-management-system access must remain authorized and read-only.

## Development Workflow

- Inspect existing files before editing.
- Keep changes small and reversible.
- Run `python3 bin/privacy_check.py` before staging or pushing.
- Run `python3 server.py scan`, `python3 server.py verify`, and `python3 server.py health` when changing scanner/server behavior.
- Run `python3 bin/bridge_acceptance.py` when changing indexing, MCP, or practice behavior.
- Run `python3 bin/askgpt_acceptance.py` when changing Ask GPT behavior.
- Use meaningful commits that describe the real change.
- Public commits must use the repository's privacy-safe maintainer identity / GitHub noreply email. Never commit with a personal email address.

## Privacy

- Public examples must use synthetic fixtures only.
- Never commit `.env.local`.
- Never expose `OPENAI_API_KEY`.
- Never put real course content in documentation examples, screenshots, tests, issues, or pull requests.
- Do not include local absolute paths, usernames, OpenAI vector-store IDs tied to private material, OpenAI file IDs, cookies, OAuth tokens, or session data.
