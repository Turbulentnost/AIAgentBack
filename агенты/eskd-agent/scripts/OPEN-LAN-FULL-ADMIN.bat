@echo off
REM LAN + отключение firewall. Запуск ОТ ИМЕНИ АДМИНИСТРАТОРА.
setlocal EnableDelayedExpansion

net session >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] Правый клик -^> "Запуск от имени администратора"
  pause
  exit /b 1
)

for /f "tokens=*" %%i in ('wsl.exe -e hostname -I 2^>nul') do set WSL_RAW=%%i
for /f "tokens=1" %%a in ("!WSL_RAW!") do set WSL_IP=%%a

if "!WSL_IP!"=="" (
  echo [ОШИБКА] WSL не запущен
  pause
  exit /b 1
)

echo WSL IP: !WSL_IP!
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0open-lan-disable-firewall.ps1" -WslIp "!WSL_IP!" -Ports "8000,8080,8765,3000,5173"

echo.
echo Запуск docker в WSL...
wsl.exe -e bash -lc "cd /home/td-user/agent_nd/eskd-agent && docker-compose -f docker-compose.yml -f docker-compose.ui.yml up -d backend frontend"

echo.
for /f "tokens=*" %%i in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-lan-ip.ps1" 2^>nul') do set LAN_IP=%%i
if not defined LAN_IP set LAN_IP=192.168.2.120

echo ========================================
echo   С другого ПК: http://!LAN_IP!:8000/
echo ========================================
start "" "http://!LAN_IP!:8000/"
pause
