# Security Policy

## Reporting

Please report security issues privately to the repository maintainer. Do not include API keys, course files, cookies, OAuth tokens, session values, or personal study data in public issues.

## Local-First Model

StudyHub Local is designed to run on `127.0.0.1`. It should not be exposed to the public internet without an additional security review.

## API Keys

OpenAI keys must remain server-side in `.env.local` or an equivalent private environment. They must not be stored in frontend code, GitHub, SQLite databases, screenshots, logs, or issue attachments.

## File Access

The scanner and MCP tools should only access the configured `STUDY_LIBRARY_PATH`. Path traversal and arbitrary filesystem reads are not allowed.

## Academic Materials

Do not upload private or copyrighted course content to issues, pull requests, tests, or demo fixtures.
