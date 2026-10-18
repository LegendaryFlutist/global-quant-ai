#!/usr/bin/env bash
# Explicit files only; caller must run relevant tests and update handoff docs first.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [[ $# -lt 2 ]]; then
  echo 'Usage: bash scripts/checkpoint.sh "type: verified work unit" <file> [file ...]' >&2
  exit 2
fi
COMMIT_MESSAGE="$1"
shift
git add -- PROJECT_PROGRESS.md TASKS.md "$@"
python3 scripts/check_publish.py --staged
git diff --cached --check
if git diff --cached --quiet; then
  echo 'No staged changes; no commit created.'
  exit 0
fi
git commit -m "$COMMIT_MESSAGE"
git log -1 --oneline
