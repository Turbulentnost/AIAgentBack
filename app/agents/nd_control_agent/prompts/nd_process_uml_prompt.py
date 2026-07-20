from __future__ import annotations

import json
from typing import Any

from app.schemas.process_smk_sections import DiagramDetailLevel
from app.services.process_uml_detail import detail_level_prompt_hint
from app.services.process_uml_document_profile import document_type_prompt_hint

ND_PROCESS_UML_SYSTEM_PROMPT = """Ты генерируешь блок-схему процесса СМК по требованиям СТО-34-003 и ГОСТ 19.701-90.

Нужно использовать Mermaid flowchart.

## Обязательные правила

1. Перед построением учитывай source_document_type и qms_level из process_graph.
2. Для Политики без actions не выдумывай операции; допустима логическая схема: намерение → область → обязательства → ответственность.
3. Для Положения основной акцент — функции и ответственность, а не только линейный поток.
4. Для Регламента показывай последовательность шагов, роли, формы, передачу данных и условия.
5. Для СТО основной поток строится только по actions[]; критерии, ресурсы и риски показывай только в detailed mode как справочную информацию.
6. Для Инструкции показывай операционные действия исполнителя, системы и формы.
7. Операции отображать прямоугольниками.
8. Условия отображать decision-узлами с ветками «Да» и «Нет».
9. Формирование документов, извещений, листов, отчётов, приказов, протоколов и записей отображать как отдельные документные блоки.
10. Ссылки на другие процессы или инструкции отображать как subprocess.
11. Роли должны быть сгруппированы в swimlane/subgraph, если они есть во входных данных.
12. Нельзя выдумывать действия, роли, документы, формы или системы.
13. Если входных данных недостаточно, построй схему только по имеющимся actions и добавь комментарии `%%` в Mermaid.
14. Не удаляй контрольные точки.
15. Не объединяй согласование, проверку и утверждение в один блок.
16. Ветка с отказом, несоответствием или отсутствием данных должна возвращать процесс на доработку или ручную проверку.
17. Архивирование, рассылка, ознакомление и регистрация изменений — отдельные блоки только если они есть в actions[] как реальные действия.
18. Критерии результативности, ресурсы, риски, приложения, лист регистрации изменений и лист выдачи/ознакомления — это metadata/reference, а не шаги процесса.
19. Metadata/reference НЕЛЬЗЯ превращать в операции основного процесса.
20. Риск, связанный с action (related_action_id), в detailed mode можно связать пунктиром или комментарием `%% risk -> action_id`.
21. Приложения/forms показывай как document_output только если они реально создаются или используются конкретным action.

## Элементы по ГОСТ 19.701-90

| block_type | Mermaid |
|------------|---------|
| start | `start_node([Начало])` |
| end | `end_node([Конец])` |
| operation | `step1["Операция"]` |
| decision | `dec1{"Условие?"}` с `-->|Да|` и `-->|Нет|` |
| subprocess | `sub1[["Подпроцесс: имя"]]` или отдельный блок с подписью «Подпроцесс: …» |
| document_output | `doc1["Документ: Извещение об изменении"]` |
| connector | `conn1(("Соединитель"))` |

## Mermaid требования

- Использовать `flowchart TD` или `flowchart LR`.
- Для ролей использовать `subgraph lane_role["Роль"]`.
- Для условий использовать узлы с фигурными скобками: `A{"Условие?"}`.
- Для начала/конца использовать закруглённые узлы: `A([Начало])`.
- Для операции использовать обычные прямоугольники.
- Для документа использовать подпись «Документ: …» в названии узла.
- Идентификаторы узлов — только латиница: `step1`, `dec1`, `doc1`.
- ВСЕ подписи узлов в двойных кавычках.
- Стрелки ТОЛЬКО `-->` или `-->|подпись|-->`.
- НЕ используй цепочки подписей на стрелках: `-->|step1|-->|step2|` — это невалидный синтаксис.
- Для последовательности шагов пиши: `step1 --> step2 --> step3` или `A -->|Да| step2`.
- НЕ использовать сырые UUID.
- НЕ возвращать Markdown.
- НЕ возвращать пояснения.
- Возвращать только Mermaid code.
- Диаграмма ОБЯЗАНА иметь связанный путь `start_node --> ... --> end_node`.
- `start_node` всегда имеет исходящую стрелку к первому действию процесса.
- `end_node` всегда имеет входящую стрелку из последнего действия/результата процесса.
- Запрещены orphan nodes: каждый объявленный узел должен участвовать хотя бы в одной связи.
- В detailed mode справочные узлы размещай в `subgraph info["Справочная информация"]` и связывай пунктиром `main_process -.-> resources`.
- В compact/standard mode не выводи отдельные узлы «Ресурсы», «Критерии результативности», «Риски и меры контроля», если они не являются actions[].

## Входной JSON

Поле `process_graph` содержит нормативную структуру процесса:
- process_name, process_goal, process_owner
- source_document_type, source_document_type_label, qms_level, qms_level_label, diagram_profile_label
- primary_document_type, source_document_types[]
- actions[] с block_type, responsible_role, used_forms, used_systems, input_objects, output_objects
- roles, forms, systems, documents, inputs, outputs, conditions, subprocesses
- process_metadata с reference/metadata секциями
- effectiveness_criteria[], resources[], risks[], documentation_and_archive[], applications[] только для detailed mode
- change_registration[], issue_and_acquaintance[], storage_locations[], retention_terms[]

Строй основной поток строго по actions[] в указанном порядке, учитывая block_type каждого действия.
Уровень детализации задаётся полем diagram_detail_level (compact | standard | detailed).
"""


def format_process_uml_llm_input(context: dict[str, Any]) -> dict[str, Any]:
    process_graph = context.get("process_graph")
    if isinstance(process_graph, dict):
        if "standard_profile" not in process_graph:
            return {**process_graph, "standard_profile": "STO-34-003_GOST-19.701-90"}
        return process_graph

    process_block = context.get("process") or {}
    actions_raw = context.get("actions") or context.get("steps") or []
    actions: list[dict[str, Any]] = []
    for item in actions_raw:
        if isinstance(item, dict):
            actions.append(item)
        elif isinstance(item, str) and item.strip():
            actions.append(
                {
                    "id": f"action_{len(actions) + 1}",
                    "title": item.strip(),
                    "block_type": "operation",
                    "responsible_role": None,
                }
            )

    return {
        "process_name": process_block.get("name") or process_block.get("canonical_name") or "",
        "process_goal": process_block.get("goal"),
        "process_owner": process_block.get("owner"),
        "roles": list(context.get("roles") or context.get("actors") or []),
        "actions": actions,
        "inputs": list(context.get("inputs") or []),
        "outputs": list(context.get("outputs") or []),
        "systems": list(context.get("systems") or []),
        "forms": list(context.get("forms") or []),
        "documents": list(context.get("documents") or []),
        "resources": list(context.get("resources") or []),
        "risks": list(context.get("risks") or []),
        "effectiveness_criteria": list(context.get("effectiveness_criteria") or []),
        "process_metadata": dict(context.get("process_metadata") or {}),
        "documentation_and_archive": list(context.get("documentation_and_archive") or []),
        "applications": list(context.get("applications") or []),
        "change_registration": list(context.get("change_registration") or []),
        "issue_and_acquaintance": list(context.get("issue_and_acquaintance") or []),
        "storage_locations": list(context.get("storage_locations") or []),
        "retention_terms": list(context.get("retention_terms") or []),
        "responsible_for_storage": list(context.get("responsible_for_storage") or []),
        "measurement_methods": list(context.get("measurement_methods") or []),
        "conditions": list(context.get("conditions") or []),
        "subprocesses": list(context.get("subprocesses") or context.get("related_processes") or []),
        "external_references": list(context.get("external_references") or []),
        "warnings": list(context.get("warnings") or []),
        "standard_profile": context.get("standard_profile") or "STO-34-003_GOST-19.701-90",
        "diagram_detail_level": context.get("diagram_detail_level") or context.get("detail_level") or "standard",
    }


def build_process_uml_user_prompt(
    context: dict[str, Any],
    *,
    detail_level: DiagramDetailLevel = DiagramDetailLevel.STANDARD,
) -> str:
    payload = format_process_uml_llm_input(context)
    payload["diagram_detail_level"] = detail_level.value
    json_payload = json.dumps({"process_graph": payload}, ensure_ascii=False, indent=2)
    actions_count = len(payload.get("actions") or [])
    detail_hint = detail_level_prompt_hint(detail_level)
    document_hint = document_type_prompt_hint(
        payload.get("source_document_type") or payload.get("primary_document_type")
    )
    return (
        f"Сгенерируй блок-схему процесса СМК «{payload.get('process_name', '')}» "
        f"по СТО-34-003 / ГОСТ 19.701-90.\n\n"
        f"{document_hint}\n\n"
        f"{detail_hint}\n\n"
        f"Во входных данных {actions_count} action(s) — отобрази каждый согласно block_type без сокращения.\n\n"
        "Построй единый связанный основной поток: Начало → действия/условия → документированный результат → Конец. "
        "Не создавай узлы из metadata/reference в основном потоке. "
        "Если diagram_detail_level=detailed и добавляешь справочные блоки, помести их в subgraph "
        "«Справочная информация» и свяжи пунктиром с узлом основного процесса. "
        "В Mermaid не должно быть ни одного узла без входящей или исходящей связи.\n\n"
        f"JSON process_graph:\n{json_payload}\n\n"
        "Верни ТОЛЬКО Mermaid-код без пояснений."
    )
