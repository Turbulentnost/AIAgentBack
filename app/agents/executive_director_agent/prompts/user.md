# executive_director_agent — user prompt template

## Для редакторов (не в LLM)

Шаблон user-сообщения: слоты rag/case/memo/task. Плейсхолдеры подставляет код.
Язык: инструкции в task — EN; recommendation — RU; данные case/rag — как есть.

Дефолты при пустых значениях (см. `prompts/__init__.py`):
- `{rag}` → `(no relevant STO fragments)`
- `{memo}` → `(empty)`
- пустой `{task_hint}` → `(none)`

Минимум полей в `{case_json}`:
`case_id`, `registry_id`, `lines_count`, `missing_cfo`, `registry_deadline_passed`, `line_priorities`, `risks`, `code_suggested_action`.

Пример `task_hint`:
`Payment registry {registry_id}: lines={n}, missing_cfo={k}, deadline_passed={bool}.`

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
Prepare a recommendation for role executive_director.
Reply with ONLY JSON:
{{"recommendation":"...","rationale":"...","suggested_action":"...","needs_hitl":true,"norm_refs":["..."]}}
recommendation MUST be in Russian; JSON keys and suggested_action codes stay English.
needs_hitl must be true for registry decisions.
Do not propose pay/sign — only approve registry or return to OMTO.
Also consider task_hint: {task_hint}
</task>
```
