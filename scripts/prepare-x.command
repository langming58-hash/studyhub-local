#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/x.md"
SHORT_FILE="$ROOT/docs/launch/clipboard/x-short-post.txt"

echo "StudyHub Local - X helper"
echo
echo "Running launch privacy check..."
cd "$ROOT"
python3 bin/privacy_check.py
echo
cat "$BODY_FILE"
echo
echo "Copying X short post to clipboard..."
pbcopy < "$SHORT_FILE"
echo "Opening X compose page..."
open "https://x.com/compose/post"
echo
echo "Use either the short post from the clipboard or the optional thread in docs/launch/x.md. Review character limits before posting."
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
