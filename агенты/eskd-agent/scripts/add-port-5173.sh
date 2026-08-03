#!/usr/bin/env bash
# Только порт 5173 (Vite): поднимает UAC, затем проверяет http://LAN:5173/
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WSL_IP="$(hostname -I | awk '{print $1}')"
WIN_BAT="$(wslpath -w "$ROOT/scripts/ADD-5173-LAN-ADMIN.bat")"
echo "WSL IP: $WSL_IP"
echo "Подтвердите UAC (администратор)..."
powershell.exe -NoProfile -Command "Start-Process -FilePath cmd.exe -Verb RunAs -Wait -ArgumentList '/c','\"$WIN_BAT\"'"
powershell.exe -NoProfile -Command "netsh interface portproxy show v4tov4" | grep 5173 || true
GET_LAN_PS1="$(wslpath -w "$ROOT/scripts/get-lan-ip.ps1" 2>/dev/null || echo "")"
LAN_IP=""
[[ -n "$GET_LAN_PS1" ]] && LAN_IP="$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$GET_LAN_PS1" 2>/dev/null | tr -d '\r\n' || true)"
LAN_IP="${LAN_IP:-192.168.2.120}"
code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "http://${LAN_IP}:5173/" || echo 000)"
echo "Проверка: http://${LAN_IP}:5173/ -> HTTP $code"
