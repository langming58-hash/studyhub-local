#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TITLE="[分享创造] 做了一个本地优先的开源学习资料管理器 StudyHub Local"
BODY_FILE="$ROOT/docs/launch/v2ex.md"

echo "StudyHub Local - V2EX launch helper"
echo
echo "Title:"
echo "$TITLE"
echo
echo "Body:"
cat "$BODY_FILE"
echo
echo "Copying V2EX body to clipboard..."
pbcopy < "$BODY_FILE"
echo "Opening V2EX 分享创造 node..."
open "https://www.v2ex.com/go/create"
echo
echo "Manual steps:"
echo "1. Log in if needed."
echo "2. Create a new topic in 分享创造."
echo "3. Paste the title and body."
echo "4. Submit only after reviewing the preview."
echo
echo "No credentials were read or stored."
read -r -p "Press Return to close this window. " _
