# finance_director_agent — user prompt template

## Для редакторов (не в LLM)

Шаблон user-сообщения: слоты rag/case/memo/task. Плейсхолдеры подставляет код.
Язык: инструкции в task — EN; recommendation — RU; данные case/rag — как есть.

Дефолты при пустых значениях (см. `prompts/__init__.py`):
- `{rag}` → `(no relevant STO fragments)`
- `{memo}` → `(empty)`
- пустой `{task_hint}` → `(none)`

Минимум полей в `{case_json}`:
`case_id`, `amount`, `s10_week_remaining`, `s10_ok`, `escalation_reason_code`, `trigger`, `risks`, `one_off`, `contract_status`, `project_price_valid_until`, `suggested_action` (code).

Пример `task_hint`:
`Finance exception {esc_code}: amount={amount}, S10 remaining={remaining}, s10_ok={s10_ok}.`

В шаблоне ниже слоты: `{rag}`, `{case_json}`, `{memo}`, `{task_hint}`.
Фигурные скобки JSON удвоены (`{{` / `}}`) — требование Python `str.format`.

## USER_PROMPT_TEMPLATE (в LLM после подстановки)

```
<rag>
{rag}
</rag>

<case>
{case_json}
</case>

<untrusted_memo>
{memo}
</untrusted_memo>

<task>
Prepare a recommendation for role finance_director.
Reply with ONLY JSON:
{{"recommendation":"...","rationale":"...","suggested_action":"...","needs_hitl":true,"norm_refs":["..."]}}
recommendation MUST be in Russian; JSON keys and suggested_action codes stay English.
needs_hitl must be true for financial exception decisions.
Do not propose pay/sign — only financial allow/deny/defer.
Also consider task_hint: {task_hint}
</task>
```
