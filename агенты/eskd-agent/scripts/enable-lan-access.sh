#!/usr/bin/env bash
# Доступ к ESKD Agent с других ПК в локальной сети (WSL2 + Windows).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

read_env() {
  local key="$1" default="$2" val=""
  if [[ -f .env ]]; then
    val="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  fi
  echo "${val:-$default}"
}

FRONTEND_PORT="$(read_env FRONTEND_PORT 3000)"
LAN_PORT="$(read_env FRONTEND_LAN_PORT 8000)"
BACKEND_PORT="$(read_env BACKEND_PORT 8080)"
MODEL_PORT="$(read_env MODEL_PORT 8765)"
VITE_DEV_PORT="$(read_env VITE_DEV_PORT 5173)"
WSL_IP="$(hostname -I | awk '{print $1}')"
PS1="$ROOT/scripts/enable-lan-access.ps1"
PORTS_CSV="${LAN_PORT},${BACKEND_PORT},${MODEL_PORT},${FRONTEND_PORT},${VITE_DEV_PORT}"

echo "=== ESKD Agent — доступ из LAN ==="
echo "WSL IP: $WSL_IP"
echo "Порты: LAN UI=$LAN_PORT local UI=$FRONTEND_PORT Vite dev=$VITE_DEV_PORT API=$BACKEND_PORT"
echo ""

if ! command -v powershell.exe >/dev/null 2>&1; then
  echo "Нужен Windows + WSL2. Запустите enable-lan-access.ps1 в PowerShell (Admin):" >&2
  echo "  -WslIp $WSL_IP -Ports $PORTS_CSV" >&2
  exit 1
fi

WIN_PS1="$(wslpath -w "$PS1")"

echo "Откроется UAC — подтвердите права администратора Windows..."
powershell.exe -NoProfile -Command \
  "Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', '${WIN_PS1//\'/''}',
    '-WslIp', '${WSL_IP}',
    '-Ports', '${PORTS_CSV}'
  )"

GET_LAN_PS1="$(wslpath -w "$ROOT/scripts/get-lan-ip.ps1" 2>/dev/null || echo "")"

_get_lan_ip() {
  if [[ -n "$GET_LAN_PS1" ]]; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$GET_LAN_PS1" 2>/dev/null | tr -d '\r\n' || true
  else
    powershell.exe -NoProfile -Command "
      Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        \$_.IPAddress -match '^192\.168\.' -and \$_.IPAddress -ne '192.168.56.1' -and \$_.IPAddress -notlike '192.168.137.*'
      } | Select-Object -First 1 -ExpandProperty IPAddress
    " 2>/dev/null | tr -d '\r\n' || true
  fi
}

echo ""
echo "Проверка LAN URL (Windows):"
LAN_IP_WIN="$(_get_lan_ip)"
if [[ -n "${LAN_IP_WIN:-}" ]]; then
  echo "  http://${LAN_IP_WIN}:${LAN_PORT}/"
  powershell.exe -NoProfile -Command "
    try {
      Invoke-WebRequest -Uri 'http://${LAN_IP_WIN}:${LAN_PORT}/health' -UseBasicParsing -TimeoutSec 3 | Out-Null
      Write-Host '  Статус: OK'
    } catch {
      Write-Host '  Статус: FAIL — запустите scripts/START-LAN-ADMIN.bat от администратора'
    }
  " 2>/dev/null || true
else
  echo "  (не найден LAN IP — scripts/SHOW-URL.bat)"
fi

echo ""
echo "Проверка с WSL:"
for p in "$LAN_PORT" "$BACKEND_PORT"; do
  if curl -sf --max-time 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1 || \
     curl -sf --max-time 2 "http://127.0.0.1:$p/" >/dev/null 2>&1; then
    echo "  :$p — OK"
  else
    echo "  :$p — сервис не отвечает (запустите docker-compose / model)"
  fi
done

echo ""
LAN_IP="$(_get_lan_ip)"
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(powershell.exe -NoProfile -Command "
    (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
      \$_.IPAddress -match '^192\.168\.' -and \$_.IPAddress -ne '192.168.56.1' -and \$_.IPAddress -notlike '192.168.137.*' -and \$_.InterfaceAlias -notmatch 'Radmin'
    } | Select-Object -First 1 -ExpandProperty IPAddress)
  " 2>/dev/null | tr -d '\r\n' || true)"
fi
if [[ -n "$LAN_IP" ]]; then
  echo "С другого ПК: http://${LAN_IP}:${LAN_PORT}/"
else
  echo "С другого ПК: http://<IP-WiFi>:${LAN_PORT}/  (scripts\\SHOW-URL.bat)"
fi
echo "Локально:     http://localhost:${FRONTEND_PORT}/"
echo "Диагностика:  ./scripts/lan-diagnose.sh"
