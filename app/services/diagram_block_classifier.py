from __future__ import annotations

import re
from typing import Any

from app.models.enums import NdRelationType
from app.models.nd_control_structural import NdRelation
from app.schemas.diagram_block import DiagramBlockType

_START_KEYWORDS = (
    "инициация",
    "инициир",
    "потребность",
    "получение заявки",
    "создание заявки",
    "поступление документа",
    "начало процесса",
    "старт",
)
_END_KEYWORDS = (
    "завершение",
    "закрытие",
    "архивирование",
    "сдача в архив",
    "рассылка завершена",
    "ознакомление завершено",
    "окончание",
    "конец процесса",
)
_DECISION_KEYWORDS = (
    "если ",
    "если?",
    "при наличии",
    "при отсутствии",
    "согласовано?",
    "выявлены несоответствия?",
    "влияет?",
    "требуется?",
    "необходимо?",
    "утверждено?",
)
_SUBPROCESS_KEYWORDS = (
    "в соответствии с",
    "согласно ",
    "процесс описан в",
    "по инструкции",
    "по сто",
    "по регламенту",
    "по процедуре",
)
_DOCUMENT_OUTPUT_KEYWORDS = (
    "извещение",
    "служебная записка",
    "отчёт",
    "отчет",
    "лист",
    "журнал",
    "протокол",
    "приказ",
    "план",
    "форма",
    "запись",
    "документ",
    "акт ",
)
_CONNECTOR_KEYWORDS = ("продолжение на", "соединитель", "см. лист", "переход на лист")


def _normalize_text(*parts: str | None) -> str:
    return " ".join(part.strip().lower() for part in parts if part and str(part).strip())


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_diagram_block(
    action: dict[str, Any],
    relations: list[NdRelation] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    *,
    linked_subprocess_names: list[str] | None = None,
) -> tuple[DiagramBlockType, list[str]]:
    """Классификация блока блок-схемы по СТО-34-003 / ГОСТ 19.701-90."""
    warnings: list[str] = []
    title = _normalize_text(
        str(action.get("title") or action.get("name") or action.get("action") or ""),
        str(action.get("description") or ""),
    )
    if not title:
        return DiagramBlockType.OPERATION, ["Пустое действие классифицировано как operation"]

    if _contains_any(title, _CONNECTOR_KEYWORDS):
        return DiagramBlockType.CONNECTOR, warnings

    if _contains_any(title, _START_KEYWORDS):
        return DiagramBlockType.START, warnings

    if _contains_any(title, _END_KEYWORDS):
        return DiagramBlockType.END, warnings

    if _contains_any(title, _DECISION_KEYWORDS) or title.rstrip().endswith("?"):
        return DiagramBlockType.DECISION, warnings

    if linked_subprocess_names and any(name.lower() in title for name in linked_subprocess_names if name):
        return DiagramBlockType.SUBPROCESS, warnings

    if _contains_any(title, _SUBPROCESS_KEYWORDS):
        return DiagramBlockType.SUBPROCESS, warnings

    if action.get("used_forms") or action.get("output_objects") and _contains_any(title, _DOCUMENT_OUTPUT_KEYWORDS):
        return DiagramBlockType.DOCUMENT_OUTPUT, warnings

    if _contains_any(title, _DOCUMENT_OUTPUT_KEYWORDS):
        return DiagramBlockType.DOCUMENT_OUTPUT, warnings

    if relations:
        for relation in relations:
            if relation.relation_type == NdRelationType.PROCESS_RELATED_TO_PROCESS:
                name = relation.target_name or relation.source_name
                if name and name.lower() in title:
                    return DiagramBlockType.SUBPROCESS, warnings

    if evidence:
        for item in evidence:
            quote = _normalize_text(str(item.get("quote") or ""))
            if quote and _contains_any(quote, _SUBPROCESS_KEYWORDS):
                return DiagramBlockType.SUBPROCESS, warnings

    warnings.append(f"block_type для «{action.get('title') or action.get('name')}» определён как operation")
    return DiagramBlockType.OPERATION, warnings
