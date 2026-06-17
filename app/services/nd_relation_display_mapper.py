from __future__ import annotations

import re
import uuid
from typing import Any

from app.models.enums import (
    ConfidenceLevel,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
)
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ENTITY_TYPE_LABELS: dict[NdGraphEntityType, str] = {
    NdGraphEntityType.DEPARTMENT: "Отдел",
    NdGraphEntityType.PROCESS: "Процесс",
    NdGraphEntityType.DOCUMENT: "Документ",
    NdGraphEntityType.ROLE: "Роль",
    NdGraphEntityType.FORM: "Форма",
    NdGraphEntityType.SYSTEM: "Система",
    NdGraphEntityType.RESOURCE: "Ресурс",
}

RELATION_TYPE_LABELS: dict[NdRelationType, str] = {
    NdRelationType.DEPARTMENT_OWNS_PROCESS: "Отдел владеет процессом",
    NdRelationType.DEPARTMENT_PARTICIPATES_IN_PROCESS: "Отдел участвует в процессе",
    NdRelationType.DOCUMENT_REGULATES_PROCESS: "Документ регулирует процесс",
    NdRelationType.PROCESS_USES_FORM: "Процесс использует форму",
    NdRelationType.PROCESS_USES_SYSTEM: "Процесс использует систему",
    NdRelationType.PROCESS_HAS_ROLE: "В процессе есть роль",
    NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION: "Роль отвечает за действие",
    NdRelationType.PROCESS_PRODUCES_OUTPUT: "Процесс создаёт результат",
    NdRelationType.PROCESS_CONSUMES_INPUT: "Процесс использует вход",
    NdRelationType.PROCESS_RELATED_TO_PROCESS: "Процесс связан с процессом",
    NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT: "Документ упоминает отдел",
}

CONFIDENCE_LABELS: dict[ConfidenceLevel, str] = {
    ConfidenceLevel.HIGH: "Высокая",
    ConfidenceLevel.MEDIUM: "Средняя",
    ConfidenceLevel.LOW: "Низкая",
}

EXTRACTION_TYPE_LABELS: dict[NdRelationExtractionType, str] = {
    NdRelationExtractionType.EXPLICIT: "Явно из документа",
    NdRelationExtractionType.INFERRED: "Вывод агента",
    NdRelationExtractionType.UNCERTAIN: "Требует проверки",
}

PRIMARY_RELATION_TYPES: frozenset[NdRelationType] = frozenset(
    {
        NdRelationType.DEPARTMENT_OWNS_PROCESS,
        NdRelationType.DEPARTMENT_PARTICIPATES_IN_PROCESS,
        NdRelationType.DOCUMENT_REGULATES_PROCESS,
        NdRelationType.PROCESS_USES_FORM,
        NdRelationType.PROCESS_USES_SYSTEM,
        NdRelationType.PROCESS_HAS_ROLE,
        NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION,
    }
)

WEAK_RELATION_TYPES: frozenset[NdRelationType] = frozenset(
    {
        NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT,
    }
)

EVIDENCE_REQUIRED_TYPES: frozenset[NdRelationType] = frozenset(
    {
        NdRelationType.DEPARTMENT_OWNS_PROCESS,
        NdRelationType.DOCUMENT_REGULATES_PROCESS,
        NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION,
        NdRelationType.PROCESS_USES_FORM,
        NdRelationType.PROCESS_USES_SYSTEM,
    }
)

OWNERSHIP_EVIDENCE_KEYWORDS: tuple[str, ...] = (
    "ответственн",
    "контроль возлага",
    "отдел осуществ",
    "отдел обеспеч",
    "начальник отдел",
    "владелец процесс",
    "ответственным назнача",
)


def is_uuid_like(value: str | None) -> bool:
    if not value:
        return False
    return bool(UUID_RE.match(value.strip()))


def format_document_display_name(card: DocumentCard | None, fallback: str | None = None) -> str:
    if card is None:
        return fallback or "Документ без названия"
    return format_document_name_parts(card.document_code, card.title, card.file_name, fallback)


def format_document_name_parts(
    document_code: str | None,
    title: str | None = None,
    file_name: str | None = None,
    fallback: str | None = None,
) -> str:
    display_title = title or file_name
    if document_code and display_title:
        return f"{document_code} — {display_title}"
    if document_code:
        return document_code
    if display_title:
        return display_title
    return fallback or "Документ без названия"


def entity_fallback_name(entity_type: NdGraphEntityType) -> str:
    return {
        NdGraphEntityType.DOCUMENT: "Документ без названия",
        NdGraphEntityType.PROCESS: "Процесс без названия",
        NdGraphEntityType.DEPARTMENT: "Отдел без названия",
        NdGraphEntityType.ROLE: "Роль без названия",
        NdGraphEntityType.FORM: "Форма без названия",
        NdGraphEntityType.SYSTEM: "Система без названия",
        NdGraphEntityType.RESOURCE: "Ресурс без названия",
    }.get(entity_type, "Объект без названия")


def evidence_has_content(evidence_json: list | None) -> bool:
    if not evidence_json:
        return False
    for item in evidence_json:
        if not isinstance(item, dict):
            continue
        if item.get("quote") or item.get("section") or item.get("document_code"):
            return True
        if item.get("source") == "department_profile_build":
            return False
    return False


def evidence_summary(evidence_json: list | None) -> str | None:
    if not evidence_json:
        return None
    for item in evidence_json:
        if not isinstance(item, dict):
            continue
        code = item.get("document_code")
        section = item.get("section")
        quote = item.get("quote")
        parts: list[str] = []
        if code:
            parts.append(str(code))
        if section:
            parts.append(f"Раздел {section}")
        if quote:
            text = str(quote).strip()
            parts.append(text[:120] + ("…" if len(text) > 120 else ""))
        if parts:
            return ": ".join(parts)
    return None


def has_ownership_evidence(evidence_json: list | None) -> bool:
    if not evidence_json:
        return False
    blob = " ".join(
        str(item.get("quote") or item.get("section") or "")
        for item in evidence_json
        if isinstance(item, dict)
    ).lower()
    return any(keyword in blob for keyword in OWNERSHIP_EVIDENCE_KEYWORDS)


class RelationResolutionCache:
    def __init__(self) -> None:
        self.documents_by_id: dict[uuid.UUID, DocumentCard] = {}
        self.processes_by_id: dict[uuid.UUID, ProcessCard] = {}
        self.departments_by_id: dict[uuid.UUID, str] = {}

    def resolve_name(
        self,
        entity_type: NdGraphEntityType,
        entity_id: uuid.UUID | None,
        stored_name: str | None,
    ) -> str:
        if stored_name and not is_uuid_like(stored_name):
            return stored_name.strip()

        if entity_type == NdGraphEntityType.DOCUMENT and entity_id:
            card = self.documents_by_id.get(entity_id)
            if card:
                return format_document_display_name(card)
        if entity_type == NdGraphEntityType.PROCESS and entity_id:
            process = self.processes_by_id.get(entity_id)
            if process and process.canonical_name:
                return process.canonical_name
        if entity_type == NdGraphEntityType.DEPARTMENT:
            if entity_id and entity_id in self.departments_by_id:
                return self.departments_by_id[entity_id]
            if stored_name and not is_uuid_like(stored_name):
                return stored_name

        if stored_name and not is_uuid_like(stored_name):
            return stored_name
        return entity_fallback_name(entity_type)


def build_relation_description(
    *,
    source_label: str,
    source_name: str,
    relation_label: str,
    target_label: str,
    target_name: str,
    extraction_type: NdRelationExtractionType,
    has_evidence: bool,
    document_code: str | None = None,
) -> str:
    if extraction_type == NdRelationExtractionType.EXPLICIT and document_code:
        return (
            f"В документе {document_code} явно указано, что «{source_name}» "
            f"связан с «{target_name}» ({relation_label.lower()})."
        )
    if extraction_type == NdRelationExtractionType.EXPLICIT:
        return f"«{source_name}» явно связан с «{target_name}» ({relation_label.lower()})."
    if not has_evidence:
        return (
            f"«{source_name}» предположительно связан с «{target_name}» "
            f"({relation_label.lower()}), но основание не найдено. Требуется проверка."
        )
    return (
        f"«{source_name}» предположительно связан с «{target_name}» "
        f"({relation_label.lower()}). Агент сделал вывод по тексту документа — требуется подтверждение."
    )


def relation_display_flags(relation: NdRelation) -> dict[str, bool]:
    has_evidence = evidence_has_content(relation.evidence_json)
    is_weak = relation.relation_type in WEAK_RELATION_TYPES
    is_service = is_weak or (
        relation.extraction_type == NdRelationExtractionType.INFERRED
        and not has_evidence
        and relation.relation_type not in PRIMARY_RELATION_TYPES
    )
    is_primary = relation.relation_type in PRIMARY_RELATION_TYPES and not is_service
    requires_review = (
        not relation.is_confirmed
        and (
            not has_evidence
            and relation.relation_type in EVIDENCE_REQUIRED_TYPES
            or relation.extraction_type in {NdRelationExtractionType.INFERRED, NdRelationExtractionType.UNCERTAIN}
            or is_weak
        )
    )
    can_bulk_approve = (
        not relation.is_confirmed
        and relation.extraction_type == NdRelationExtractionType.EXPLICIT
        and relation.confidence == ConfidenceLevel.HIGH
        and has_evidence
        and relation.relation_type in PRIMARY_RELATION_TYPES
    )
    return {
        "has_evidence": has_evidence,
        "is_weak_relation": is_weak,
        "is_service_relation": is_service,
        "is_primary_relation": is_primary,
        "requires_review": requires_review,
        "can_bulk_approve": can_bulk_approve,
    }


def map_relation_to_display(relation: NdRelation, cache: RelationResolutionCache) -> dict[str, Any]:
    flags = relation_display_flags(relation)
    source_name = cache.resolve_name(relation.source_type, relation.source_id, relation.source_name)
    target_name = cache.resolve_name(relation.target_type, relation.target_id, relation.target_name)
    source_type_label = ENTITY_TYPE_LABELS.get(relation.source_type, relation.source_type.value)
    target_type_label = ENTITY_TYPE_LABELS.get(relation.target_type, relation.target_type.value)
    relation_label = RELATION_TYPE_LABELS.get(relation.relation_type, relation.relation_type.value)
    confidence_label = CONFIDENCE_LABELS.get(relation.confidence, relation.confidence.value)
    extraction_label = EXTRACTION_TYPE_LABELS.get(
        relation.extraction_type, relation.extraction_type.value
    )
    confirmation_label = "Подтверждено" if relation.is_confirmed else "Не подтверждено"
    review_status = "approved" if relation.is_confirmed else "pending"
    review_status_label = "Подтверждено" if relation.is_confirmed else "На проверке"
    evidence = relation.evidence_json or []
    first_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    summary = evidence_summary(evidence)
    document_code = first_evidence.get("document_code")

    description = build_relation_description(
        source_label=source_type_label,
        source_name=source_name,
        relation_label=relation_label,
        target_label=target_type_label,
        target_name=target_name,
        extraction_type=relation.extraction_type,
        has_evidence=flags["has_evidence"],
        document_code=document_code,
    )

    return {
        "relation_id": relation.id,
        "source_type": relation.source_type.value,
        "source_type_label": source_type_label,
        "source_id": relation.source_id,
        "source_display_name": source_name,
        "source": {
            "type": relation.source_type.value,
            "type_label": source_type_label,
            "id": str(relation.source_id) if relation.source_id else None,
            "name": source_name,
        },
        "relation_type": relation.relation_type.value,
        "relation_type_label": relation_label,
        "relation": {
            "type": relation.relation_type.value,
            "label": relation_label,
        },
        "target_type": relation.target_type.value,
        "target_type_label": target_type_label,
        "target_id": relation.target_id,
        "target_display_name": target_name,
        "target": {
            "type": relation.target_type.value,
            "type_label": target_type_label,
            "id": str(relation.target_id) if relation.target_id else None,
            "name": target_name,
        },
        "confidence": relation.confidence.value,
        "confidence_label": confidence_label,
        "extraction_type": relation.extraction_type.value,
        "extraction_type_label": extraction_label,
        "confirmation_status": "confirmed" if relation.is_confirmed else "pending",
        "confirmation_status_label": confirmation_label,
        "is_confirmed": relation.is_confirmed,
        "review_status": review_status,
        "review_status_label": review_status_label,
        "evidence_summary": summary,
        "evidence_json": evidence,
        "evidence": {
            "label": summary or "Нет основания",
            "document_code": document_code,
            "section": first_evidence.get("section"),
            "quote": first_evidence.get("quote"),
        },
        "relation_description": description,
        "created_at": relation.created_at,
        **flags,
    }
