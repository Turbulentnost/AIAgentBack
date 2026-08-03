@echo off

REM === ESKD Agent: LAN-доступ. Запуск ОТ ИМЕНИ АДМИНИСТРАТОРА ===

REM Правый клик -> "Запуск от имени администратора"

setlocal EnableDelayedExpansion



echo.

echo ========================================

echo   ESKD Agent - настройка LAN + firewall

echo ========================================

echo.



net session >nul 2>&1

if errorlevel 1 (

  echo [ОШИБКА] Нужны права администратора!

  echo Правый клик на этот файл -^> "Запуск от имени администратора"

  pause

  exit /b 1

)



for /f "tokens=*" %%i in ('wsl.exe -e hostname -I 2^>nul') do set WSL_RAW=%%i

for /f "tokens=1" %%a in ("!WSL_RAW!") do set WSL_IP=%%a



if "!WSL_IP!"=="" (

  echo [ОШИБКА] WSL не запущен. Сначала откройте Ubuntu/WSL.

  pause

  exit /b 1

)



echo WSL IP: !WSL_IP!



REM iphlpsvc нужен для portproxy

sc query iphlpsvc | find "RUNNING" >nul || net start iphlpsvc >nul 2>&1



for %%P in (8000 8080 8765 3000 5173) do (

  netsh interface portproxy delete v4tov4 listenport=%%P listenaddress=0.0.0.0 >nul 2>&1

  netsh interface portproxy add v4tov4 listenport=%%P listenaddress=0.0.0.0 connectaddress=!WSL_IP! connectport=%%P

  netsh advfirewall firewall delete rule name="ESKD Agent TCP %%P" >nul 2>&1

  netsh advfirewall firewall add rule name="ESKD Agent TCP %%P" dir=in action=allow protocol=TCP localport=%%P profile=any enable=yes

  echo   Port %%P -^> !WSL_IP!:%%P  [OK]

)



echo.

echo --- portproxy ---

netsh interface portproxy show v4tov4



echo.

echo --- Запуск сервисов в WSL ---

wsl.exe -e bash -lc "cd /home/td-user/agent_nd/eskd-agent && ./start.sh"



echo.

echo ========================================

echo   Откройте с ДРУГОГО компьютера:

echo.

for /f "tokens=*" %%i in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-lan-ip.ps1" 2^>nul') do set LAN_IP=%%i

if not defined LAN_IP for /f "tokens=*" %%i in ('powershell.exe -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\\.168\\.' -and $_.IPAddress -ne '192.168.56.1' -and $_.IPAddress -notlike '192.168.137.*' -and $_.InterfaceAlias -notmatch 'Radmin' } | Select-Object -First 1 -ExpandProperty IPAddress)"') do set LAN_IP=%%i

if not defined LAN_IP set LAN_IP=192.168.2.102

echo   http://!LAN_IP!:8000/
echo   http://!LAN_IP!:5173/  ^(AIAgentFront Vite dev^)

echo.

echo   НЕ localhost, НЕ порт 3000

echo ========================================

echo.

pause

