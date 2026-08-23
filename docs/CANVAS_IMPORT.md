# Canvas Import

StudyHub Local can support Canvas-style workflows, but it does not include a public universal downloader.

## Principles

- Only use accounts and materials you are authorized to access.
- Do not bypass login, MFA, DRM, or access controls.
- Keep importer logic separate from the local scanner.
- Treat each learning-management-system instance as an adapter, not a hard-coded domain.

## Suggested Adapter Shape

```text
CanvasImporter
  -> authenticate through user-approved local browser/session
  -> download selected files
  -> preserve original formats
  -> write into STUDY_LIBRARY_PATH
  -> run local scan
```

The local folder and scanner continue to work without Canvas.
