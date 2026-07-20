# accountant_agent — user prompt template

## Для редакторов (не в LLM)

Шаблон user-сообщения: слоты rag/case/memo/task. Плейсхолдеры подставляет код.
Язык: инструкции в task — EN; recommendation — RU; данные case/rag — как есть.

Дефолты при пустых значениях (см. `prompts/__init__.py`):
- `{rag}` → `(no relevant STO fragments)`
- `{memo}` → `(empty)`
- пустой `{task_hint}` → `(none)`

Минимум полей в `{case_json}`:
`case_id`, `payment_request_id`, `payment_status`, `payment_delay_days`, `payment_planned_date`, `overdue`, `risks`, `code_suggested_action`.

Пример `task_hint`:
`Payment status {pr_id}: {status}, delay {n} days.`

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
Prepare a recommendation for role accountant.
Reply with ONLY JSON:
{{"recommendation":"...","rationale":"...","suggested_action":"...","needs_hitl":true,"norm_refs":["..."]}}
recommendation MUST be in Russian; JSON keys and suggested_action codes stay English.
needs_hitl must be true for payment decisions.
Do not propose pay/sign yourself — only mark_paid/defer/cancel/escalate_overdue for the human.
Also consider task_hint: {task_hint}
</task>
```
