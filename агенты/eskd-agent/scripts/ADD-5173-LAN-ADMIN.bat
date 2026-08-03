@echo off
REM Один клик от администратора: проброс Vite dev :5173 (WSL -> LAN)
setlocal EnableDelayedExpansion
net session >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] Правый клик -^> "Запуск от имени администратора"
  pause
  exit /b 1
)
for /f "tokens=1" %%a in ('wsl.exe -e hostname -I') do set WSL_IP=%%a
if "!WSL_IP!"=="" (
  echo [ОШИБКА] WSL не запущен
  pause
  exit /b 1
)
sc query iphlpsvc | find "RUNNING" >nul || net start iphlpsvc >nul 2>&1
netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=0.0.0.0 >nul 2>&1
netsh interface portproxy add v4tov4 listenport=5173 listenaddress=0.0.0.0 connectaddress=!WSL_IP! connectport=5173
netsh advfirewall firewall delete rule name="ESKD Agent TCP 5173" >nul 2>&1
netsh advfirewall firewall add rule name="ESKD Agent TCP 5173" dir=in action=allow protocol=TCP localport=5173 profile=any enable=yes
echo OK: 0.0.0.0:5173 -^> !WSL_IP!:5173
for /f "tokens=*" %%i in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-lan-ip.ps1" 2^>nul') do set LAN_IP=%%i
if not defined LAN_IP set LAN_IP=192.168.2.120
echo.
echo   http://!LAN_IP!:5173/
echo.
pause
