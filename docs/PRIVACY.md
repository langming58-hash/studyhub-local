# Privacy

StudyHub Local is designed for private local study workflows.

## Stays Local By Default

- Original course files
- SQLite metadata
- Extracted text cache
- Notes, stars, attempts, and wrong-question records
- Logs

These paths are ignored by Git and should not be uploaded to issues or pull requests.

## Optional Cloud AI

OpenAI integration is optional and server-side only. When enabled, selected extracted content may be uploaded to a vector store for retrieval. Users are responsible for confirming they have permission to process those materials with any cloud provider.

## No Telemetry

The project does not include analytics or telemetry by default.

## Public Fixture Rule

Public examples, tests, screenshots, and docs must use synthetic materials only.

## Public Identity Boundary

Public project documentation must not act as a directory linking the
maintainer's personal social accounts unless that linkage is intentionally part
of the public professional profile.

Do not record direct social post URLs, private messages, login state, account
eligibility, account age, platform verification details, or exact personal
publication timestamps in this repository. Social publishing status belongs
outside the code repository.

## Local Privacy Markers

The public privacy checker is generic and does not contain user-specific names, course codes, university names, or local paths.

If you want extra deny markers for your own machine, copy `.privacy.example.json` to `.privacy.local.json` and add only your private markers there. `.privacy.local.json` is ignored by Git and must never be committed.
