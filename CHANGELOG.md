# Changelog

## Unreleased

- Redesign the main product IA around Home, Courses, Search, Study, AI, and Settings.
- Add a fuller AI study workspace with saved local conversation history.
- Improve first-run onboarding, recovery notices, and dependency preflight checks.
- Add extraction retry/reindex behavior for files whose readable text was missing.
- Use a verified CA bundle for OpenAI HTTPS requests when `certifi` is available.
- Render AI Markdown and academic math locally with KaTeX.
- Simplify returning-user Home, course rows, file rows, preview toolbar, and upload labels.
- Add hash-based routing for course, week, file preview, AI, Search, Study, and Settings refresh/back behavior.
- Keep preview embedding same-origin while preserving active-content isolation.

## v0.1.5

- Hide stale empty courses after switching StudyLibrary folders.
- Tighten AI no-match behavior so unrelated teacher questions are not surfaced.
- Make Notes discoverable and persistent in the UI.
- Improve plain-text official solution heading parsing.
- Sanitize solution metadata responses.
- Fix a retest privacy regression so internal text-cache paths are not exposed in solution payloads.
- Add P2 regression acceptance coverage.
- Confirm the first-time-user retest passes without the previous P2 friction.

## v0.1.4

- Isolate active-content previews for HTML, HTM, XHTML, XML, and SVG.
- Render untrusted active files as escaped plaintext instead of executable same-origin pages.
- Preserve preview usability while keeping `nosniff` and restrictive content-security behavior.
- Add security acceptance coverage for active-content preview isolation.

## v0.1.3

- Enforce exact same-origin validation for mutating browser requests.
- Reject cross-port localhost origins, loopback hostname mismatches, and HTTP/HTTPS scheme mismatches.
- Preserve CSRF enforcement for requests without an Origin header.
- Keep Host validation, DNS-rebinding protection, `/api/session` bootstrap, and localhost-only defaults.

## v0.1.2

- Add universal loopback Host-header validation across HTTP methods.
- Strengthen DNS-rebinding protection for API, preview, and MCP routes.
- Move CSRF bootstrap out of `/api/health` and into `/api/session`.
- Update documentation for localhost-only security behavior.

## v0.1.1

- Add privacy and security hardening after the first public release.
- Improve CSRF, same-origin, upload, path containment, and request-size checks.
- Reduce exposure of local paths and provider identifiers in user-facing responses.
- Expand privacy and security acceptance coverage.

## v0.1.0

- Prepare the first public edition as StudyHub Local.
- Add synthetic demo data for local-first study workflows.
- Include course/week browsing, search, preview, optional source-grounded AI, and read-only MCP behavior.
- Add privacy-first docs, license, contribution guide, security policy, and CI.
- Exclude private course materials, runtime databases, extracted text, logs, and secrets.
