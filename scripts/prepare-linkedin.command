#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/linkedin.md"

echo "StudyHub Local - LinkedIn helper"
echo
cat "$BODY_FILE"
echo
echo "Copying LinkedIn draft to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening LinkedIn feed..."
open "https://www.linkedin.com/feed/"
echo
echo "Review before posting. No credentials were read or stored."
read -r -p "Press Return to close this window. " _
