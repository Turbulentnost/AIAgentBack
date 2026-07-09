@echo off

setlocal EnableExtensions



REM Одной командой: IP listing IP, URL, логин/пароль → access-info.txt

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\write_access_info.ps1" %*

if errorlevel 1 exit /b 1



endlocal

