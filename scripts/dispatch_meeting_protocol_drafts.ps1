# Запуск диспетчера черновиков протоколов для Windows Task Scheduler.
# Пример: триггер Daily 8:00, 12:00, 16:00 или Repeat every 1 hour.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
python -m app.workers.dispatch_meeting_protocol_drafts
exit $LASTEXITCODE
