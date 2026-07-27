#!/usr/bin/env bash
# Быстро добавить проброс порта UI (3000) в Windows portproxy — частая причина «LAN не работает».
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

read_env() {
  local key="$1" default="$2" val=""
  [[ -f .env ]] && val="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  echo "${val:-$default}"
}

FRONTEND_PORT="$(read_env FRONTEND_PORT 3000)"
BACKEND_PORT="$(read_env BACKEND_PORT 8080)"
MODEL_PORT="$(read_env MODEL_PORT 8765)"
WSL_IP="$(hostname -I | awk '{print $1}')"
WIN_PS1="$(wslpath -w "$ROOT/scripts/enable-lan-access.ps1")"
PORTS_CSV="${FRONTEND_PORT},${BACKEND_PORT},${MODEL_PORT}"

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

echo ""
echo "Проверка LAN (Windows -> 192.168.2.61):"
powershell.exe -NoProfile -Command "
  foreach (\$p in ${FRONTEND_PORT},${BACKEND_PORT}) {
    try {
      Invoke-WebRequest -Uri \"http://192.168.2.61:\$p/health\" -UseBasicParsing -TimeoutSec 3 | Out-Null
      Write-Host \"  192.168.2.61:\$p OK\"
    } catch {
      Write-Host \"  192.168.2.61:\$p FAIL — проверьте firewall / portproxy\"
    }
  }
"
