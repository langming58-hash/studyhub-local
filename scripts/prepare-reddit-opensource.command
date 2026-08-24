#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE="I built StudyHub Local, an open-source local-first course material hub with source-grounded AI"
BODY_FILE="$ROOT/docs/launch/reddit-opensource.md"

echo "StudyHub Local - Reddit r/opensource helper"
echo
echo "Rule note: use Promotional flair, avoid drive-by posting, and engage with comments."
echo
echo "Title:"
echo "$TITLE"
echo
echo "Body:"
cat "$BODY_FILE"
echo
echo "Copying r/opensource body to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening Reddit submit page..."
open "https://www.reddit.com/r/opensource/submit?type=TEXT"
echo
echo "Manual steps:"
echo "1. Log in if needed."
echo "2. Paste the title and body."
echo "3. Select Promotional flair if available."
echo "4. Review subreddit rules again in the UI."
echo "5. Click Post yourself."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
