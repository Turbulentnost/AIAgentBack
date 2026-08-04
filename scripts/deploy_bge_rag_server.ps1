# Деплой BGE-RAG (agent-pochta) — Windows / PowerShell
# Аналог scripts/deploy_bge_rag_server.sh
#
# Запуск из корня репозитория:
#   .\scripts\deploy_bge_rag_server.ps1
#
# На сервере Jalko (192.168.1.157) после git pull и настройки .env:
#   pwsh ./scripts/deploy_bge_rag_server.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "=== BGE RAG deploy (agent-pochta) ==="

if (-not (Test-Path ".env")) {
    Write-Error "ERROR: .env not found. Copy-Item .env.Jalko.example .env and fill secrets"
    exit 1
}

Write-Host "1. Docker stack (Postgres/RabbitMQ/Celery; Qdrant from .env)"
docker compose up -d postgres rabbitmq celery-worker celery-erp-worker celery-imap-worker celery-beat api
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2. Init Qdrant collections (email_messages, department_corrections_bge, catalogs)"
docker compose run --rm rag-init
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "3. Analyze department knowledge"
docker compose run --rm api python scripts/analyze_department_knowledge.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "4. Sync catalog RAG (contractors, departments, spam)"
docker compose run --rm api python scripts/sync_rag_to_qdrant.py --all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5. Sync department corrections via BGE"
docker compose run --rm api python scripts/sync_department_corrections_to_qdrant.py --all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5b. Backfill operator corrections (full reextract, dedup by email_id)"
docker compose run --rm api python scripts/backfill_bge_corrections.py --all --reextract
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "6. Backfill email vectors (last 30 days, full IMAP reextract)"
docker compose run --rm api python scripts/sync_emails_to_qdrant.py --since-days 30 --limit 500 --force --reextract
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "7. Export verification Excel"
docker compose run --rm api python scripts/export_embedding_verification_table.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== Done. Check data/stats/embedding_verification_*.xlsx ==="
