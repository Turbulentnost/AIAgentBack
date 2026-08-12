@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo === Aveon LAN firewall (Administrator) ===
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"\"%~dp0scripts\open_aveon_lan_firewall.ps1\"\"'"
if errorlevel 1 (
  echo Firewall setup failed or was cancelled.
  exit /b 1
)
echo.
echo === Generating access-info for testers ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_aveon_lan_access.ps1" -SkipFirewall
endlocal
