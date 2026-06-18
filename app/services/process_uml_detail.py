from __future__ import annotations

from typing import Any

from app.schemas.diagram_block import DiagramBlockType
from app.schemas.process_smk_sections import DiagramDetailLevel


def apply_detail_level_to_context(context: dict[str, Any], detail_level: DiagramDetailLevel) -> dict[str, Any]:
    """Отфильтровать process_graph для LLM по уровню детализации."""
    process_graph = dict(context.get("process_graph") or {})
    actions = list(process_graph.get("actions") or [])

    if detail_level == DiagramDetailLevel.COMPACT:
        process_graph["actions"] = [
            action
            for action in actions
            if action.get("block_type")
            in {
                DiagramBlockType.START.value,
                DiagramBlockType.OPERATION.value,
                DiagramBlockType.DECISION.value,
                DiagramBlockType.END.value,
                DiagramBlockType.CONNECTOR.value,
            }
        ]
        for key in (
            "roles",
            "forms",
            "systems",
            "documents",
            "resources",
            "risks",
            "effectiveness_criteria",
            "documentation_and_archive",
            "applications",
            "change_registration",
            "issue_and_acquaintance",
            "subprocesses",
            "external_references",
            "storage_locations",
            "retention_terms",
            "responsible_for_storage",
            "measurement_methods",
            "process_metadata",
        ):
            process_graph[key] = {} if key == "process_metadata" else []

    elif detail_level == DiagramDetailLevel.STANDARD:
        for key in (
            "resources",
            "risks",
            "effectiveness_criteria",
            "documentation_and_archive",
            "applications",
            "change_registration",
            "issue_and_acquaintance",
            "storage_locations",
            "retention_terms",
            "responsible_for_storage",
            "measurement_methods",
            "process_metadata",
        ):
            process_graph[key] = {} if key == "process_metadata" else []

    filtered = dict(context)
    filtered["process_graph"] = process_graph
    filtered["detail_level"] = detail_level.value
    filtered["diagram_detail_level"] = detail_level.value
    return filtered


def detail_level_prompt_hint(detail_level: DiagramDetailLevel) -> str:
    if detail_level == DiagramDetailLevel.COMPACT:
        return (
            "Режим compact: построй только основной поток — начало, операции, условия, конец. "
            "Не добавляй блоки рисков, ресурсов, критериев и архивирования."
        )
    if detail_level == DiagramDetailLevel.STANDARD:
        return (
            "Режим standard: основной поток + документы + роли + системы + формы + связанные процессы. "
            "Не добавляй отдельные блоки рисков, ресурсов, критериев результативности, приложений, "
            "листов регистрации изменений, листов выдачи/ознакомления и архивирования, если они не являются action."
        )
    return (
        "Режим detailed: включи основной поток и дополнительные блоки: "
        "критерии результативности, ресурсы, риски и меры контроля, документирование и архивирование. "
        "Справочные элементы размести в subgraph «Справочная информация» и свяжи пунктирными линиями "
        "с узлом основного процесса. Не оставляй справочные узлы несвязанными."
    )
