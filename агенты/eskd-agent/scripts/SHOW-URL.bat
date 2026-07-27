@echo off
REM Показать актуальный URL для других ПК (без прав админа)
setlocal EnableDelayedExpansion

for /f "tokens=*" %%i in ('powershell.exe -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\\.168\\.' -and $_.IPAddress -ne '192.168.56.1' -and $_.IPAddress -notlike '192.168.137.*' } | Select-Object -First 1 -ExpandProperty IPAddress)"') do set LAN_IP=%%i

if not defined LAN_IP set LAN_IP=192.168.2.102

set URL=http://!LAN_IP!:8000/

echo.
echo ========================================
echo   ESKD Agent - URL для других ПК
echo.
echo   !URL!
echo.
echo   НЕ localhost, НЕ порт 3000
echo ========================================
echo.

powershell.exe -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Откройте с другого ПК в той же Wi-Fi:`n`n%URL%`n`nЕсли не открывается:`n1) ping %LAN_IP% с другого ПК`n2) START-LAN-ADMIN.bat от админа','ESKD Agent LAN URL','OK','Information')" 2>nul

pause
