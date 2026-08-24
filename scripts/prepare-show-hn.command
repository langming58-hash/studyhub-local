#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE_FILE="$ROOT/docs/launch/clipboard/show-hn-title.txt"
URL_FILE="$ROOT/docs/launch/clipboard/show-hn-url.txt"
COMMENT_FILE="$ROOT/docs/launch/clipboard/show-hn-first-comment.txt"

echo "StudyHub Local - Show HN launch helper"
echo
echo "Title:"
cat "$TITLE_FILE"
echo
echo "Repository URL:"
cat "$URL_FILE"
echo
echo "First comment:"
cat "$COMMENT_FILE"
echo
echo "Copying HN title to clipboard..."
pbcopy < "$TITLE_FILE"
echo "Opening Hacker News submit page..."
open "https://news.ycombinator.com/submit"
echo
echo "Manual steps:"
echo "1. Paste the title."
echo "2. Paste the repository URL."
echo "3. Click Submit yourself."
echo "4. After the post is live, add the first comment from docs/launch/show-hn-first-comment.md."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
