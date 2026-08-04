#!/usr/bin/env bash
# Деплой BGE-RAG на сервер Jalko (192.168.1.157)
# Запуск на сервере из каталога agent-pochta после git pull и настройки .env
# Windows: .\scripts\deploy_bge_rag_server.ps1

set -euo pipefail

echo "=== BGE RAG deploy (agent-pochta) ==="

if [ ! -f .env ]; then
  echo "ERROR: .env not found. cp .env.Jalko.example .env && fill secrets"
  exit 1
fi

echo "1. Docker stack (Postgres/RabbitMQ/Celery; Qdrant external on 192.168.1.157:6333)"
docker compose up -d postgres rabbitmq celery-worker celery-erp-worker celery-imap-worker celery-beat api

echo "2. Init Qdrant collections (email_messages, department_corrections_bge, catalogs)"
docker compose run --rm rag-init

echo "3. Analyze department knowledge"
docker compose run --rm api python scripts/analyze_department_knowledge.py

echo "4. Sync catalog RAG (contractors, departments, spam)"
docker compose run --rm api python scripts/sync_rag_to_qdrant.py --all

echo "5. Sync department corrections via BGE"
docker compose run --rm api python scripts/sync_department_corrections_to_qdrant.py --all

echo "5b. Backfill operator corrections (full reextract, dedup by email_id)"
docker compose run --rm api python scripts/backfill_bge_corrections.py --all --reextract

echo "6. Backfill email vectors (last 30 days, full IMAP reextract)"
docker compose run --rm api python scripts/sync_emails_to_qdrant.py --since-days 30 --limit 500 --force --reextract

echo "7. Export verification Excel"
docker compose run --rm api python scripts/export_embedding_verification_table.py

echo "=== Done. Check data/stats/embedding_verification_*.xlsx ==="
