#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE_FILE="$ROOT/docs/launch/clipboard/reddit-opensource-title.txt"
BODY_FILE="$ROOT/docs/launch/reddit-opensource.md"
REPO_URL="https://github.com/langming58-hash/studyhub-local"
RELEASE_URL="https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.4"

echo "StudyHub Local - Reddit r/opensource helper"
echo
echo "Running launch privacy check..."
cd "$ROOT"
python3 bin/privacy_check.py
echo
echo "Rule note: use Promotional flair, avoid drive-by posting, avoid clickbait, and engage with comments."
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
echo "Copying r/opensource title to clipboard..."
pbcopy < "$TITLE_FILE"
echo "Opening Reddit submit page..."
open "https://www.reddit.com/r/opensource/submit?type=TEXT"
echo
echo "Manual steps:"
echo "1. Log in if needed."
echo "2. Paste the title from the clipboard."
echo "3. Return to this Terminal window and press Return to copy the body."
echo "4. Paste the body."
echo "5. Select the Promotional flair if Reddit shows flair selection."
echo "6. Review subreddit rules again in the UI."
echo "7. Click Post yourself."
echo
read -r -p "After pasting the title, press Return to copy the Reddit body. " _
pbcopy < "$BODY_FILE"
echo "Reddit body copied to clipboard."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
