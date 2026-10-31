# Preview Matrix

Status: **CURRENT / IMPLEMENTED FILE-TYPE POLICY**.

StudyHub separates the visual preview shown in the main document pane from the
readable text used by search, AI grounding, citations, and the Readable Text tab.

Generated preview derivatives are runtime cache only. They must not be written
back into the user's study folder.

| File type | Primary visual preview | Fallback preview | Readable text extraction | Optional dependency |
| --- | --- | --- | --- | --- |
| PDF | StudyHub Reader using local PDF.js 6.4.299 | Safe extracted text, then Open Original | `pdftotext`, MarkItDown fallback, then bounded on-device OCR | Poppler optional; bundled mac-ocr fallback on macOS |
| PPTX / PPT | Cached PDF generated locally by LibreOffice, rendered by PDF.js | Readable Text, then Open Original | MarkItDown for PPTX with built-in structured fallback; legacy PPT remains limited | LibreOffice optional |
| DOCX / DOC | Cached PDF generated locally by LibreOffice, rendered by PDF.js | Readable Text, then Open Original | MarkItDown for DOCX with built-in structured fallback; legacy DOC remains limited | LibreOffice optional |
| XLSX / XLS | Readable Text in the StudyHub Reader | Open Original | MarkItDown workbook extraction for XLSX; legacy XLS remains limited | None for XLSX |
| Images | StudyHub image reader from the authorized original | Open Original | Bounded on-device OCR fallback on macOS | Bundled mac-ocr |
| TXT / MD / CSV | Escaped Reader text | Open Original | Direct text read | None |
| Python / R / IPYNB | Escaped text with local Prism highlighting | Open Original | Source/cell text read | None |
| HTML / SVG / XML | Escaped text preview only | Open Original | Direct text read | None |
| Unknown binary | Clear unavailable state | Open Original | None | None |

## Office Conversion Policy

Office files are untrusted input. Conversion uses local LibreOffice/`soffice`
when available, with:

- argv-based subprocess calls, not shell interpolation
- isolated temporary LibreOffice profile and output directories
- timeout handling
- per-file conversion locking
- cache keys based on file id, source hash, extension, and converter version
- atomic promotion into runtime cache

The original Office document remains the source of truth. The cached PDF is only
a visual preview derivative.

## Reader Security And Cache Policy

- PDF.js, Prism, and their workers/assets are packaged locally; Reader has no
  CDN or runtime asset fetch.
- Reader accepts a StudyHub `file_id`, never a browser-provided filesystem path
  or arbitrary URL.
- PDF script execution/XFA are disabled and no PDF annotation link layer is
  mounted in V1.
- extracted Markdown/HTML is displayed as text, never injected as active HTML;
  DOMPurify is therefore not required by the current rendering policy.
- mac-ocr accepts only allowlisted local files, has a 50 MiB input limit,
  60-page PDF limit, 90-second timeout, and never receives URL input. OCR and
  conversion derivatives live in runtime cache.
- cache identity includes source digest plus extraction/converter version.
- Reader DOM is not an AI source. Search, citations, and Ask continue to use
  backend extraction chunks and provenance.
