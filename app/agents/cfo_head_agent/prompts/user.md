# cfo_head_agent — user prompt template

## Для редакторов (не в LLM)

Шаблон user-сообщения: слоты rag/case/memo/task. Плейсхолдеры подставляет код.
Язык: инструкции в task — EN; recommendation — RU; данные case/rag — как есть.

Дефолты при пустых значениях (см. `prompts.py`):
- `{rag}` → `(no relevant STO fragments)`
- `{memo}` → `(empty)`
- пустой `{task_hint}` → `(none)`

Минимум полей в `{case_json}`:
`case_id`, `cfo_code`, `payment_request_id`, `amount`, `ds_limit`, `ds_ok`, `payment_mode`, `staged_issue`, `suggested_payment_date` (если есть), `risks`, `expense_article`, `project` (если есть).

Пример `task_hint`:
`CFO {cfo_code} request amount={amount}. DS limit={ds_limit}. limit_exceeded={not ds_ok}.`

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
Prepare a recommendation for role cfo_head.
Reply with ONLY JSON:
{{"recommendation":"...","rationale":"...","suggested_action":"...","needs_hitl":true,"norm_refs":["..."]}}
recommendation MUST be in Russian; JSON keys and suggested_action codes stay English.
needs_hitl must be true for financial decisions.
Also consider task_hint: {task_hint}
</task>
```
