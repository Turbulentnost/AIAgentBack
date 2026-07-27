#!/usr/bin/env bash
# Modular Docker stack for ESKD Agent.
#
# Usage:
#   ./stack.sh up infra|model|backend|frontend|app|all [options]
#   ./stack.sh down infra|model|backend|frontend|app|all
#   ./stack.sh dev backend|frontend
#   ./stack.sh debug backend|model
#   ./stack.sh ps|logs [service]
#
# Options (up):
#   --model-host   model on host, backend -> host.docker.internal:8765
#   --lan          extra LAN frontend port (8000)
#   --build        rebuild images
#   --no-build     skip build (default for up)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN=(docker-compose)
else
  echo "Нужен docker compose или docker-compose" >&2
  exit 1
fi

COMPOSE=(
  "${COMPOSE_BIN[@]}"
  --project-directory "$ROOT"
  --project-name eskd
)

if [[ -f "$ROOT/.env" ]]; then
  COMPOSE+=(--env-file "$ROOT/.env")
fi

BASE=( -f "$ROOT/compose/base.yml" )
POSTGRES=( -f "$ROOT/compose/postgres.yml" )
MODEL=( -f "$ROOT/compose/model.yml" )
BACKEND=( -f "$ROOT/compose/backend.yml" )
FRONTEND=( -f "$ROOT/compose/frontend.yml" )
OV_MODEL_HOST=( -f "$ROOT/compose/overrides/model-host.yml" )
OV_DEV_BACKEND=( -f "$ROOT/compose/overrides/dev-backend.yml" )
OV_DEV_FRONTEND=( -f "$ROOT/compose/overrides/dev-frontend.yml" )
OV_DEBUG_BACKEND=( -f "$ROOT/compose/overrides/debug-backend.yml" )
OV_DEBUG_MODEL=( -f "$ROOT/compose/overrides/debug-model.yml" )
OV_LAN=( -f "$ROOT/compose/overrides/lan.yml" )

MODEL_HOST=0
LAN=0
BUILD_FLAG=()
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
ESKD Agent — modular Docker stack

  ./stack.sh up infra|model|backend|frontend|app|all [--model-host] [--lan] [--build]
  ./stack.sh down infra|model|backend|frontend|app|all
  ./stack.sh dev backend|frontend [--model-host] [--build]
  ./stack.sh debug backend|model [--build]
  ./stack.sh ps [service]
  ./stack.sh logs [service]

Modules:
  infra     PostgreSQL only
  model     GPU model container (profile: model)
  backend   postgres + API gateway
  frontend  nginx UI (starts backend if needed)
  app       postgres + backend + frontend (no docker model)
  all       full stack including docker model

Examples:
  ./stack.sh up app --model-host --lan --build
  ./stack.sh dev frontend --model-host
  ./stack.sh debug backend
EOF
}

compose_files_for() {
  local module="$1"
  local mode="${2:-prod}"
  local files=( "${BASE[@]}" )

  case "$module" in
    infra)
      files+=( "${POSTGRES[@]}" )
      ;;
    model)
      files+=( "${MODEL[@]}" )
      if [[ "$mode" == "debug" ]]; then
        files+=( "${OV_DEBUG_MODEL[@]}" )
      fi
      ;;
    backend)
      files+=( "${POSTGRES[@]}" "${BACKEND[@]}" )
      if [[ "$MODEL_HOST" == "1" ]]; then
        files+=( "${OV_MODEL_HOST[@]}" )
      fi
      if [[ "$mode" == "dev" ]]; then
        files+=( "${OV_DEV_BACKEND[@]}" )
      elif [[ "$mode" == "debug" ]]; then
        files+=( "${OV_DEBUG_BACKEND[@]}" )
      fi
      ;;
    frontend)
      files+=( "${POSTGRES[@]}" "${BACKEND[@]}" "${FRONTEND[@]}" )
      if [[ "$MODEL_HOST" == "1" ]]; then
        files+=( "${OV_MODEL_HOST[@]}" )
      fi
      if [[ "$LAN" == "1" ]]; then
        files+=( "${OV_LAN[@]}" )
      fi
      if [[ "$mode" == "dev" ]]; then
        files+=( "${OV_DEV_FRONTEND[@]}" )
      fi
      ;;
    app)
      files+=( "${POSTGRES[@]}" "${BACKEND[@]}" "${FRONTEND[@]}" )
      if [[ "$MODEL_HOST" == "1" ]]; then
        files+=( "${OV_MODEL_HOST[@]}" )
      fi
      if [[ "$LAN" == "1" ]]; then
        files+=( "${OV_LAN[@]}" )
      fi
      if [[ "$mode" == "dev-backend" ]]; then
        files+=( "${OV_DEV_BACKEND[@]}" )
      fi
      ;;
    all)
      files+=( "${POSTGRES[@]}" "${MODEL[@]}" "${BACKEND[@]}" "${FRONTEND[@]}" )
      if [[ "$LAN" == "1" ]]; then
        files+=( "${OV_LAN[@]}" )
      fi
      ;;
    *)
      echo "Неизвестный модуль: $module" >&2
      usage
      exit 1
      ;;
  esac

  printf '%s\0' "${files[@]}"
}

services_for() {
  local module="$1"
  case "$module" in
    infra) echo postgres ;;
    model) echo model ;;
    backend) echo postgres backend ;;
    frontend) echo postgres backend frontend ;;
    app) echo postgres backend frontend ;;
    all) echo postgres model backend frontend ;;
  esac
}

run_compose() {
  local module="$1"
  local mode="$2"
  shift 2
  local -a files=()
  while IFS= read -r -d '' f; do
    files+=( "$f" )
  done < <(compose_files_for "$module" "$mode")
  # docker-compose v2: --build must follow `up`, not precede it
  if [[ ${#BUILD_FLAG[@]} -gt 0 && $# -gt 0 && "$1" == "up" ]]; then
    "${COMPOSE[@]}" "${files[@]}" up "${BUILD_FLAG[@]}" "${@:2}"
  else
    "${COMPOSE[@]}" "${files[@]}" "$@"
  fi
}

CMD="${1:-}"
shift || true

case "$CMD" in
  up|down|dev|debug|ps|logs|"")
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Неизвестная команда: $CMD" >&2
    usage
    exit 1
    ;;
esac

if [[ -z "$CMD" ]]; then
  usage
  exit 1
fi

MODE=prod
MODULE=""
SERVICE_FILTER=""

if [[ "$CMD" == "dev" || "$CMD" == "debug" ]]; then
  MODULE="${1:-}"
  shift || true
  [[ -z "$MODULE" ]] && { echo "Укажите модуль: backend|frontend|model" >&2; exit 1; }
  if [[ "$CMD" == "dev" ]]; then
    MODE=dev
    [[ "$MODULE" == "backend" ]] && MODE=dev
    [[ "$MODULE" == "frontend" ]] && MODE=dev
  else
    MODE=debug
  fi
else
  MODULE="${1:-}"
  shift || true
  [[ -z "$MODULE" ]] && { echo "Укажите модуль: infra|model|backend|frontend|app|all" >&2; exit 1; }
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-host) MODEL_HOST=1 ;;
    --lan) LAN=1 ;;
    --build) BUILD_FLAG=(--build) ;;
    --no-build) BUILD_FLAG=() ;;
    -d|--detach) EXTRA_ARGS+=(-d) ;;
    *) EXTRA_ARGS+=("$1") ;;
  esac
  shift
done

if [[ "$CMD" == "up" && ${#EXTRA_ARGS[@]} -eq 0 ]]; then
  EXTRA_ARGS=(-d)
fi

case "$CMD" in
  up)
    SERVICES="$(services_for "$MODULE")"
    if [[ "$MODULE" == "model" || "$MODULE" == "all" ]]; then
      run_compose "$MODULE" "$MODE" up --profile model "${EXTRA_ARGS[@]}" $SERVICES
    else
      run_compose "$MODULE" "$MODE" up "${EXTRA_ARGS[@]}" $SERVICES
    fi
    ;;
  down)
    SERVICES="$(services_for "$MODULE")"
    if [[ "$MODULE" == "model" || "$MODULE" == "all" ]]; then
      run_compose "$MODULE" "$MODE" down --profile model "${EXTRA_ARGS[@]}" $SERVICES
    else
      run_compose "$MODULE" "$MODE" down "${EXTRA_ARGS[@]}" $SERVICES
    fi
    ;;
  dev)
    BUILD_FLAG=(--build)
    EXTRA_ARGS=(-d)
    case "$MODULE" in
      backend)
        if [[ "$MODEL_HOST" != "1" ]]; then
          MODEL_HOST=1
        fi
        run_compose backend dev up "${EXTRA_ARGS[@]}" postgres backend
        ;;
      frontend)
        run_compose frontend dev up "${EXTRA_ARGS[@]}" postgres backend frontend
        ;;
      *)
        echo "dev поддерживает: backend|frontend" >&2
        exit 1
        ;;
    esac
    ;;
  debug)
    BUILD_FLAG=(--build)
    EXTRA_ARGS=(-d)
    case "$MODULE" in
      backend)
        run_compose backend debug up "${EXTRA_ARGS[@]}" postgres backend
        ;;
      model)
        run_compose model debug up "${EXTRA_ARGS[@]}" --profile model model
        ;;
      *)
        echo "debug поддерживает: backend|model" >&2
        exit 1
        ;;
    esac
    ;;
  ps)
    run_compose app prod ps "${EXTRA_ARGS[@]}"
    ;;
  logs)
    run_compose app prod logs -f "${EXTRA_ARGS[@]}"
    ;;
esac
