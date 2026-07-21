@echo off
setlocal EnableExtensions

REM UI "Vhodjashhaja korrespondencija" ? agent_nd_front (React + Vite)
REM Backend API: docker compose up api  OR  python scripts\run_api.py  (http://127.0.0.1:8080)
REM Page: http://localhost:5173/agents/incoming-mail

if defined AGENT_ND_FRONT_DIR (
  set "FRONTEND_DIR=%AGENT_ND_FRONT_DIR%"
) else (
  set "FRONTEND_DIR=%~dp0..\..\..\agent_nd_front"
)

if not exist "%FRONTEND_DIR%\package.json" (
  echo [error] Frontend not found: %FRONTEND_DIR%
  echo Set AGENT_ND_FRONT_DIR to your agent_nd_front clone, then retry.
  exit /b 1
)

call "%~dp0scripts\wait_pochta_api.cmd" http://127.0.0.1:8080/health 120

cd /d "%FRONTEND_DIR%"
echo Starting frontend: %FRONTEND_DIR%
echo.
echo Required: agent-pochta API on http://127.0.0.1:8080
echo Open:      http://localhost:5173/agents/incoming-mail
echo Auth:      platform login NOT needed (VITE_STANDALONE_INCOMING_MAIL=true)
echo Tip: after "docker compose up -d --force-recreate", wait ~30s or run scripts\wait_pochta_api.cmd
echo.
set VITE_STANDALONE_INCOMING_MAIL=true
set VITE_INCOMING_MAIL_PUBLIC=true
npm run dev

endlocal
