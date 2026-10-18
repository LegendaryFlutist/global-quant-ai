#!/usr/bin/env bash
# Read-only handoff. Never pulls, resets, migrates, or exposes credentials.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
for DOCUMENT in AGENTS.md PROJECT_PROGRESS.md TASKS.md; do
  if [[ -f "$DOCUMENT" ]]; then
    printf '\n===== %s =====\n' "$DOCUMENT"
    cat "$DOCUMENT"
  else
    printf 'Missing required handoff file: %s\n' "$DOCUMENT" >&2
    exit 1
  fi
done
printf '\n===== Git status =====\n'
git status --short --branch
printf '\n===== Recent commits =====\n'
git log -5 --oneline
printf '\nNext: check working changes, then continue the highest-priority unfinished task in TASKS.md.\n'
