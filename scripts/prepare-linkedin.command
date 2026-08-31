#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/clipboard/linkedin-post.txt"
MEDIA_FILE="$ROOT/docs/assets/screenshots/first-run-en.png"
REPO_URL="https://github.com/langming58-hash/studyhub-local"

echo "StudyHub Local - LinkedIn helper"
echo
echo "Running launch privacy check..."
cd "$ROOT"
python3 bin/privacy_check.py
echo
echo "Repository URL:"
echo "$REPO_URL"
echo
echo "Suggested media:"
echo "$MEDIA_FILE"
echo
cat "$BODY_FILE"
echo
echo "Copying LinkedIn draft to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening LinkedIn composer..."
open "https://www.linkedin.com/feed/?shareActive=true"
echo "Revealing the approved clean-workspace screenshot in Finder..."
open -R "$MEDIA_FILE"
echo
echo "Manual steps:"
echo "1. Log in or verify if LinkedIn asks."
echo "2. Paste the post text."
echo "3. Optionally attach the clean-workspace screenshot shown in Finder."
echo "4. Review the preview."
echo "5. Click Post yourself."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
