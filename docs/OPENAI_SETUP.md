# OpenAI Setup

OpenAI is optional. The app works without an API key.

## Configure

Copy `.env.example` to `.env.local` and set:

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4
```

Keep `.env.local` private.

## Sync

```bash
npm run study:scan
npm run study:ai-sync
```

The sync process uses hashes so unchanged files are not uploaded again. Changed files are detected and indexed as new versions.

## Safety

- Do not expose API keys to the frontend.
- Do not commit vector-store IDs tied to private content.
- Do not upload materials unless you are authorized to process them.
- Mock OpenAI tests should be used in CI.
