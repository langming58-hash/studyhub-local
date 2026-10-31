# Third-Party Notices

StudyHub Local includes or packages the following Reader V1 dependencies. The
package manifests and lockfiles are authoritative for adopted versions.

| Component | Version | License | Use |
| --- | --- | --- | --- |
| Mozilla PDF.js (`pdfjs-dist`) | 6.4.299 | Apache-2.0 | Local PDF rendering and text layers |
| PrismJS | 1.30.0 | MIT | Local, non-executing code syntax highlighting |
| Microsoft MarkItDown | 0.1.9 | MIT | Local DOCX, PPTX, XLSX and PDF fallback extraction |
| mac-ocr | 1.1.1 | MIT | Optional on-device Apple Vision OCR fallback |

PDF.js and Prism license texts are copied into the generated Reader static
assets. The mac-ocr license is packaged next to its native binary. Python
package license metadata is retained by the packaged backend build.

No dependency above is permitted to replace StudyHub provenance, ownership,
source-grounding, cache, or filesystem authorization rules.
