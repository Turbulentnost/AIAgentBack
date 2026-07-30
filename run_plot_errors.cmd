@echo off
setlocal EnableExtensions

REM Графики ошибок/правок агента (отдел, спам) из PostgreSQL.
REM Пример: run_plot_errors.cmd
REM         run_plot_errors.cmd --days 30

chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set MPLBACKEND=Agg

cd /d "%~dp0"

set "USE_VENV="
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import matplotlib" >nul 2>&1
    if not errorlevel 1 set "USE_VENV=1"
)

if defined USE_VENV (
    ".venv\Scripts\python.exe" scripts\plot_agent_errors.py %*
) else (
    py -3 scripts\plot_agent_errors.py %*
)
if errorlevel 1 exit /b 1

endlocal
