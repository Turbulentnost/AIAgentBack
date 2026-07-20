# Glyph + pytest: 6 ролевых агентов AIAgentBack

Дата: 2026-07-20  
Инструменты: `glyph-scan` 0.3.0 (AnalysisEngine с tools), `uv` + Python 3.11, pytest.

## Метод

1. Синтетические MCP-конфиги из промптов: `security/glyph/build_role_mcp_configs.py` → `generated/*.mcp.json` (в `.gitignore`).
2. Скан ролей: `security/glyph/run_role_scans.py` — **не** сырой `glyph scan` CLI: `ClaudeDesktopParser` игнорирует `tools[]`, поэтому runner явно наполняет `Tool.description` и гоняет те же 7 static rules.
3. Общий MCP 1С: `glyph scan app/agents/procurement_agent/mcp1C.json`.
4. Функционалка: `uv run pytest app/agents/<pkg>/tests/test_service.py --import-mode=importlib` (по пакету; общий basename `test_service` иначе ломает collection).

`glyph live` / `proxy` не запускались (ролевые агенты — не MCP-серверы; vendor `1c-odata-mcp` в репо нет). Семантическая модель ONNX Glyph: 404 при загрузке — `semantic-poisoning` фактически деградирует.

## Сводка по ролям

| Роль | Glyph exit | Findings | Triage | pytest |
|------|------------|----------|--------|--------|
| cfo_head_agent | 0 PASS | 0 | — | 19 passed |
| finance_director_agent | 0 PASS | 0 | — | 18 passed |
| executive_director_agent | 1 FAIL | 3× high `command-injection` «Java System Call» | **FP**: паттерн `\bexec` без trailing `\b` ловит `executive` / фрагменты слов | 17 passed |
| chief_accountant_agent | 0 PASS | 0 | — | 15 passed |
| accountant_agent | 1 FAIL | 1× high `command-injection` «Java System Call» | **FP**: `\bexec` в слове `execution` (payment execution flow) | 17 passed |
| legal_specialist_agent | 1 FAIL | 1× medium `command-injection` «Shell Metacharacters» | **FP**: backticks/markdown в system prompt, не shell | 19 passed |
| **mcp1C.json** | 0 PASS | 0 | плейсхолдеры `${ODATA_*}` не flagged | — |

**pytest итого:** 105 passed / 0 failed.

## Детали triage Glyph

### executive_director / accountant — «Java System Call»

Правило Glyph: `\b(?:Runtime\.getRuntime|ProcessBuilder|exec)` (без конца слова после `exec`).  
Совпадение: подстрока `exec` в `executive` и `execution`. Это **не** command injection в промпте. Правки промптов не требуются; при желании — upstream fix в Glyph или фильтр triage в `run_role_scans.py`.

### legal_specialist — «Shell Metacharacters»

Срабатывание на метасимволы + ключевые слова в легитимном тексте промпта (markdown/JSON-схема). **FP**, не эксплойт.

### prompt-injection / tool-poisoning / data-exfiltration

По всем 6 ролям — 0 findings (включая защиту `<untrusted_memo>`).

### mcp1C.json

Credential/transport чистые при `${VAR}`-плейсхолдерах. Live-скан реального 1c-odata — blocker (entrypoint/vendor не в репо; бэкенд на сервере).

## Next actions

1. Принять Glyph role-scan как security gate промптов; findings `command-injection` по `exec*` считать known FP (задокументированы здесь).
2. Опционально: в `run_role_scans.py` подавлять finding, если evidence ⊆ `executive|execution|execute` без `Runtime.getRuntime` / `ProcessBuilder`.
3. На сервере при наличии MCP 1С: `glyph live` с реальным entrypoint (вне этой волны).
4. pytest в CI: `uv run pytest … --import-mode=importlib` по пакетам или уникальные имена модулей тестов.

## Артефакты

| Путь | Содержимое |
|------|------------|
| `security/glyph/build_role_mcp_configs.py` | генератор MCP JSON |
| `security/glyph/run_role_scans.py` | Glyph engine + tools |
| `security/glyph/reports/*.glyph.json` | отчёты по ролям |
| `security/glyph/reports/roles_index.json` | индекс Glyph |
| `security/glyph/reports/mcp1C.glyph.json` | скан MCP 1С |
| `security/glyph/reports/pytest_roles.json` | pytest exit codes |
| `security/glyph/reports/pytest_roles.txt` | краткий текст pytest |

## Как повторить

```bash
cd AIAgentBack
uv sync --extra dev
python security/glyph/build_role_mcp_configs.py
python security/glyph/run_role_scans.py
glyph scan app/agents/procurement_agent/mcp1C.json --format json
uv run pytest app/agents/cfo_head_agent/tests/test_service.py --import-mode=importlib -q
# … остальные 5 пакетов аналогично
```
