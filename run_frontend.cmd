@echo off
setlocal EnableExtensions

REM UI "Vhodjashhaja korrespondencija" — agent_nd_front (React + Vite)
REM Backend API: python scripts\run_api.py  (http://localhost:8080)
REM Page: http://localhost:5173/agents/incoming-mail

if defined AGENT_ND_FRONT_DIR (
  set "FRONTEND_DIR=%AGENT_ND_FRONT_DIR%"
) else (
  set "ROOT=%~dp0..\.."
  set "FRONTEND_DIR=%ROOT%..\..\..\agent_nd_front"
)

if not exist "%FRONTEND_DIR%\package.json" (
  echo [error] Frontend not found: %FRONTEND_DIR%
  echo Set AGENT_ND_FRONT_DIR to your agent_nd_front clone, then retry.
  exit /b 1
)

cd /d "%FRONTEND_DIR%"
echo Starting frontend: %FRONTEND_DIR%
echo Open http://localhost:5173/agents/incoming-mail
npm run dev

endlocal
