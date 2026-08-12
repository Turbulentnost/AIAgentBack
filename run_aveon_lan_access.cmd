@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo === Aveon LAN access setup (firewall + access-info) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_aveon_lan_access.ps1" %*
if errorlevel 1 exit /b 1
endlocal
