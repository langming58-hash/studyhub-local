#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE="StudyHub Local - a local-only study material hub, not a typical LAN-hosted app"
BODY_FILE="$ROOT/docs/launch/reddit-selfhosted.md"

echo "StudyHub Local - Reddit r/selfhosted helper"
echo
echo "Status: SKIP for direct launch unless you find an approved New Project Megathread or moderator guidance."
echo "Reason: StudyHub Local is intentionally localhost-only and refuses LAN/public binding."
echo
echo "Title:"
echo "$TITLE"
echo
echo "Body:"
cat "$BODY_FILE"
echo
read -r -p "Open r/selfhosted submit page anyway? Type YES to continue: " ANSWER
if [[ "$ANSWER" == "YES" ]]; then
  pbcopy < "$BODY_FILE"
  open "https://www.reddit.com/r/selfhosted/submit?type=TEXT"
  echo "Body copied. Review community rules and use an approved megathread if required."
else
  echo "Not opened. This is the recommended default."
fi
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
