#!/usr/bin/env bash
# Can run from any directory. Do not read the project's mail .env in Compose.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose --env-file /dev/null --project-directory "$PROJECT_DIR" -p quant-lab -f "$PROJECT_DIR/compose.yaml")
action="${1:-help}"
if (($#)); then shift; fi
case "$action" in
  build) "${COMPOSE[@]}" build "$@" ;;
  start) "${COMPOSE[@]}" up -d lab "$@" ;;
  check) "${COMPOSE[@]}" exec -T lab python scripts/check_runtime.py "$@" ;;
  run) "${COMPOSE[@]}" exec -T lab python run.py "$@" ;;
  lab) "${COMPOSE[@]}" exec -T lab python lab.py "$@" ;;
  test) "${COMPOSE[@]}" exec -T lab python -m unittest discover -s tests -v "$@" ;;
  shell) "${COMPOSE[@]}" exec lab bash "$@" ;;
  stop) "${COMPOSE[@]}" stop lab "$@" ;;
  *) printf '%s\n' 'Usage: bash scripts/container.sh {build|start|check|run|lab|test|shell|stop} [arguments]' ;;
esac
