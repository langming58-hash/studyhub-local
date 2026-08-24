#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

run_helper() {
  local helper="$1"
  "$ROOT/scripts/$helper"
}

while true; do
  clear
  echo "StudyHub Local Public Launch"
  echo
  echo "1. Show HN"
  echo "2. V2EX"
  echo "3. Reddit r/opensource"
  echo "4. Reddit r/selfhosted"
  echo "5. Xiaohongshu"
  echo "6. Zhihu"
  echo "7. LinkedIn"
  echo "8. X"
  echo "9. Open GitHub repo"
  echo "10. Open v0.1.4 Release"
  echo "11. Run launch privacy check"
  echo "12. Exit"
  echo
  read -r -p "Choose an option: " CHOICE
  case "$CHOICE" in
    1) run_helper "prepare-show-hn.command" ;;
    2) run_helper "prepare-v2ex.command" ;;
    3) run_helper "prepare-reddit-opensource.command" ;;
    4) run_helper "prepare-reddit-selfhosted.command" ;;
    5) run_helper "prepare-xiaohongshu.command" ;;
    6) run_helper "prepare-zhihu.command" ;;
    7) run_helper "prepare-linkedin.command" ;;
    8) run_helper "prepare-x.command" ;;
    9) open "https://github.com/langming58-hash/studyhub-local" ;;
    10) open "https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.4" ;;
    11)
      cd "$ROOT"
      npm run ci
      read -r -p "Press Return to continue. " _
      ;;
    12) exit 0 ;;
    *) echo "Unknown option"; sleep 1 ;;
  esac
done
