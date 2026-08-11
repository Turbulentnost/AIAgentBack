# Быстрый обход без docker build: копирует изменённые .py в работающие контейнеры и перезапускает их.
# Не заменяет полноценную пересборку образа — только для срочного деплоя правок Python.
#
#   .\scripts\hot_patch_backend.ps1
#   .\scripts\hot_patch_backend.ps1 -DryRun

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$files = @(
    "src/agent_pochta/demo_filter.py",
    "src/agent_pochta/db/message_filters.py",
    "src/agent_pochta/db/repository.py",
    "src/agent_pochta/routing/recipients.py",
    "src/agent_pochta/nodes/n7_create_erp_task.py",
    "src/agent_pochta/workers/tasks.py",
    "src/agent_pochta/api/app.py",
    "src/agent_pochta/stats/classification_log.py",
    "src/agent_pochta/metrics/prometheus_exporter.py"
)

$containers = @(
    "agent-pochta-api-1",
    "agent-pochta-celery-worker-1",
    "agent-pochta-celery-erp-worker-1",
    "agent-pochta-celery-imap-worker-1"
)

foreach ($c in $containers) {
    $exists = docker ps --format "{{.Names}}" | Select-String -Pattern "^$([regex]::Escape($c))$" -Quiet
    if (-not $exists) {
        Write-Host "Пропуск (не запущен): $c" -ForegroundColor DarkYellow
        $containers = $containers | Where-Object { $_ -ne $c }
    }
}

if ($containers.Count -eq 0) {
    Write-Error "Нет запущенных backend-контейнеров. Сначала: docker compose up -d api celery-worker celery-imap-worker celery-erp-worker"
}

foreach ($c in $containers) {
    foreach ($f in $files) {
        $local = Join-Path $Root $f
        if (-not (Test-Path $local)) {
            Write-Warning "Нет файла: $f"
            continue
        }
        if ($DryRun) {
            Write-Host "[dry-run] docker cp $f ${c}:/app/$f"
        } else {
            docker cp $local "${c}:/app/$f"
        }
    }
}

if ($DryRun) {
    Write-Host "[dry-run] docker compose restart api celery-worker celery-erp-worker celery-imap-worker"
    exit 0
}

Write-Host "Перезапуск контейнеров..." -ForegroundColor Green
docker compose restart api celery-worker celery-erp-worker celery-imap-worker
Write-Host "Готово. Для очистки тестовых писем: docker compose exec api python scripts/cleanup_demo_messages.py" -ForegroundColor Cyan
