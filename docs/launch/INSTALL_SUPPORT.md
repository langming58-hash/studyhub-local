# Install Support Notes

Use this guide when first users hit setup problems. Keep all examples synthetic and avoid asking people to share private course material.

## macOS

Recommended path:

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
npm install
python3 -m pip install -r requirements.txt
npm run dev
```

Then open the localhost URL printed in Terminal. Default:

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
- run `npm install`
- run `python3 -m pip install -r requirements.txt`, or `py -3 -m pip install -r requirements.txt`
- run `npm run dev`

If this fails, ask for the exact command output with any private paths redacted.

## Linux

Expected path:

- install Git, Python 3, and Node.js
- clone the repository
- run `npm install`
- run `python3 -m pip install -r requirements.txt`
- run `npm run dev`

The app should bind to loopback only.

## Port Already in Use

If `8765` is already occupied, use another loopback port:

```bash
python3 server.py serve --port 8876
```

Then open:

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

## Empty First Run

With no local configuration, StudyHub opens an empty workspace. Create a course
or import a course folder from the Home screen.

Advanced check: if `.env.local` exists, confirm any configured folder is an
intended local path:

```text
STUDY_LIBRARY_PATH=~/StudyLibrary
```

StudyHub does not bundle sample courses and does not scan unrelated folders.

## Reset Local Metadata

Stop the server, then move the local runtime database aside. Do not commit runtime database files.

Example:

```bash
mv data/studyhub.sqlite data/studyhub.sqlite.bak
npm run dev
```

## Connect Your Own StudyLibrary

Use the app UI: Settings -> Study folder -> enter the local folder path -> Use
this folder -> restart -> Scan Library.

Keep real course files outside the repository.

## OpenAI Not Configured

This is normal. The app works without an OpenAI API key. OpenAI is only needed for optional Ask AI/vector retrieval features.

If enabled, keep the key server-side in `.env.local` and never paste it into issues, screenshots, or frontend code.

## What to Ask Users For

- operating system
- Python version
- Node/npm version
- command run
- error output with private paths redacted
- whether this is a clean first run or an existing library

Do not ask users to upload private course files.
