# PROMT — дорожная карта данных Агент-Почта

> Рабочий журнал этапов загрузки справочников и интеграции с 1С.  
> Обновляйте статусы по мере прогресса; используйте при merge с платформой и другими БД.

**Документы:** ТЗ-АГТ-ПОЧТА-001 §9, Алгоритм разработки Фаза 3.

---

## Зачем три слоя данных

| Слой | Назначение | Технология |
|------|------------|------------|
| **Staging (истина для merge)** | Нормализованные справочники из разных источников | PostgreSQL `erp_*` |
| **RAG (поиск для агента)** | Семантический поиск отделов, email → контрагент | Qdrant |
| **Операционные письма** | Результаты обработки входящей почты | PostgreSQL `email_*` |

Почтовые ящики (`info@`, `pereadres@`) **не** из 1С — только IMAP в `.env`.

---

## Таблицы PostgreSQL (миграция `003`)

### `catalog_sync_runs`

Журнал каждой загрузки (для аудита и отката).

| Поле | Смысл |
|------|--------|
| `source` | `1c`, `json`, `platform`, … |
| `status` | `running` / `done` / `error` |
| `contractors_count`, `departments_count` | Сколько записей upsert |
| `notes` | Откуда грузили (путь JSON, URL OData) |

### `erp_contractors`

| Поле | Из 1С / JSON | Для агента |
|------|--------------|------------|
| `source` + `contractor_id` | уникальный ключ merge | |
| `name` | наименование | узел 3, поле «Партнёр» в 1С |
| `emails_json` | email контактов | точный поиск From |
| `department_codes_json` | допустимые отделы | фильтр узла 5 |
| `contractor_type` | клиент / поставщик / госорган | приоритет «Срочно» |
| `external_ref` | Ref_Key 1С | связь при merge |
| `raw_payload_json` | сырая строка OData | отладка, diff |
| `needs_review` | черновик нового контрагента | HITL (будущее) |
| `is_active` | soft-delete при merge | |

### `erp_departments`

| Поле | Из 1С / JSON | Для агента |
|------|--------------|------------|
| `source` + `department_id` | код подразделения | поле «Кому» в 1С |
| `department_name` | наименование | UI, задача ERP |
| `head_name` | руководитель | исполнитель в 1С |
| `responsibility` | зона ответственности | RAG departments |
| `keywords_json` | ключевые слова (+ Приложение Г) | RAG departments |
| `external_ref`, `raw_payload_json` | как у контрагентов | merge |

---

## Что выгружаем из 1С (READ → staging)

### Контрагенты

- один или несколько **email**;
- наименование, тип контрагента;
- коды отделов, к которым относится контрагент.

### Отделы

- код и название подразделения;
- **ФИО руководителя** (обязательно для задачи в 1С);
- описание зоны ответственности;
- keywords — частично из **Приложения Г** СТО-34-238 (`data/rag_department_keywords.json`).

### Не выгружаем в staging

- входящие письма (IMAP);
- готовые документы «Входящая корреспонденция» (это WRITE через Integration Service).

---

## Этапы (обновляйте статус)

| # | Этап | Статус | Команда / артефакт |
|---|------|--------|-------------------|
| E1 | Миграция БД `003` | ✅ done | `python scripts/run_migrate.py` |
| E2 | Загрузка example JSON → PostgreSQL | ✅ done | `python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --source json` |
| E3 | Тот же каталог → Qdrant | ✅ done | команда выше (без `--skip-qdrant`) |
| E4 | Расширить каталог (IT, HR, … по Приложению Г) | ⬜ todo | правка JSON + keywords |
| E5 | OData публикации в 1С (контрагенты, подразделения) | ⬜ blocked | `.env`: `ODATA_*` |
| E6 | Sync OData → PostgreSQL → Qdrant | ⬜ blocked | `python scripts/sync_rag_from_1c.py --odata` |
| E7 | Merge правила: `source` + `external_ref` | ⬜ todo | см. раздел ниже |
| E8 | Integration Service → запись в 1С (WRITE) | ⬜ blocked | `INTEGRATION_SERVICE_URL` |
| E9 | Черновик нового контрагента (`needs_review`) | ⬜ todo | узел 3 + UI платформы |
| E10 | Периодический sync (Celery beat / cron) | ⬜ todo | |

**Легенда:** ✅ done · 🔄 in progress · ⬜ todo · ⛔ blocked

---

## Merge с другими базами (E7)

Принцип: одна строка = `(source, business_id)`.

```
source=1c       contractor_id=C-001     external_ref=<Ref_Key 1С>
source=json     contractor_id=C-001     (ручная доработка до OData)
source=platform (будущее)               merge по external_ref или email
```

**Порядок при конфликте (предложение):**

1. `1c` — master для кодов и `head_name`;
2. `json` / `platform` — дополняют `keywords`, `responsibility`;
3. при расхождении email — не удалять, флаг `needs_review=true`.

**Qdrant** пересобирается из **активных** строк PostgreSQL (`is_active=true`), не напрямую из 1С.

---

## Команды

```bash
# Только preview
python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --dry-run

# PostgreSQL + Qdrant (по умолчанию оба)
python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --source json

# Только staging в PostgreSQL
python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --skip-qdrant

# Из 1С OData (когда готовы URL)
python scripts/sync_rag_from_1c.py --odata --source 1c
```

---

## Связь с узлами графа

| Узел | Читает |
|------|--------|
| 3 identify_sender | Qdrant `contractors` ← из `erp_contractors` |
| 5 route_department | Qdrant `departments` ← из `erp_departments` |
| 7 create_erp_task | `department_id`, `head_name`, `contractor_id` → Integration Service |

---

## Чеклист для 1С-разработчика

- [ ] OData: сущность контрагентов с email и department_codes  
- [ ] OData: сущность подразделений с head_name  
- [ ] Коды `department_id` совпадают с агентом и документом «Входящая корреспонденция»  
- [ ] Integration Service: API создания документа + задачи исполнения  
- [ ] Тестовая выгрузка в JSON формате `data/rag_catalog.example.json`

---

## История изменений

| Дата | Изменение |
|------|-----------|
| 2025-06-24 | Миграция 003, `catalog_repository`, sync → PostgreSQL, создан PROMT.md |
