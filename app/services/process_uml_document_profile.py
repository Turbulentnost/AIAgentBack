from __future__ import annotations

from app.models.enums import NdDocumentLevel, NdStructuralDocumentType
from app.schemas.nd_process_graph import ProcessGraphDTO
from app.utils.smk_document_classification import (
    DOCUMENT_LEVEL_LABELS,
    DOCUMENT_TYPE_LABELS,
    DOCUMENT_TYPE_TO_LEVEL,
    get_document_level_label,
    get_document_type_label,
)

# Приоритет выбора основного документа при нескольких источниках.
_PRIMARY_DOCUMENT_TYPE_PRIORITY: tuple[NdStructuralDocumentType, ...] = (
    NdStructuralDocumentType.STO,
    NdStructuralDocumentType.PROCESS_REGULATION,
    NdStructuralDocumentType.INSTRUCTION,
    NdStructuralDocumentType.REGULATION,
    NdStructuralDocumentType.POLICY,
)

DIAGRAM_PROFILE_LABELS: dict[NdStructuralDocumentType, str] = {
    NdStructuralDocumentType.POLICY: "Схема обязательств и ответственности",
    NdStructuralDocumentType.REGULATION: "Схема функций и ответственности",
    NdStructuralDocumentType.PROCESS_REGULATION: "Алгоритм взаимодействия",
    NdStructuralDocumentType.STO: "Процессная схема с требованиями и контролем",
    NdStructuralDocumentType.INSTRUCTION: "Операционная схема выполнения",
}

DOCUMENT_TYPE_PROMPT_RULES: dict[NdStructuralDocumentType, str] = {
    NdStructuralDocumentType.POLICY: (
        "Политика — строи схему обязательств и ответственности. "
        "Покажи стратегические намерения, область распространения, обязательства, "
        "связанные процессы и ответственных за реализацию политики. "
        "Не придумывай операции, если их нет во входных actions."
    ),
    NdStructuralDocumentType.REGULATION: (
        "Положение — строй схему функций и ответственности. "
        "Покажи структуру управления, функции, зоны ответственности, "
        "взаимодействие подразделений, права и ответственность. "
        "Основной акцент: кто за что отвечает."
    ),
    NdStructuralDocumentType.PROCESS_REGULATION: (
        "Регламент — строи последовательный алгоритм взаимодействия. "
        "Покажи последовательность шагов, роли участников, сроки, формы, "
        "передачу данных между участниками, условия и ветвления. "
        "Основной акцент: кто → что делает → кому передаёт → когда → по какой форме."
    ),
    NdStructuralDocumentType.STO: (
        "СТО — строй процессную схему по actions[] с контрольными точками. "
        "Критерии результативности, риски, ресурсы, документирование и архивирование не являются "
        "шагами основного процесса; показывай их только в detailed mode как связанную справочную информацию. "
        "Основной акцент: по каким правилам и с каким контролем выполняется процесс."
    ),
    NdStructuralDocumentType.INSTRUCTION: (
        "Инструкция — строй операционную схему выполнения для исполнителя. "
        "Покажи конкретные действия, порядок операций, используемые системы и формы, результат выполнения. "
        "Основной акцент: как конкретному работнику выполнить действие."
    ),
}


def _parse_document_type(value: str | NdStructuralDocumentType | None) -> NdStructuralDocumentType | None:
    if value is None:
        return None
    if isinstance(value, NdStructuralDocumentType):
        return value
    try:
        return NdStructuralDocumentType(value)
    except ValueError:
        return None


def select_primary_document_type(
    document_types: list[NdStructuralDocumentType],
) -> NdStructuralDocumentType | None:
    if not document_types:
        return None
    unique = list(dict.fromkeys(document_types))
    if len(unique) == 1:
        return unique[0]
    for candidate in _PRIMARY_DOCUMENT_TYPE_PRIORITY:
        if candidate in unique:
            return candidate
    return unique[0]


def get_diagram_profile_label(document_type: NdStructuralDocumentType | str | None) -> str | None:
    parsed = _parse_document_type(document_type)
    if parsed is None:
        return None
    return DIAGRAM_PROFILE_LABELS.get(parsed)


def document_type_context_fields(
    document_type: NdStructuralDocumentType | None,
) -> dict[str, str | None]:
    if document_type is None:
        return {
            "source_document_type": None,
            "source_document_type_label": None,
            "qms_level": None,
            "qms_level_label": None,
            "diagram_profile_label": None,
        }
    level = DOCUMENT_TYPE_TO_LEVEL.get(document_type)
    return {
        "source_document_type": document_type.value,
        "source_document_type_label": DOCUMENT_TYPE_LABELS.get(document_type),
        "qms_level": level.value if level else None,
        "qms_level_label": DOCUMENT_LEVEL_LABELS.get(level) if level else None,
        "diagram_profile_label": DIAGRAM_PROFILE_LABELS.get(document_type),
    }


def document_type_prompt_hint(document_type: NdStructuralDocumentType | str | None) -> str:
    parsed = _parse_document_type(document_type)
    if parsed is None:
        return (
            "Перед построением диаграммы учитывай source_document_type из process_graph. "
            "Если тип не указан, используй только фактические данные process_graph без выдуманных операций."
        )
    rule = DOCUMENT_TYPE_PROMPT_RULES.get(parsed, "")
    label = get_document_type_label(parsed) or parsed.value
    profile = get_diagram_profile_label(parsed) or "диаграмма процесса"
    return (
        f"Перед построением диаграммы учитывай source_document_type: {label} ({parsed.value}). "
        f"Профиль диаграммы: {profile}. {rule}"
    )


def has_operational_actions(graph: ProcessGraphDTO) -> bool:
    return bool(graph.actions)
