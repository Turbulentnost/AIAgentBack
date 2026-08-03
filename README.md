# Backend

FastAPI backend платформы ИИ-агентов: API, агенты, оркестратор, RAG, документы и workers.

Агент входящей корреспонденции (`src/agent_pochta`) живёт в этом же репозитории:
отдельные Celery-воркеры/beat и API на `:8080` (см. `infrastructure/docker-compose.yml`, сервисы `pochta-*`).
