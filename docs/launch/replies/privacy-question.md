# Privacy Question

The normal workflow stays on your machine. StudyHub Local scans a local folder, builds a local SQLite metadata index, and serves the UI on localhost.

There is no telemetry by default. OpenAI is optional. If you explicitly enable vector indexing, selected file content is sent to OpenAI for retrieval; otherwise the local browsing/search workflow does not require it.

Real course files should stay outside the repository. Synthetic fixtures are test-only and excluded from the production app bundle.
