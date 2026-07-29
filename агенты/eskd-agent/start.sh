#!/usr/bin/env bash
# Запуск backend + frontend в Docker. Model не поднимается — URL в MODEL_SERVICE_URL (.env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

read_env() {
  local key="$1" default="$2" val=""
  [[ -f .env ]] && val="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  echo "${val:-$default}"
}

FRONTEND_PORT="$(read_env FRONTEND_PORT 3000)"
LAN_PORT="$(read_env FRONTEND_LAN_PORT 8000)"
BACKEND_PORT="$(read_env BACKEND_PORT 8080)"
# BASE_MODEL="$REPO/models/gemma-3n-e4b-hf"
# ADAPTER="$REPO/dist/kaggle_release/gemma-3n-eskd-lora"
#
# if [[ "${SKIP_MODEL_START:-0}" != "1" ]]; then
#   if ! curl -sf --max-time 2 "http://127.0.0.1:8765/health" | grep -q '"model_loaded": true'; then
#     echo "Запуск model API на хосте…"
#     cd "$REPO"
#     # shellcheck disable=SC1091
#     source .venv/bin/activate
#     nohup python scripts/finetune/gemma3n_eskd_api.py \
#       --host 0.0.0.0 --port 8765 --preload \
#       --model "$BASE_MODEL" --adapter "$ADAPTER" \
#       > /tmp/eskd-model.log 2>&1 &
#     echo "Ожидание model (до 3 мин)…"
#     for _ in $(seq 1 36); do
#       if curl -sf --max-time 2 "http://127.0.0.1:8765/health" | grep -q '"model_loaded": true'; then
#         echo "Model OK"
#         break
#       fi
#       sleep 5
#     done
#     cd "$ROOT"
#   fi
# else
#   echo "SKIP_MODEL_START=1 — model на хосте не запускается (offline/cache режим)"
# fi

MODEL_URL="$(read_env MODEL_SERVICE_URL http://host.docker.internal:8765)"
echo "Model не запускается локально. Backend использует: ${MODEL_URL}"
echo "  (задайте MODEL_SERVICE_URL в .env для внешнего API)"

docker-compose -f docker-compose.yml -f docker-compose.ui.yml up --build -d backend frontend

if [[ "${SKIP_LAN_SETUP:-0}" != "1" ]] && command -v powershell.exe >/dev/null 2>&1; then
  echo ""
  echo "Настройка доступа из локальной сети (Windows portproxy + firewall)…"
  if bash "$ROOT/scripts/enable-lan-access.sh"; then
    :
  else
    echo "  Не удалось автоматически — запустите от админа Windows: scripts\\START-LAN-ADMIN.bat"
  fi
fi

GET_LAN_PS1="$(wslpath -w "$ROOT/scripts/get-lan-ip.ps1" 2>/dev/null || echo "")"
if [[ -n "$GET_LAN_PS1" ]]; then
  LAN_IP="$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$GET_LAN_PS1" 2>/dev/null | tr -d '\r\n' || true)"
fi
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(powershell.exe -NoProfile -Command \
    "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { \$_.IPAddress -match '^192\.168\.' -and \$_.IPAddress -ne '192.168.56.1' -and \$_.IPAddress -notlike '192.168.137.*' } | Select-Object -First 1 -ExpandProperty IPAddress" \
    2>/dev/null | tr -d '\r\n' || true)"
fi

echo ""
echo "=== ESKD Agent запущен ==="
echo "  Этот ПК:     http://localhost:${FRONTEND_PORT}/"
if [[ -n "$LAN_IP" ]]; then
  echo "  Другие ПК:   http://${LAN_IP}:${LAN_PORT}/"
  echo "  API (LAN):   http://${LAN_IP}:${BACKEND_PORT}/health"
else
  echo "  Другие ПК:   http://<IP-WiFi>:${LAN_PORT}/  (см. scripts/SHOW-URL.bat)"
fi
echo ""
echo "  Health: curl http://localhost:${LAN_PORT}/health"
echo ""
echo "  Модули: ./stack.sh up infra|backend|frontend|model|app|all"
echo ""
echo "  LAN не открывается с других ПК?"
echo "  1) Windows Admin: scripts\\START-LAN-ADMIN.bat"
echo "  2) Узнать URL:     scripts\\SHOW-URL.bat"
echo "  3) IP мог смениться — не используйте старый 192.168.2.61"
