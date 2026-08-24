# Install Support Notes

Use this guide when first users hit setup problems. Keep all examples synthetic and avoid asking people to share private course material.

## macOS

Recommended path:

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
cp .env.example .env.local
npm install
npm run dev
```

Then install locally and open:

```text
http://127.0.0.1:8765
```

If macOS blocks the `.command` launcher, start from Terminal with `npm run dev`.

## Windows

Windows has not been fully polished yet. The expected path is:

- install Git
- install Python 3
- install Node.js
- clone the repository
- copy `.env.example` to `.env.local`
- run `npm install`
- run `npm run dev`

If this fails, ask for the exact command output with any private paths redacted.

## Linux

Expected path:

- install Git, Python 3, and Node.js
- clone the repository
- copy `.env.example` to `.env.local`
- run `npm install`
- run `npm run dev`

The app should bind to loopback only.

## Port Already in Use

If `8765` is already occupied, use another loopback port:

```bash
python3 server.py serve --port 8876
```

Then install locally and open:

```text
http://127.0.0.1:8876
```

Do not close unrelated local processes unless you know what they are.

## Python Not Found

Install Python 3, then check:

```bash
python3 --version
```

If the command is missing, the install path is not available to the shell.

## npm Not Found

Install Node.js, then check:

```bash
npm --version
```

StudyHub Local uses npm scripts as a simple task runner.

## Demo Mode Not Showing

Check `.env.local`:

```text
DEMO_MODE=true
```

If `STUDY_LIBRARY_PATH` is set, unset it temporarily to use the bundled synthetic demo fixtures.

## Reset Demo Mode Database

Stop the server, then move the local runtime database aside. Do not commit runtime database files.

Example:

```bash
mv data/studyhub.sqlite data/studyhub.sqlite.bak
npm run dev
```

## Connect Your Own StudyLibrary

Edit `.env.local`:

```text
DEMO_MODE=false
STUDY_LIBRARY_PATH=~/StudyLibrary
```

Keep real course files outside the repository.

## OpenAI Not Configured

This is normal. The app works without an OpenAI API key. OpenAI is only needed for optional Ask GPT/vector retrieval features.

If enabled, keep the key server-side in `.env.local` and never paste it into issues, screenshots, or frontend code.

## What to Ask Users For

- operating system
- Python version
- Node/npm version
- command run
- error output with private paths redacted
- whether Demo Mode is enabled

Do not ask users to upload private course files.
