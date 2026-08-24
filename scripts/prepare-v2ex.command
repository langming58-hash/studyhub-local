#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE_FILE="$ROOT/docs/launch/clipboard/v2ex-title.txt"
BODY_FILE="$ROOT/docs/launch/v2ex.md"
REPO_URL="https://github.com/langming58-hash/studyhub-local"
RELEASE_URL="https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.4"

echo "StudyHub Local - V2EX launch helper"
echo
echo "Running launch privacy check..."
cd "$ROOT"
python3 bin/privacy_check.py
echo
echo "Title:"
cat "$TITLE_FILE"
echo
echo "Repository URL:"
echo "$REPO_URL"
echo
echo "Release:"
echo "$RELEASE_URL"
echo
echo "Body:"
cat "$BODY_FILE"
echo
echo "Copying V2EX title to clipboard..."
pbcopy < "$TITLE_FILE"
echo "Opening V2EX 分享创造 node..."
open "https://www.v2ex.com/go/create"
echo
echo "Manual steps:"
echo "1. Log in if needed."
echo "2. Create a new topic in 分享创造."
echo "3. Paste the title from the clipboard."
echo "4. Return to this Terminal window and press Return to copy the body."
echo "5. Paste the body and submit only after reviewing the preview."
echo
read -r -p "After pasting the title, press Return to copy the V2EX body. " _
pbcopy < "$BODY_FILE"
echo "V2EX body copied to clipboard."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
