#!/usr/bin/env bash
# Проброс портов UI/API/Vite в Windows portproxy (UAC: подтвердите окно администратора).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

read_env() {
  local key="$1" default="$2" val=""
  [[ -f .env ]] && val="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  echo "${val:-$default}"
}

FRONTEND_PORT="$(read_env FRONTEND_PORT 3000)"
LAN_PORT="$(read_env FRONTEND_LAN_PORT 8000)"
BACKEND_PORT="$(read_env BACKEND_PORT 8080)"
MODEL_PORT="$(read_env MODEL_PORT 8765)"
VITE_DEV_PORT="$(read_env VITE_DEV_PORT 5173)"
WSL_IP="$(hostname -I | awk '{print $1}')"
WIN_PS1="$(wslpath -w "$ROOT/scripts/enable-lan-access.ps1")"
PORTS_CSV="${LAN_PORT},${FRONTEND_PORT},${BACKEND_PORT},${MODEL_PORT},${VITE_DEV_PORT}"

echo "WSL IP: $WSL_IP"
echo "Проброс портов: $PORTS_CSV"

powershell.exe -NoProfile -Command "
  Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', '${WIN_PS1//\'/''}',
    '-WslIp', '${WSL_IP}',
    '-Ports', '${PORTS_CSV}'
  )
"

echo ""
powershell.exe -NoProfile -Command "netsh interface portproxy show v4tov4"

GET_LAN_PS1="$(wslpath -w "$ROOT/scripts/get-lan-ip.ps1" 2>/dev/null || echo "")"
LAN_IP=""
if [[ -n "$GET_LAN_PS1" ]]; then
  LAN_IP="$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$GET_LAN_PS1" 2>/dev/null | tr -d '\r\n' || true)"
fi
LAN_IP="${LAN_IP:-192.168.2.187}"

echo ""
echo "Проверка LAN (Windows -> ${LAN_IP}):"
powershell.exe -NoProfile -Command "
  foreach (\$p in ${LAN_PORT},${BACKEND_PORT},${VITE_DEV_PORT}) {
    try {
      Invoke-WebRequest -Uri \"http://${LAN_IP}:\$p/\" -UseBasicParsing -TimeoutSec 5 | Out-Null
      Write-Host \"  http://${LAN_IP}:\$p/ OK\"
    } catch {
      try {
        Invoke-WebRequest -Uri \"http://${LAN_IP}:\$p/health\" -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host \"  http://${LAN_IP}:\$p/health OK\"
      } catch {
        Write-Host \"  http://${LAN_IP}:\$p/ FAIL — запустите scripts/ADD-5173-LAN-ADMIN.bat от админа\"
      }
    }
  }
"
