#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
umask 077
mkdir -p .runtime/market-data .runtime/research-runs
if [[ ! -f .runtime/platform.env ]]; then
  python3 - <<'PY'
from pathlib import Path
import secrets
path=Path('.runtime/platform.env')
path.write_text('PLATFORM_DB_PASSWORD='+secrets.token_urlsafe(32)+'\nPLATFORM_PORT=8080\nPLATFORM_COOKIE_SECURE=false\nPLATFORM_ALLOWED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080\n')
path.chmod(0o600)
PY
fi
if ! grep -q '^PLATFORM_RESEARCH_TOKEN=' .runtime/platform.env; then
  python3 - <<'PY'
from pathlib import Path
import secrets
path=Path('.runtime/platform.env')
with path.open('a') as stream:
    stream.write('PLATFORM_RESEARCH_TOKEN='+secrets.token_urlsafe(48)+'\n')
path.chmod(0o600)
PY
fi
COMPOSE=(docker compose --env-file .runtime/platform.env -p quant-lab -f compose.yaml -f compose.platform.yaml)
case "${1:-up}" in
  up|start) "${COMPOSE[@]}" build api web research-runner
    "${COMPOSE[@]}" up -d --no-build --pull never postgres redis proxy-bridge api worker dataset-worker market-refresh web research-runner ;;
  build) "${COMPOSE[@]}" build api web research-runner ;;
  status) "${COMPOSE[@]}" ps ;;
  logs) "${COMPOSE[@]}" logs --tail=100 "${2:-api}" ;;
  test) TEST_EXIT_CODE=0
    "${COMPOSE[@]}" --profile test run --rm backend-tests || TEST_EXIT_CODE=$?
    "${COMPOSE[@]}" --profile test stop postgres-test redis-test
    exit "$TEST_EXIT_CODE" ;;
  stop) "${COMPOSE[@]}" stop web worker dataset-worker market-refresh research-runner api proxy-bridge redis postgres ;;
  credentials) if [[ -f .runtime/bootstrap-admin.txt ]]; then cat .runtime/bootstrap-admin.txt; else echo '先运行 scripts/platform.sh up'; fi ;;
  *) echo '用法: scripts/platform.sh {up|build|status|logs [服务]|test|stop|credentials}' >&2; exit 2 ;;
esac
