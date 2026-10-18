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
# Do not silently include another person's previously staged changes in this unit.
python3 - "$@" <<'PY_CHECKPOINT'
from pathlib import Path
import subprocess,sys
root=Path.cwd()
allowed=[(root/p).resolve() for p in ['PROJECT_PROGRESS.md','TASKS.md',*sys.argv[1:]]]
staged=subprocess.check_output(['git','diff','--cached','--name-only','-z']).decode().split('\0')
unexpected=[]
for name in filter(None,staged):
    path=(root/name).resolve()
    if not any(path==item or item.is_dir() and path.is_relative_to(item) for item in allowed):unexpected.append(name)
if unexpected:
    print('Refusing to include unrelated staged files; review them first:',file=sys.stderr)
    for name in unexpected:print(name,file=sys.stderr)
    raise SystemExit(1)
PY_CHECKPOINT
git add -- PROJECT_PROGRESS.md TASKS.md "$@"
python3 scripts/check_publish.py --staged
git diff --cached --check
if git diff --cached --quiet; then
  echo 'No staged changes; no commit created.'
  exit 0
fi
git commit -m "$COMMIT_MESSAGE"
git log -1 --oneline
