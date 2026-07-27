#!/usr/bin/env bash
# Диагностика LAN-доступа к ESKD Agent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

read_env() {
  local key="$1" default="$2" val=""
  [[ -f .env ]] && val="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\n' || true)"
  echo "${val:-$default}"
}

LAN_PORT="$(read_env FRONTEND_LAN_PORT 8000)"
WSL_IP="$(hostname -I | awk '{print $1}')"

echo "=== ESKD LAN diagnose ==="
echo "WSL IP: $WSL_IP"
echo "Рекомендуемый URL для других ПК: http://<IP-Windows>:${LAN_PORT}/"
echo ""

echo "--- Сервисы в WSL ---"
for p in "$LAN_PORT" 8080 3000; do
  if curl -sf --max-time 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
    echo "  127.0.0.1:$p  OK"
  else
    echo "  127.0.0.1:$p  FAIL (запустите ./start.sh)"
  fi
done

if command -v powershell.exe >/dev/null 2>&1; then
  echo ""
  echo "--- Windows portproxy + LAN ---"
  powershell.exe -NoProfile -Command "
    netsh interface portproxy show v4tov4
    Write-Host ''
    \$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
      \$_.IPAddress -match '^192\.168\.' -and \$_.IPAddress -ne '192.168.56.1' -and \$_.IPAddress -notlike '192.168.137.*'
    } | Select-Object -First 1 -ExpandProperty IPAddress)
    if (-not \$ip) { \$ip = '192.168.2.61' }
    foreach (\$p in ${LAN_PORT},8080,3000) {
      try {
        Invoke-WebRequest -Uri \"http://\${ip}:\$p/health\" -UseBasicParsing -TimeoutSec 3 | Out-Null
        Write-Host \"  \${ip}:\$p  OK (Windows)\"
      } catch {
        Write-Host \"  \${ip}:\$p  FAIL\"
      }
    }
    Write-Host ''
    Write-Host 'Listening:'
    Get-NetTCPConnection -State Listen -LocalPort ${LAN_PORT},8080,3000 -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort | Format-Table -AutoSize
  "
fi

echo ""
echo "--- Что проверить ---"
echo "1. Другой ПК в той же Wi-Fi (не гостевая сеть, не VPN-only)"
echo "2. URL: http://<IP-WiFi>:${LAN_PORT}/  (сейчас см. scripts/show-lan-url.sh)"
echo "3. Запустите от админа: ./scripts/enable-lan-access.sh"
echo "4. После wsl --shutdown снова enable-lan-access.sh (IP WSL меняется)"
echo "5. Роутер: отключите «изоляция клиентов» / AP isolation"
echo "6. Постоянно: scripts/install-wsl-mirrored.ps1 + wsl --shutdown"
