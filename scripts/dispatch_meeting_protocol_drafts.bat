@echo off
setlocal
cd /d "%~dp0.."
python -m app.workers.dispatch_meeting_protocol_drafts
exit /b %ERRORLEVEL%
