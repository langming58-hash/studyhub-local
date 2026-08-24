#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/xiaohongshu.md"
CAROUSEL_FILE="$ROOT/docs/launch/xiaohongshu-carousel.md"

echo "StudyHub Local - Xiaohongshu helper"
echo
echo "Use carousel plan:"
echo "$CAROUSEL_FILE"
echo
cat "$BODY_FILE"
echo
echo "Copying Xiaohongshu caption to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening Xiaohongshu creator publish page..."
open "https://creator.xiaohongshu.com/publish/publish"
echo
echo "Use only synthetic screenshots from docs/assets/."
echo "No real academic screenshots."
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
