# Security Policy

## Reporting

Please report security issues privately to the repository maintainer. Do not include API keys, course files, cookies, OAuth tokens, session values, or personal study data in public issues.

## Local-First Model

StudyHub Local is designed to run on `127.0.0.1`, `localhost`, or `::1` only. The server refuses non-loopback bind hosts. Do not expose a live StudyHub Local instance through ngrok, Cloudflare Tunnel, port forwarding, reverse proxies, or public hosting without a separate security review.

## API Keys

OpenAI keys must remain server-side in `.env.local` or an equivalent private environment. They must not be stored in frontend code, GitHub, SQLite databases, screenshots, logs, or issue attachments.

OpenAI/vector indexing is opt-in. When enabled, selected indexed file content and safe academic metadata are sent to OpenAI for retrieval. Local absolute paths, local database paths, cache paths, and provider IDs should not be returned through public-facing API or MCP responses.

## File Access

The scanner, previews, upload flow, open-original action, Ask GPT, and MCP tools should only access the configured `STUDY_LIBRARY_PATH`. Path traversal and arbitrary filesystem reads are not allowed. Upload targets are built from validated server-side components and copied by temporary file plus atomic rename.

## Browser/API Boundary

Mutating HTTP routes require a per-process CSRF token and same-origin loopback headers. API responses include security headers, deny framing, disable referrer leakage, and use `Cache-Control: no-store` for API/MCP responses.

## MCP Boundary

MCP is read-only. It may list/search/fetch indexed study-library content by safe file IDs, but must not expose local absolute paths, local cache paths, database paths, OpenAI provider file IDs, vector-store IDs, write tools, delete tools, shell execution, app-opening actions, or indexing/upload actions.

## Academic Materials

Do not upload private or copyrighted course content to issues, pull requests, tests, or demo fixtures.
