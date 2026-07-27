# Агенты — выносимые стеки

В этой папке лежат **автономные** агенты с собственным Docker-стеком (отдельно от `docker-compose.yml` платформы в корне AIAgentBack).

## ESKD Agent (`eskd-agent/`)

Проверка КД по ЕСКД: backend (FastAPI), UI (Vite + nginx в Docker), PostgreSQL. LLM — через **MODEL_SERVICE_URL** (OpenRouter или model API на хосте), без обязательной локальной GPU-модели в репозитории.

### Быстрый старт

```bash
cd агенты/eskd-agent
cp .env.example .env
# Заполните MODEL_SERVICE_URL, OPENROUTER_API_KEY (или свой inference URL), AGENT_API_KEY и т.д.

./stack.sh up app --build          # postgres + backend + frontend
# или
./start.sh                         # app + LAN-хелперы (WSL/Windows)
```

Порты по умолчанию: UI `3000` / LAN `8000`, API `8080` (см. `.env.example`).

### Модули stack.sh

| Модуль | Назначение |
|--------|------------|
| `infra` / `postgres` | только БД |
| `backend` | API |
| `frontend` | UI |
| `app` | postgres + backend + frontend |
| `model` | GPU-контейнер (профиль `model`) |
| `all` | полный стек с docker-model |

OpenRouter / внешний API:

```bash
./stack.sh up app --model-host --build
```

### Платформенный UI (AIAgentFront)

Корневой backend AIAgentBack уже содержит интеграцию `app/eskd/` (прокси к ESKD API). **React-оболочка платформы** — репозиторий [AIAgentFront, ветка Jalko](https://github.com/Turbulentnost/AIAgentFront/tree/Jalko), каталог **`frontend/`** в этом клоне.

- `frontend/` — платформенный UI (AIAgentFront);
- `агенты/eskd-agent/frontend/` — **отдельный** UI для автономного стека (Docker), не заменяется платформенным фронтом.

Запуск платформенного UI (после клона):

```bash
cd frontend
npm ci && npm run dev
# Backend платформы: из корня AIAgentBack — docker compose up api / scripts/run_api.py
```

### Профиль GPU model (опционально)

Файлы `compose/model.yml` и `model/Dockerfile` рассчитаны на **корень монорепозитория** с `scripts/finetune/`, `models/`, `tools/LLaMA-Factory` (как в исходном `agent_nd`). Внутри только AIAgentBack профиль `model` **не самодостаточен** — используйте OpenRouter/`--model-host` или смонтируйте пути через `.env` (`BASE_MODEL_PATH`, `ADAPTER_PATH`).

### Документация

- `eskd-agent/docs/` — руководство пользователя, интеграция CAD/KOMPAS, OpenAPI sidecar.
- `eskd-agent/docs/EXPORT_WORKING_COPY_PROMPT.md` — что включать/исключать при переносе из полного `agent_nd`.

### Секреты

Не коммитьте `.env`. Используйте только `.env.example` как шаблон.
