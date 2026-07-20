# legal_specialist_agent — user prompt template

## Для редакторов (не в LLM)

Шаблон user-сообщения: слоты rag/case/memo/task. Плейсхолдеры подставляет код.
Язык: инструкции в task — EN; recommendation — RU; данные case/rag — как есть.

Дефолты при пустых значениях (см. `prompts/__init__.py`):
- `{rag}` → `(no relevant STO fragments)`
- `{memo}` → `(empty)`
- пустой `{task_hint}` → `(none)`

Минимум полей в `{case_json}`:
`case_id`, `supplier_id`, `open_advances_count`, `claim_status`, `claim_sla`, `code_suggested_action`, `risks`.

Пример `task_hint`:
`Claim work for supplier {id}, advances={n}.`

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
Prepare a recommendation for role legal_specialist.
Reply with ONLY JSON:
{{"recommendation":"...","rationale":"...","suggested_action":"...","needs_hitl":true,"norm_refs":["..."]}}
recommendation MUST be in Russian; JSON keys and suggested_action codes stay English.
needs_hitl must be true for claim decisions.
Do not send letters yourself — only approve_claim_draft / prepare_lawsuit / return for the human.
Also consider task_hint: {task_hint}
</task>
```
