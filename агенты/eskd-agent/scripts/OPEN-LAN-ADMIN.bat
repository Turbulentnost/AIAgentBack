@echo off
REM Открыть настройку LAN от администратора (UAC).
setlocal
set "BAT=%~dp0START-LAN-ADMIN.bat"
powershell.exe -NoProfile -Command "Start-Process -FilePath '%BAT%' -Verb RunAs"
echo.
echo Если UAC не появился — правый клик на START-LAN-ADMIN.bat -^> Запуск от имени администратора
pause
