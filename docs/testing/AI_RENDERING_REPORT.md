# AI Rendering Acceptance Report

Date: 2026-08-25

## Scope

This pass fixes the AI answer rendering pipeline for Markdown and academic mathematics.

Canonical storage remains raw assistant/user text:

```text
Markdown prose + TeX math source
```

Rendering happens only at display time in the browser. No conversation-history database migration is required.

## Dependency Review

| Package | Version | License | Use | Runtime impact |
| --- | --- | --- | --- | --- |
| `katex` | `0.18.4` | MIT | Local TeX math rendering for AI messages | Served locally from `node_modules/katex/dist`; no CDN or remote font request |

No new Markdown parser or sanitizer dependency was added. StudyHub keeps a small purpose-built Markdown renderer that escapes model prose by default, disables raw HTML, validates links, keeps code blocks out of math rendering, and passes math expressions to KaTeX with `trust: false`.

## Acceptance Coverage

`bin/ai_rendering_acceptance.py` covers synthetic examples for:

- inline math with `$...$`
- display math with `$$...$$`
- legacy `\(...\)` and `\[...\]`
- conservative naked TeX recovery such as `\frac{dN}{dt}`
- Chinese + English + mathematics
- subscripts and superscripts
- derivatives, integrals, matrices, lists, and tables
- economics currency such as `$10 to $12`
- code blocks and inline code
- underscore filenames
- malformed formula fallback
- hostile Markdown/HTML/XSS-style input
- existing raw conversation-history rendering
- long display equation containment
- OpenAI math-format prompt contract

## Browser Visual Check

Headless Chrome rendered a screenshot-like synthetic answer at:

```text
1600
1440
1280
1024
900
768
```

Result: PASS.

Visible page text did not expose raw:

```text
\frac
\qquad
\[
\]
```

Display equations stayed inside their formula containers without causing whole-page horizontal overflow.

## Accessibility Note

KaTeX is configured to output HTML + MathML. Browser accessibility-tree sanity confirmed rendered math is present without breaking keyboard/page flow.

This is not a full human screen-reader audit and should not be described as formal WCAG compliance.
