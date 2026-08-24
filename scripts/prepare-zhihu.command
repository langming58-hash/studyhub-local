#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BODY_FILE="$ROOT/docs/launch/zhihu.md"

echo "StudyHub Local - Zhihu helper"
echo
cat "$BODY_FILE"
echo
echo "Copying Zhihu article draft to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening Zhihu article editor..."
open "https://zhuanlan.zhihu.com/write"
echo
echo "Review before publishing. No credentials were read or stored."
read -r -p "Press Return to close this window. " _
