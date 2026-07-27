#!/usr/bin/env bash
# Показать URL для доступа с других ПК в локальной сети.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAN_PORT="$(grep -E '^FRONTEND_LAN_PORT=' "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
LAN_PORT="${LAN_PORT:-8000}"

GET_LAN_PS1="$(wslpath -w "$ROOT/scripts/get-lan-ip.ps1" 2>/dev/null || echo "")"
if [[ -n "$GET_LAN_PS1" ]]; then
  LAN_IP="$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$GET_LAN_PS1" 2>/dev/null | tr -d '\r\n' || true)"
else
  LAN_IP=""
fi

echo ""
echo "=== ESKD Agent — URL для других компьютеров ==="
if [[ -n "$LAN_IP" ]]; then
  echo "  http://${LAN_IP}:${LAN_PORT}/"
else
  echo "  http://<IP-вашего-WiFi>:${LAN_PORT}/"
  echo "  (запустите scripts/SHOW-URL.bat в Windows)"
fi
echo ""
echo "  Не используйте localhost и не порт 3000 с другого ПК."
echo "  Если не открывается: scripts/START-LAN-ADMIN.bat от администратора"
echo ""
