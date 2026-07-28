@echo off
REM Двойной клик от администратора — проброс LAN для ESKD Agent
setlocal
cd /d "%~dp0.."

for /f "tokens=*" %%i in ('wsl.exe -e hostname -I') do set WSL_RAW=%%i
for /f "tokens=1" %%a in ("%WSL_RAW%") do set WSL_IP=%%a

echo WSL IP: %WSL_IP%
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable-lan-access.ps1" -WslIp %WSL_IP% -Ports 8000,8080,8765,3000,5173
echo.
pause
