#!/usr/bin/env bash
# Запуск model API на хосте (Gemma + LoRA, без OpenRouter).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read_env() {
  local key="$1" default="$2" val=""
  [[ -f "$AGENT/.env" ]] && val="$(grep -E "^${key}=" "$AGENT/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  echo "${val:-$default}"
}

BASE_MODEL="$(read_env BASE_MODEL_PATH "$ROOT/models/gemma-3n-e4b-hf")"
ADAPTER="$(read_env ADAPTER_PATH "$ROOT/dist/kaggle_release/gemma-3n-eskd-lora")"
PORT="$(read_env MODEL_PORT 8765)"
PIPELINE="$(read_env ESKD_PIPELINE_MODE legacy)"

[[ "$BASE_MODEL" != /* ]] && BASE_MODEL="$ROOT/${BASE_MODEL#../}"
[[ "$ADAPTER" != /* ]] && ADAPTER="$ROOT/${ADAPTER#../}"

if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health" | grep -q '"model_loaded": true'; then
  echo "Model API уже работает: http://127.0.0.1:${PORT}/health"
  curl -sf "http://127.0.0.1:${PORT}/health" | python3 -m json.tool 2>/dev/null || true
  exit 0
fi

cd "$ROOT"
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export ESKD_VLM_BACKEND=local
export ESKD_PIPELINE_MODE="$PIPELINE"

echo "Запуск local model API на 0.0.0.0:${PORT}…"
nohup python scripts/finetune/gemma3n_eskd_api.py \
  --host 0.0.0.0 --port "$PORT" --preload \
  --model "$BASE_MODEL" --adapter "$ADAPTER" \
  --vlm-backend local \
  --pipeline "$PIPELINE" \
  > /tmp/eskd-model.log 2>&1 &

echo "PID=$! · лог: /tmp/eskd-model.log"
echo "Ожидание загрузки (до 5 мин)…"
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health" | grep -q '"model_loaded": true'; then
    echo "Model OK — backend: MODEL_SERVICE_URL=http://host.docker.internal:${PORT}"
    curl -sf "http://127.0.0.1:${PORT}/health" | python3 -m json.tool 2>/dev/null || true
    exit 0
  fi
  sleep 5
done

echo "Timeout — см. /tmp/eskd-model.log" >&2
exit 1
