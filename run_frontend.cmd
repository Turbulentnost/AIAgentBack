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

if not exist "%FRONTEND_DIR%\src\auth\standaloneIncomingMail.ts" (
  echo [error] Frontend is too old for no-auth launch: missing src\auth\standaloneIncomingMail.ts
  echo Update agent_nd_front ^(see UI-POCHTA.md^), alternatively set AGENT_ND_FRONT_DIR to a current clone.
  exit /b 1
)

call "%~dp0scripts\wait_pochta_api.cmd" http://127.0.0.1:8080/health 120

cd /d "%FRONTEND_DIR%"
echo Starting frontend: %FRONTEND_DIR%
echo.
echo Required: agent-pochta API on http://127.0.0.1:8080
echo Open:      http://localhost:5173/agents/incoming-mail
echo Auth:      platform login NOT needed (standalone incoming-mail mode)
echo Tip: after "docker compose up -d --force-recreate", wait ~30s or run scripts\wait_pochta_api.cmd
echo.
REM Override frontend .env: no platform login, only agent-pochta API on :8080
set "VITE_STANDALONE_INCOMING_MAIL=true"
set "VITE_INCOMING_MAIL_PUBLIC=true"
set "VITE_POCHTA_API_PROXY=http://127.0.0.1:8080"
npm run dev -- --open /agents/incoming-mail

endlocal