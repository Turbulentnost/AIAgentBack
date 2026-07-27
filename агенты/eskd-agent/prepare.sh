#!/usr/bin/env bash
# Предзагрузка артефактов ESKD Agent (Docker + веса HF + опционально warm GPU)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTORCH_IMAGE="hiyouga/pytorch:th2.6.0-cu124-flashattn2.7.4-cxx11abi0-devel"
BASE_MODEL="${BASE_MODEL_PATH:-$ROOT/models/gemma-3n-e4b-hf}"
ADAPTER="${ADAPTER_PATH:-$ROOT/dist/kaggle_release/gemma-3n-eskd-lora}"
HF_REPO_BASE="${HF_REPO_BASE:-google/gemma-3n-E4B-it}"
HF_REPO_ADAPTER="${HF_REPO_ADAPTER:-MaxJalo/gemma-3n-eskd-lora}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "Нужен docker-compose или docker compose plugin" >&2
  exit 1
fi

echo "=== 1/4 Docker-образ CUDA (model) ~5–6 GB ==="
docker pull "$PYTORCH_IMAGE"

echo "=== 2/4 Базовые образы backend + frontend ==="
$COMPOSE --project-directory . --project-name eskd \
  -f compose/base.yml \
  -f compose/postgres.yml \
  -f compose/backend.yml \
  -f compose/frontend.yml \
  build backend frontend

model_ok=false
if [[ -f "$BASE_MODEL/config.json" ]] && [[ -f "$BASE_MODEL/model.safetensors.index.json" || -f "$BASE_MODEL/model.safetensors" ]]; then
  echo "=== 3/4 Base model OK: $BASE_MODEL ==="
  model_ok=true
else
  echo "=== 3/4 Скачивание base model с HuggingFace → $BASE_MODEL ==="
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "Задайте HF_TOKEN (лицензия Gemma): export HF_TOKEN=hf_..." >&2
    exit 1
  fi
  mkdir -p "$BASE_MODEL"
  export HF_REPO_BASE BASE_MODEL HF_TOKEN
  python3 - <<'PY'
from huggingface_hub import snapshot_download
import os
snapshot_download(
    repo_id=os.environ["HF_REPO_BASE"],
    local_dir=os.environ["BASE_MODEL"],
    token=os.environ.get("HF_TOKEN"),
)
PY
  model_ok=true
fi

if [[ -f "$ADAPTER/adapter_model.safetensors" ]]; then
  echo "=== LoRA adapter OK: $ADAPTER ==="
else
  echo "=== Скачивание LoRA adapter → $ADAPTER ==="
  mkdir -p "$ADAPTER"
  export HF_REPO_ADAPTER ADAPTER HF_TOKEN
  python3 - <<'PY'
from huggingface_hub import snapshot_download
import os
snapshot_download(
    repo_id=os.environ["HF_REPO_ADAPTER"],
    local_dir=os.environ["ADAPTER"],
    token=os.environ.get("HF_TOKEN"),
)
PY
fi

if [[ "${WARM_GPU:-0}" == "1" ]]; then
  echo "=== 4/4 Warm load в VRAM (--preload) ==="
  cd "$ROOT"
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  python scripts/finetune/gemma3n_eskd_api.py \
    --host 0.0.0.0 --port 8765 --preload \
    --model "$BASE_MODEL" --adapter "$ADAPTER" &
  pid=$!
  echo "Ожидание /health (до 15 мин)..."
  for _ in $(seq 1 90); do
    if curl -sf http://127.0.0.1:8765/health | grep -q '"model_loaded": true'; then
      echo "Модель в VRAM, API: http://0.0.0.0:8765 (и http://<IP-хоста>:8765)"
      echo "Остановить: kill $pid"
      exit 0
    fi
    sleep 10
  done
  kill "$pid" 2>/dev/null || true
  echo "Timeout warm load — проверьте GPU/CUDA" >&2
  exit 1
else
  echo "=== 4/4 Warm GPU пропущен (WARM_GPU=1 для загрузки в VRAM) ==="
fi

echo ""
echo "Готово. Запуск стека:"
echo "  cd eskd-agent && ./stack.sh up all --build"
echo "Model на хосте + UI (рекомендуется для WSL):"
echo "  ./start.sh"
echo "  # или: ./stack.sh up app --model-host --lan --build"
echo "Отладка:"
echo "  ./stack.sh dev backend --model-host"
echo "  ./stack.sh dev frontend --model-host"
echo "  ./stack.sh debug backend"
