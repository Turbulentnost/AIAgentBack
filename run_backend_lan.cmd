@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Stopping backend on port 5454...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5454" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID:"') do (
  wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /I "uvicorn app.main" >nul && taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting backend: uvicorn 0.0.0.0:5454
start "Aveon Backend" cmd /k ".\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 5454"
echo.
echo Backend window opened. LAN: http://192.168.2.225:5454/api/v1/health
endlocal
