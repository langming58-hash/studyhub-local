#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/x.md"

echo "StudyHub Local - X helper"
echo
cat "$BODY_FILE"
echo
echo "Copying X draft file to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening X compose page..."
open "https://x.com/compose/post"
echo
echo "Use either the single post or the optional thread. Review character limits before posting."
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
