from __future__ import annotations

from app.models.enums import NdDocumentLevel, NdStructuralDocumentType

# Алиасы по спецификации продукта (DocumentType / DocumentLevel).
DocumentType = NdStructuralDocumentType
DocumentLevel = NdDocumentLevel

DOCUMENT_TYPE_LABELS: dict[NdStructuralDocumentType, str] = {
    NdStructuralDocumentType.POLICY: "Политика",
    NdStructuralDocumentType.REGULATION: "Положение",
    NdStructuralDocumentType.PROCESS_REGULATION: "Регламент",
    NdStructuralDocumentType.STO: "СТО",
    NdStructuralDocumentType.INSTRUCTION: "Инструкция",
}

DOCUMENT_LEVEL_LABELS: dict[NdDocumentLevel, str] = {
    NdDocumentLevel.STRATEGIC: "Стратегический",
    NdDocumentLevel.ORGANIZATIONAL: "Организационный",
    NdDocumentLevel.PROCESS: "Процессный",
    NdDocumentLevel.TECHNICAL: "Технический",
    NdDocumentLevel.OPERATIONAL: "Операционный",
}

DOCUMENT_TYPE_TO_LEVEL: dict[NdStructuralDocumentType, NdDocumentLevel] = {
    NdStructuralDocumentType.POLICY: NdDocumentLevel.STRATEGIC,
    NdStructuralDocumentType.REGULATION: NdDocumentLevel.ORGANIZATIONAL,
    NdStructuralDocumentType.PROCESS_REGULATION: NdDocumentLevel.PROCESS,
    NdStructuralDocumentType.STO: NdDocumentLevel.TECHNICAL,
    NdStructuralDocumentType.INSTRUCTION: NdDocumentLevel.OPERATIONAL,
}

LEGACY_DOCUMENT_TYPE_VALUES: dict[str, NdStructuralDocumentType] = {
    "position": NdStructuralDocumentType.REGULATION,
    "POSITION": NdStructuralDocumentType.REGULATION,
    "procedure": NdStructuralDocumentType.PROCESS_REGULATION,
    "PROCEDURE": NdStructuralDocumentType.PROCESS_REGULATION,
    "form": NdStructuralDocumentType.INSTRUCTION,
    "FORM": NdStructuralDocumentType.INSTRUCTION,
    "other": NdStructuralDocumentType.INSTRUCTION,
    "OTHER": NdStructuralDocumentType.INSTRUCTION,
    "policy": NdStructuralDocumentType.POLICY,
    "regulation": NdStructuralDocumentType.REGULATION,
    "process_regulation": NdStructuralDocumentType.PROCESS_REGULATION,
    "process-regulation": NdStructuralDocumentType.PROCESS_REGULATION,
    "sto": NdStructuralDocumentType.STO,
    "instruction": NdStructuralDocumentType.INSTRUCTION,
}


def get_document_level(document_type: NdStructuralDocumentType | None) -> NdDocumentLevel | None:
    if document_type is None:
        return None
    return DOCUMENT_TYPE_TO_LEVEL.get(document_type)


def get_document_type_label(document_type: NdStructuralDocumentType | str | None) -> str | None:
    if document_type is None:
        return None
    if isinstance(document_type, str):
        try:
            document_type = NdStructuralDocumentType(document_type)
        except ValueError:
            legacy = LEGACY_DOCUMENT_TYPE_VALUES.get(document_type)
            if legacy is None:
                return None
            document_type = legacy
    return DOCUMENT_TYPE_LABELS.get(document_type)


def get_document_level_label(document_level: NdDocumentLevel | str | None) -> str | None:
    if document_level is None:
        return None
    if isinstance(document_level, str):
        try:
            document_level = NdDocumentLevel(document_level)
        except ValueError:
            return None
    return DOCUMENT_LEVEL_LABELS.get(document_level)


def sync_document_card_level(card) -> None:
    """Пересчитать document_level по document_type на карточке DocumentCard."""
    level = get_document_level(card.document_type)
    card.document_level = level.value if level else None
