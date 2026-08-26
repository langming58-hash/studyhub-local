# Preview Matrix

StudyHub separates the visual preview shown in the main document pane from the
readable text used by search, AI grounding, citations, and the Readable Text tab.

Generated preview derivatives are runtime cache only. They must not be written
back into the user's study folder.

| File type | Primary visual preview | Fallback preview | Readable text extraction | Optional dependency |
| --- | --- | --- | --- | --- |
| PDF | Browser PDF viewer from original file | Original file can still be opened | `pdftotext` by page when available | Poppler |
| PPTX / PPT | Cached PDF generated locally by LibreOffice | Clear unavailable state plus Readable Text tab | PPTX slide XML by slide and paragraph; legacy PPT text extraction is limited | LibreOffice |
| DOCX / DOC | Cached PDF generated locally by LibreOffice | Clear unavailable state plus Readable Text tab | DOCX paragraph XML; legacy DOC text extraction is limited | LibreOffice |
| XLSX / XLS | Not promoted to PDF yet | Clear unavailable state plus Open Original | Future workbook/sheet-aware extraction | Future work |
| Images | Browser image preview from original file | Open Original | No text extraction by default | None |
| TXT / MD / CSV | Escaped text preview | Open Original | Direct text read | None |
| Python / R / IPYNB | Escaped code/text preview | Open Original | Source/cell text read | None |
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
