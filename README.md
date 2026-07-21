# Агент-Почта (ТЗ-АГТ-ПОЧТА-001)

ИИ-агент обработки входящей корреспонденции Outlook для НПО «Турбулентность-ДОН».
Маршрутизация входящей почты в 1С:ERP на платформе **LangGraph**.

Компонент корпоративной платформы ИИ-агентов (ТЗ-ПЛАТФ-001).

## Что делает агент

Конечный граф из 8 узлов:

| № | Узел | Назначение |
|---|------|-----------|
| 1 | `imap_listener` | Мониторинг ящиков по IMAP (polling) |
| 2 | `spam_filter` | Двухуровневая фильтрация: правила + LLM |
| 3 | `identify_sender` | Идентификация отправителя через RAG `contractors` |
| 4 | `process_content` | Извлечение текста из вложений (PDF/DOCX/XLSX/OCR) |
| 5 | `route_department` | Определение профильного отдела (RAG `departments` + LLM) |
| 6 | `summarize` | Краткий русскоязычный обзор (3–5 предложений) |
| 7 | `create_erp_task` | Создание задачи в 1С:ERP через Integration Service |
| 8 | `finalize` | Логирование в PostgreSQL и завершение |

Плюс human-in-the-loop (5 сценариев эскалации к офис-менеджеру).

## Архитектурный принцип

Все **платформенные сервисы** скрыты за интерфейсами (`services/*.py`):

- `LLMGateway` — единая точка обращения к LLM
- `DocumentService` — извлечение текста из вложений
- `IntegrationService` — создание документов/задач в 1С:ERP (прямой доступ к 1С **запрещён**)
- `RAGService` — поиск по коллекциям `contractors` и `departments` (Qdrant)
- `VaultClient` — секреты (пароли IMAP, токены 1С, ключи LLM)

По умолчанию (`USE_STUBS=true`) используются **заглушки** — агент запускается без внешней инфраструктуры.
Когда появятся реальные сервисы платформы, заглушки меняются на адаптеры без изменения логики узлов.

## Быстрый старт (демо на заглушках)

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env
python scripts/run_demo.py      # прогон графа на тестовом письме
```

## Локальная инфраструктура

```bash
docker compose up -d            # PostgreSQL + Qdrant + RabbitMQ
pip install -e .
cp .env.example .env
alembic upgrade head            # миграции БД
python scripts/seed_rag.py --load   # демо-данные в Qdrant (опционально)
```

### Спринт 2: IMAP + API + retry 1С

```bash
docker compose up -d
pip install -e ".[dev,api]"
python scripts/run_migrate.py

python scripts/run_celery_worker.py
python scripts/run_celery_beat.py
python scripts/run_api.py
```

На **Windows** worker и beat — в **двух** терминалах (`--beat` в worker не работает).

Если порт 5432 уже занят — в `docker-compose.yml` Postgres публикуется на **5433** (см. `DATABASE_URL`).

**Windows cmd:** не копируйте команды с `# комментарием` — cmd воспринимает `#` как аргумент.

При `USE_STUBS=false` задайте URL сервисов платформы (см. `agent_nd/.env.example`).

```bash
python scripts/check_platform.py   # LLM + Qdrant
python scripts/seed_rag.py --load  # коллекции contractors / departments
```

**Гибридный режим:** без `INTEGRATION_SERVICE_URL` / `DOCUMENT_SERVICE_URL` эти узлы остаются stub (API на платформе ещё не развёрнуты). LLM — через `/chat/completions` (LM Studio `:1234/v1`).

## Статус

✅ **Спринт 2** — IMAP polling, HTTP-адаптеры, Celery retry 1С, REST API HITL.

🚧 **Спринт 3** — UI «Входящая корреспонденция» в `agent_nd_front` (`/agents/incoming-mail`).

### UI (agent_nd_front)

```bash
# Терминал 1: agent-pochta API (если ещё не запущен)
python scripts/run_api.py

# Терминал 2: фронтенд (из корня agent-pochta удобнее run_frontend.cmd)
run_frontend.cmd
# или: cd agent_nd_front && npm run dev
```

Откройте http://localhost:5173/agents/incoming-mail — **логин не нужен** при `VITE_STANDALONE_INCOMING_MAIL=true` (включено в `run_frontend.cmd` и `.env.example` фронта). UI работает только с agent-pochta (`:8080`), platform API (`:5454`) не требуется.

Proxy dev: `/pochta-api` → `http://127.0.0.1:8080` (см. `VITE_POCHTA_API_PROXY` в `.env` фронта).

Если в консоли Vite `http proxy error … ECONNREFUSED` на `/api/v1/email-messages`: это не «битый» proxy — пока контейнер `api` пересоздаётся (`docker compose up --force-recreate`), порт 8080 на хосте недоступен 20–60 с, а UI опрашивает API каждые 30 с. Проверка: `curl http://127.0.0.1:8080/health`. После перезапуска Docker: `scripts\wait_pochta_api.cmd` или `docker compose up -d --wait api`.

Следующий шаг: human-in-the-loop формы в UI, интеграция с каталогом платформы, `USE_STUBS=false` для ОПЭ.
