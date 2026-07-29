#!/usr/bin/env bash
# Обёртка: docker compose / stack.sh + LAN-доступ
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "lan" ]]; then
  exec "$ROOT/scripts/enable-lan-access.sh"
fi

if [[ "${1:-}" == "stack" ]]; then
  shift
  exec "$ROOT/stack.sh" "$@"
fi

if [[ "${1:-}" == "up" || "${1:-}" == "down" || "${1:-}" == "dev" || "${1:-}" == "debug" ]]; then
  exec "$ROOT/stack.sh" "$@"
fi

if docker compose version >/dev/null 2>&1; then
  exec docker compose --project-directory "$ROOT" --project-name eskd "$@"
elif command -v docker-compose >/dev/null 2>&1; then
  exec docker-compose --project-directory "$ROOT" --project-name eskd "$@"
else
  echo "Ошибка: не найден ни 'docker compose', ни 'docker-compose'." >&2
  echo "Используйте: ./stack.sh up app --model-host" >&2
  exit 1
fi
