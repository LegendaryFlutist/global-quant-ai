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
cat <<'NEXT_STEPS'

This script only displays recovery notes and Git history; it does not start the app or run a backtest.

Start the Web platform:
  bash scripts/platform.sh up
  Open http://localhost:8080

Start the NVIDIA research container (if it is stopped):
  bash scripts/container.sh start

Run the unified model backtest inside that container:
  bash scripts/container.sh run

Check running containers without starting or changing them:
  bash scripts/platform.sh status

Next development step: inspect working changes, then continue the highest-priority unfinished task in TASKS.md.
NEXT_STEPS
