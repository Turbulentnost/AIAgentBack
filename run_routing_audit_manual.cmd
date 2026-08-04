@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

REM Ручной тест: аудит routing_corrections и RAG keywords
REM Требует: docker compose -p agent-pochta up -d  (Postgres :5433, Qdrant :6333, API :8080)

set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONUNBUFFERED=1"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY=%ROOT%.venv\Scripts\python.exe"
) else (
  set "PY=py"
)

echo.
echo ============================================================
echo   Аудит routing_corrections — РУЧНОЙ ТЕСТ
echo ============================================================
echo.
echo Выберите шаг:
echo   0  Проверка окружения (DB, Qdrant, API, LLM)
echo   1  Только match с БД (быстро, без LLM)
echo   2  Preview LLM keywords — 25 писем (dry-run)
echo   3  Полный dry-run — все однозначные (~30-60 мин)
echo   4  APPLY — записать keywords в JSON + backup
echo   5  Sync Qdrant (--routing-keywords)
echo   6  RAG validation sample (25 писем, CSV+MD)
echo   7  После review: перезапуск только mismatch
echo   8  Быстрый цикл: 1 + 2 + 6 (без apply)
echo   Q  Выход
echo.
set /p STEP="Шаг [0-8/Q]: "

if /i "%STEP%"=="0" goto step0
if /i "%STEP%"=="1" goto step1
if /i "%STEP%"=="2" goto step2
if /i "%STEP%"=="3" goto step3
if /i "%STEP%"=="4" goto step4
if /i "%STEP%"=="5" goto step5
if /i "%STEP%"=="6" goto step6
if /i "%STEP%"=="7" goto step7
if /i "%STEP%"=="8" goto step8
if /i "%STEP%"=="Q" exit /b 0
echo Неизвестный шаг.
exit /b 1

:step0
echo.
echo --- [0] Проверка окружения ---
"%PY%" scripts\check_routing_audit_prereqs.py
goto done

:step1
echo.
echo --- [1] Match only ---
"%PY%" scripts\enrich_routing_corrections_from_mail.py --match-only --no-imap
echo.
echo Отчёт: data\stats\routing_corrections_match_report.csv
goto done

:step2
echo.
echo --- [2] Preview LLM (25 писем, dry-run) ---
"%PY%" scripts\enrich_routing_corrections_from_mail.py --dry-run --limit 25 --no-imap
echo.
echo Смотрите: data\stats\routing_corrections_keywords_diff.csv
goto done

:step3
echo.
echo --- [3] Полный dry-run (все однозначные) ---
echo Это может занять 30-60 минут...
"%PY%" scripts\enrich_routing_corrections_from_mail.py --dry-run --no-imap
goto done

:step4
echo.
echo --- [4] APPLY keywords в routing_corrections.json ---
echo ВНИМАНИЕ: создаётся backup *.bak.* перед записью
set /p CONFIRM="Продолжить? [y/N]: "
if /i not "%CONFIRM%"=="y" (
  echo Отменено.
  exit /b 0
)
"%PY%" scripts\enrich_routing_corrections_from_mail.py --apply --no-imap
goto done

:step5
echo.
echo --- [5] Sync Qdrant ---
"%PY%" scripts\sync_rag_to_qdrant.py --routing-keywords
goto done

:step6
echo.
echo --- [6] RAG validation sample ---
"%PY%" scripts\rag_department_validation_sample.py --count 25 --no-imap
echo.
echo Проверьте: data\stats\rag_validation_sample.md
echo           data\stats\rag_validation_sample.csv
echo Отметьте match=no — для шага 7
goto done

:step7
echo.
echo --- [7] Перезапуск только mismatch ---
set /p CONFIRM="Применить изменения в JSON? [y/N]: "
if /i "%CONFIRM%"=="y" (
  "%PY%" scripts\enrich_routing_corrections_from_mail.py --apply --only-mismatch --no-imap
  "%PY%" scripts\sync_rag_to_qdrant.py --routing-keywords
) else (
  "%PY%" scripts\enrich_routing_corrections_from_mail.py --dry-run --only-mismatch --no-imap
)
goto done

:step8
echo.
echo --- [8] Быстрый цикл (match + preview + validation) ---
"%PY%" scripts\enrich_routing_corrections_from_mail.py --match-only --no-imap
"%PY%" scripts\enrich_routing_corrections_from_mail.py --dry-run --limit 25 --no-imap
"%PY%" scripts\rag_department_validation_sample.py --count 25 --no-imap
echo.
echo Готово. Проверьте data\stats\rag_validation_sample.md
goto done

:done
echo.
echo ============================================================
pause
exit /b %ERRORLEVEL%
