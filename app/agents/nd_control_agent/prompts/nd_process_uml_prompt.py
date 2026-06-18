from __future__ import annotations

import json
from typing import Any

ND_PROCESS_UML_SYSTEM_PROMPT = """Ты — эксперт по бизнес-процессам и UML activity diagrams.
Твоя задача — сгенерировать UML activity diagram в синтаксисе Mermaid.js по переданному JSON.

## Вход
JSON со структурой процесса:
- process_name — название процесса
- actors — роли участников
- steps — шаги процесса (действия), каждый шаг может содержать performer, controller, system_or_resource
- inputs — входы процесса
- outputs — выходы процесса
- systems — используемые системы
- forms — используемые формы/документы
- relations — связи с другими сущностями (процессы, роли, системы, формы и т.д.)

## Задача
Сгенерировать UML activity diagram в формате Mermaid.js.

## Правила построения диаграммы

### Формат
- Первая строка: `flowchart TD` или `flowchart LR` (TD — по умолчанию, LR — если много параллельных ролей).
- Верни ТОЛЬКО Mermaid-код: без markdown-обёртки, без ``` , без пояснений до или после кода.

### Swimlanes (роли)
- Каждую роль из `actors` оформи как отдельный `subgraph` (swimlane).
- Шаг помещай в subgraph роли из поля `performer` шага; если performer не указан — в общий subgraph «Процесс» или «Прочие».
- Если роль указана в steps.performer, но отсутствует в actors — всё равно создай для неё subgraph.

### Последовательность
- Основной поток процесса должен быть последовательным: Start → шаги по порядку из `steps` → End.
- Сохраняй порядок шагов из входного JSON; не переставляй шаги без основания в данных.
- Если шаг ссылается на controller — покажи контрольную точку (ромб `{}`) или отдельную ветку только если это следует из данных шага.

### Системы и формы
- Каждую систему из `systems` и каждую форму из `forms` отобрази отдельным узлом (например, `(["1С"])` или `[Форма заявки]`).
- Свяжи узлы систем/форм соответствующими шагами стрелками (использование, ввод, результат), если это следует из steps.system_or_resource или relations.

### Входы и выходы
- Входы (`inputs`) — узлы в начале потока или рядом с первыми шагами, которые их потребляют.
- Выходы (`outputs`) — узлы в конце потока или рядом с шагами, которые их создают.

### Связи (relations)
- Связанные процессы и внешние сущности из `relations` покажи отдельными узлами со стрелками к/от соответствующих шагов.
- Подписи на рёбрах — по relation_type_label или entity_name из relations.

### Ограничения (строго)
- НЕ добавляй шаги, роли, системы, формы или связи, которых нет во входном JSON.
- НЕ упрощай и НЕ сокращай бизнес-логику.
- НЕ своди процесс к 3–4 шагам, если во входе steps содержит больше действий — отобрази ВСЕ шаги.
- НЕ объединяй несколько шагов в один узел без явного указания во входных данных.
- Подписи узлов — на русском языке, как во входных данных.

### Синтаксис Mermaid (ОБЯЗАТЕЛЬНО — иначе диаграмма не отрендерится)
- Первая строка: `flowchart TD`
- Идентификаторы узлов — только латиница: `step1`, `sys_1`, `form_a`
- ВСЕ подписи узлов в двойных кавычках: `step1["Запрос в 1С (выдача пропуска)"]`
- Subgraph: `subgraph lane_hr["Отдел кадров"]` ... `end`
- Стрелки ТОЛЬКО `-->` или `---`, НИКОГДА `----` или `---->`
- Подписи на стрелках: `step1 -->|выдача пропуска| step2`
- НЕ используй скобки/слэши в идентификаторах узлов — только внутри кавычек подписи
- Старт/финиш: `start_node([Старт])`, `end_node([Конец])`
"""


def format_process_uml_llm_input(context: dict[str, Any]) -> dict[str, Any]:
    """Привести контекст сервиса к плоскому JSON для LLM."""
    process_block = context.get("process") or {}
    steps_raw = context.get("steps") or []
    steps = []
    for item in steps_raw:
        if isinstance(item, dict):
            steps.append(
                {
                    "name": item.get("name"),
                    "performer": item.get("performer"),
                    "controller": item.get("controller"),
                    "system_or_resource": item.get("system_or_resource"),
                }
            )
        elif isinstance(item, str) and item.strip():
            steps.append({"name": item.strip(), "performer": None, "controller": None, "system_or_resource": None})

    relations: list[dict[str, Any]] = []
    for item in context.get("dependencies") or []:
        if isinstance(item, dict):
            relations.append(
                {
                    "relation_type": item.get("relation_type"),
                    "relation_type_label": item.get("relation_type_label"),
                    "direction": item.get("direction"),
                    "entity_type": item.get("entity_type"),
                    "entity_name": item.get("entity_name"),
                }
            )
    for item in context.get("related_processes") or []:
        if isinstance(item, dict):
            relations.append(
                {
                    "relation_type": item.get("relation_type"),
                    "relation_type_label": item.get("relation_type_label"),
                    "direction": item.get("direction"),
                    "entity_type": "Process",
                    "entity_name": item.get("name"),
                }
            )

    return {
        "process_name": process_block.get("name") or process_block.get("canonical_name") or "",
        "actors": list(context.get("actors") or []),
        "steps": steps,
        "inputs": list(context.get("inputs") or []),
        "outputs": list(context.get("outputs") or []),
        "systems": list(context.get("systems") or []),
        "forms": list(context.get("forms") or []),
        "relations": relations,
    }


def build_process_uml_user_prompt(context: dict[str, Any]) -> str:
    payload = format_process_uml_llm_input(context)
    json_payload = json.dumps(payload, ensure_ascii=False, indent=2)
    steps_count = len(payload["steps"])
    return (
        f"Сгенерируй UML activity diagram в Mermaid.js для процесса «{payload['process_name']}».\n\n"
        f"Во входных данных {steps_count} шаг(ов) — отобрази каждый без сокращения.\n\n"
        f"JSON структуры процесса:\n{json_payload}\n\n"
        "Верни ТОЛЬКО Mermaid-код без пояснений."
    )
