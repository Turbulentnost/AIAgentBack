# Выгрузка рабочей копии ESKD Agent (OpenRouter)

> **Назначение:** перенести только файлы, нужные для запуска **ESKD Agent** в режиме **OpenRouter** (без локальной GPU-модели и без тестовой базы разметки), в дублирующую папку.  
> **Типичный размер рабочей копии:** ~15–30 МБ (вместо ~27+ ГБ полного репозитория).

---

## 1. Карта репозитория (что где лежит)

| Путь | Размер (ориентир) | Нужен для OpenRouter? |
|------|-------------------|------------------------|
| `eskd-agent/` | ~2,5 МБ (без node_modules) | **Да** — основной стек |
| `scripts/finetune/` | ~1,6 МБ | **Да** — model API на хосте |
| `scripts/eskd_prompt_context.py` | ~12 КБ | **Да** — промпты пайплайна |
| `data/eskd_gost_vyzhimka.txt` | ~68 КБ | **Да** — выжимка ГОСТ в system prompt |
| `pyproject.toml`, `.env.example`, `.gitignore` | мало | **Да** — venv и конфиг |
| `models/` | ~17 ГБ | **Нет** — локальная Gemma + LoRA |
| `dist/` | ~1,6 ГБ | **Нет** — артефакты обучения/Kaggle |
| `.venv/` | ~6,2 ГБ | **Нет** — пересоздать |
| `data/eskd_marking/` | ~152 МБ | **Нет** — тестовая KB/разметка |
| `data/eskd_colab/` + `*.zip` | ~1,1 ГБ | **Нет** — датасеты для fine-tune |
| `ESKD/` | ~13 МБ | **Нет** (опционально для пересборки выжимки) |
| `app/`, `alembic/` (корень) | ~8 МБ | **Нет** — legacy backend платформы |
| `tests/`, `tools/`, `unsloth_pochta/` | разное | **Нет** |
| Docker volumes (`eskd_pg_data`, …) | растёт | **Нет** — создаются при `stack.sh up` |
| `eskd-agent/frontend/node_modules/` | ~170 КБ–сотни МБ | **Нет** — собирается в Docker |

---

## 2. Что копировать / что исключить

### 2.1. Включить (рабочие файлы)

```
eskd-agent/
  backend/          # FastAPI, alembic, тесты backend — опционально
  frontend/         # без node_modules и dist/
  compose/
  docs/
  scripts/          # LAN, OpenRouter, import_marking_to_kb.py
  model/            # Dockerfile/заглушки для GPU-режима (мало весит)
  *.sh, docker-compose*.yml, compose.sh
  .env.example

scripts/
  finetune/         # gemma3n_eskd_api.py, openrouter-модули
  eskd_prompt_context.py

data/
  eskd_gost_vyzhimka.txt

pyproject.toml
.env.example        # корневой — fallback AGENT_API_KEY для start.sh
.gitignore
README.md           # опционально
```

### 2.2. Исключить (не тащить в дубликат)

```
# Тяжёлые артефакты
models/
dist/
.venv/
.venv-gpu/
*.zip
*.pdf               # в корне и «документы для разметки»

# Тестовая база и датасеты
data/eskd_marking/
data/eskd_marking_compact/
data/eskd_colab/
data/eskd_ocr/
data/*.zip

# Legacy / dev-мусор
app/
alembic/            # корневой alembic платформы
tests/
tools/
unsloth_pochta/
reports/
.pytest_cache/
__pycache__/
*.py[cod]
.mypy_cache/
.ruff_cache/
htmlcov/
.cache/

# Node / Git (по желанию)
**/node_modules/
.git/               # опционально: без истории — меньше, но нет git

# Секреты (см. §4)
.env
eskd-agent/.env
```

### 2.3. Что не копировать, а пересоздать на новом месте

| Компонент | Как получить |
|-----------|--------------|
| Python venv | `python3 -m venv .venv && source .venv/bin/activate && pip install -e .` |
| Frontend deps | `docker compose build` через `./eskd-agent/stack.sh up app --build` |
| PostgreSQL | `./eskd-agent/stack.sh up infra` → пустая БД + alembic migrate |
| Превью/интеграция | Docker volumes `eskd_storage`, `eskd_integration` |
| OpenRouter VLM | API-ключ в `.env`, модели в облаке |
| Локальная Gemma | не нужна при `ESKD_VLM_BACKEND=openrouter` |

---

## 3. Готовый промпт для Cursor / AI-агента

Скопируйте блок ниже в новый чат (укажите свои пути):

```
Задача: выгрузить из репозитория /home/td-user/agent_nd только рабочие файлы
ESKD Agent (режим OpenRouter), без тестовой базы разметки и тяжёлых артефактов,
в папку-назначение: /ПУТЬ/К/ДУБЛИКАТУ/agent_nd

Контекст:
- Запуск: cd eskd-agent && ./start.sh (OpenRouter model API + Docker backend/frontend)
- Model API: scripts/finetune/gemma3n_eskd_api.py через scripts/run_model_openrouter.sh
- Нужен корневой .venv с зависимостями из pyproject.toml (не копировать .venv)
- Секреты: копировать только .env.example, создать .env вручную

Включить:
- eskd-agent/ (кроме node_modules, .env, __pycache__)
- scripts/finetune/
- scripts/eskd_prompt_context.py
- data/eskd_gost_vyzhimka.txt
- pyproject.toml, .env.example, .gitignore

Исключить:
- models/, dist/, .venv/, data/eskd_marking*, data/eskd_colab*, data/*.zip
- app/, tests/, tools/, ESKD/, unsloth_pochta/, alembic/ (корень)
- **/node_modules/, **/__pycache__/, .pytest_cache/, .git/ (если не нужна история)
- все .env с секретами

После копирования:
1. cp eskd-agent/.env.example eskd-agent/.env — заполнить OPENROUTER_API_KEY
2. python3 -m venv .venv && pip install -e .
3. cd eskd-agent && ./start.sh

Проверка:
- curl http://127.0.0.1:8765/health  (model API)
- curl http://localhost:8080/health  (backend)
- UI: http://localhost:3000/

Используй rsync или scripts/export-working-copy.sh из репозитория.
Не коммить изменения без явной просьбы.
```

---

## 4. Секреты и `.env`

| Файл | Действие |
|------|----------|
| `eskd-agent/.env.example` | **Копировать** — шаблон |
| `eskd-agent/.env` | **Не копировать** в общие/удалённые дубликаты |
| `/.env.example` | Копировать (fallback `AGENT_API_KEY` для `start.sh`) |
| `/.env` | **Не копировать** |

Минимум для OpenRouter в `eskd-agent/.env`:

```env
ESKD_PIPELINE_MODE=two_stage
ESKD_VLM_BACKEND=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_VLM_MODEL=nvidia/nemotron-nano-12b-v2-vl:free
OPENROUTER_EVAL_MODEL=anthropic/claude-sonnet-4
MODEL_SERVICE_URL=http://host.docker.internal:8765
```

---

## 5. Ручные команды

### 5.1. rsync (рекомендуется)

```bash
SRC="/home/td-user/agent_nd"
DST="/ПУТЬ/К/ДУБЛИКАТУ/agent_nd"

mkdir -p "$DST"

rsync -a --info=progress2 \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='.venv-gpu/' \
  --exclude='models/' \
  --exclude='dist/' \
  --exclude='app/' \
  --exclude='tests/' \
  --exclude='tools/' \
  --exclude='ESKD/' \
  --exclude='unsloth_pochta/' \
  --exclude='alembic/' \
  --exclude='reports/' \
  --exclude='.pytest_cache/' \
  --exclude='**/__pycache__/' \
  --exclude='**/*.py[cod]' \
  --exclude='**/node_modules/' \
  --exclude='data/eskd_marking/' \
  --exclude='data/eskd_marking_compact/' \
  --exclude='data/eskd_colab/' \
  --exclude='data/eskd_ocr/' \
  --exclude='data/*.zip' \
  --exclude='*.zip' \
  --exclude='*.pdf' \
  --exclude='.env' \
  --exclude='eskd-agent/.env' \
  "$SRC/eskd-agent/" "$DST/eskd-agent/" \
  "$SRC/scripts/finetune/" "$DST/scripts/finetune/" \
  "$SRC/scripts/eskd_prompt_context.py" "$DST/scripts/" \
  "$SRC/data/eskd_gost_vyzhimka.txt" "$DST/data/" \
  "$SRC/pyproject.toml" "$SRC/.env.example" "$SRC/.gitignore" "$SRC/README.md" "$DST/"

# Шаблоны env
cp "$DST/eskd-agent/.env.example" "$DST/eskd-agent/.env"
# Отредактируйте OPENROUTER_API_KEY вручную
```

### 5.2. tar (архив для переноса)

```bash
SRC="/home/td-user/agent_nd"
ARCHIVE="/tmp/eskd-agent-working-copy.tar.gz"

tar -czf "$ARCHIVE" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='models' \
  --exclude='dist' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='data/eskd_marking' \
  --exclude='data/eskd_colab' \
  -C "$SRC" \
  eskd-agent \
  scripts/finetune \
  scripts/eskd_prompt_context.py \
  data/eskd_gost_vyzhimka.txt \
  pyproject.toml .env.example .gitignore README.md
```

### 5.3. Скрипт-обёртка

```bash
/home/td-user/agent_nd/scripts/export-working-copy.sh /ПУТЬ/К/ДУБЛИКАТУ/agent_nd
```

---

## 6. Первый запуск в дубликате

```bash
cd /ПУТЬ/К/ДУБЛИКАТУ/agent_nd

# 1. Python-окружение для model API
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Конфиг
cp eskd-agent/.env.example eskd-agent/.env
# nano eskd-agent/.env  → OPENROUTER_API_KEY

# 3. Стек
cd eskd-agent
./start.sh
```

Docker создаст volumes `eskd_pg_data`, `eskd_storage`, `eskd_integration` — история проверок и разметка будут **пустыми** (это ожидаемо).

---

## 7. Оценка размеров

| Вариант | Размер |
|---------|--------|
| Полный репозиторий (с models, .venv, data) | ~27+ ГБ |
| **Рабочая копия OpenRouter** | **~15–30 МБ** |
| + локальная Gemma (`models/` + `dist/`) | +~18 ГБ |
| + тестовая разметка (`data/eskd_marking/`) | +~152 МБ |

---

## 8. Частые ошибки

1. **«Не найден .venv»** — создайте venv в корне дубликата (`pip install -e .`).
2. **OPENROUTER_API_KEY** — задайте в `eskd-agent/.env` или `AGENT_API_KEY` в корневом `.env`.
3. **Пустая история/разметка** — нормально: БД в Docker volume, не копируется.
4. **Нужны данные разметки для импорта** — отдельно экспортируйте dump PostgreSQL или `data/eskd_marking/` (не входит в рабочую копию по умолчанию).
