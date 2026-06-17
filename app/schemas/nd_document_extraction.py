from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConfidenceLevel, NdResponsibilityRoleType, NdUnknownReason

_STRICT_CONFIG = ConfigDict(extra="forbid")


class ExtractionBaseModel(BaseModel):
    model_config = _STRICT_CONFIG


class Evidence(ExtractionBaseModel):
    document_id: str | None = None
    document_code: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None


class Participant(ExtractionBaseModel):
    name: str | None = None
    role: str | None = None
    department: str | None = None
    date: str | None = None
    evidence: Evidence | None = None


class DocumentScopeExtraction(ExtractionBaseModel):
    text: str | None = None
    departments: list[str] = Field(default_factory=list)
    positions: list[str] = Field(default_factory=list)
    applies_to_all_company: bool = False


class DocumentMetaExtraction(ExtractionBaseModel):
    document_code: str | None = None
    title: str | None = None
    document_type: str | None = None
    document_type_confidence: ConfidenceLevel | None = None
    version: str | None = None
    status: str | None = None
    approval_date: str | None = None
    effective_date: str | None = None
    purpose: str | None = None
    scope: DocumentScopeExtraction = Field(default_factory=DocumentScopeExtraction)


class ParticipantsExtraction(ExtractionBaseModel):
    developed_by: list[Participant] = Field(default_factory=list)
    checked_by: list[Participant] = Field(default_factory=list)
    approved_by: list[Participant] = Field(default_factory=list)
    agreed_by: list[Participant] = Field(default_factory=list)


class ActionExtraction(ExtractionBaseModel):
    action: str
    performer: str | None = None
    controller: str | None = None
    deadline: str | None = None
    system_or_resource: str | None = None
    input: str | None = None
    output: str | None = None
    evidence: Evidence | None = None


class OwnerCandidate(ExtractionBaseModel):
    name_or_role: str
    reason: str
    confidence: ConfidenceLevel
    evidence: Evidence | None = None


class ProcessExtraction(ExtractionBaseModel):
    name: str
    description: str | None = None
    goal: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    actions: list[ActionExtraction] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    related_departments: list[str] = Field(default_factory=list)
    owner_candidates: list[OwnerCandidate] = Field(default_factory=list)


class ResponsibilityExtraction(ExtractionBaseModel):
    subject: str
    responsibility: str
    role_type: NdResponsibilityRoleType
    confidence: ConfidenceLevel
    evidence: Evidence | None = None


class FormExtraction(ExtractionBaseModel):
    name: str
    code: str | None = None
    purpose: str | None = None
    related_process: str | None = None
    evidence: Evidence | None = None


class UnknownItem(ExtractionBaseModel):
    field: str
    reason: NdUnknownReason
    description: str | None = None


class DocumentExtractionResult(ExtractionBaseModel):
    document: DocumentMetaExtraction
    participants: ParticipantsExtraction = Field(default_factory=ParticipantsExtraction)
    processes: list[ProcessExtraction] = Field(default_factory=list)
    responsibilities: list[ResponsibilityExtraction] = Field(default_factory=list)
    forms: list[FormExtraction] = Field(default_factory=list)
    related_departments: list[str] = Field(default_factory=list)
    related_documents: list[str] = Field(default_factory=list)
    related_systems: list[str] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _ensure_str_list(value: Any) -> list[str]:
    items: list[str] = []
    for item in _ensure_list(value):
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, dict):
            text = item.get("name") or item.get("code") or item.get("title")
            items.append(str(text) if text else json.dumps(item, ensure_ascii=False))
        elif item is not None:
            items.append(str(item))
    return items


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("text", "value", "content", "description", "purpose", "title", "name"):
            nested = value.get(key)
            if nested is not None and nested is not value:
                coerced = _coerce_text(nested)
                if coerced:
                    return coerced
        return None
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        joined = "; ".join(part for part in parts if part)
        return joined or None
    text = str(value).strip()
    return text or None


def _normalize_confidence(value: Any) -> str:
    if value is None:
        return ConfidenceLevel.LOW.value
    normalized = str(value).lower().strip().replace(" ", "_")
    aliases = {
        "very_high": ConfidenceLevel.HIGH.value,
        "h": ConfidenceLevel.HIGH.value,
        "med": ConfidenceLevel.MEDIUM.value,
        "m": ConfidenceLevel.MEDIUM.value,
        "average": ConfidenceLevel.MEDIUM.value,
        "l": ConfidenceLevel.LOW.value,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {item.value for item in ConfidenceLevel}:
        return normalized
    return ConfidenceLevel.LOW.value


def _normalize_role_type(value: Any) -> str:
    if value is None:
        return NdResponsibilityRoleType.UNKNOWN.value
    normalized = str(value).lower().strip().replace(" ", "_")
    if normalized in {item.value for item in NdResponsibilityRoleType}:
        return normalized
    mapping = {
        "manager": NdResponsibilityRoleType.UNKNOWN.value,
        "owner": NdResponsibilityRoleType.PROCESS_OWNER.value,
        "performer": NdResponsibilityRoleType.PERFORMER.value,
    }
    return mapping.get(normalized, NdResponsibilityRoleType.UNKNOWN.value)


def _normalize_unknown_reason(value: Any) -> str:
    if value is None:
        return NdUnknownReason.REQUIRES_HUMAN_CONFIRMATION.value
    normalized = str(value).lower().strip().replace(" ", "_")
    if normalized in {item.value for item in NdUnknownReason}:
        return normalized
    return NdUnknownReason.REQUIRES_HUMAN_CONFIRMATION.value


def _normalize_participant_list(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _ensure_list(value):
        if isinstance(item, str):
            items.append({"name": item, "role": None})
        elif isinstance(item, dict):
            items.append(item)
    return items


def _normalize_participants_block(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    return {
        "developed_by": _normalize_participant_list(data.get("developed_by")),
        "checked_by": _normalize_participant_list(data.get("checked_by")),
        "approved_by": _normalize_participant_list(data.get("approved_by")),
        "agreed_by": _normalize_participant_list(data.get("agreed_by")),
    }


def _normalize_action(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"action": value}
    if isinstance(value, dict):
        if "action" not in value:
            action_text = value.get("name") or value.get("description") or value.get("text")
            if action_text:
                return {"action": str(action_text), **value}
        return value
    return {"action": str(value)}


def _normalize_owner_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "name_or_role": str(value),
            "reason": "auto-normalized",
            "confidence": ConfidenceLevel.LOW.value,
        }
    candidate = dict(value)
    if not candidate.get("name_or_role"):
        candidate["name_or_role"] = (
            candidate.get("candidate")
            or candidate.get("name")
            or candidate.get("role")
            or candidate.get("reason")
            or "не указано"
        )
    candidate.pop("candidate", None)
    candidate["confidence"] = _normalize_confidence(candidate.get("confidence"))
    candidate.setdefault("reason", "не указано")
    return candidate


def _normalize_process(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"name": str(value), "actions": []}
    process = dict(value)
    process.setdefault("name", process.get("title") or "Процесс")
    for field in ("description", "goal"):
        process[field] = _coerce_text(process.get(field))
    for field in ("inputs", "outputs", "roles", "forms", "systems", "resources", "related_departments"):
        process[field] = _ensure_str_list(process.get(field))
    process["actions"] = [_normalize_action(item) for item in _ensure_list(process.get("actions"))]
    process["owner_candidates"] = [
        _normalize_owner_candidate(item) for item in _ensure_list(process.get("owner_candidates"))
    ]
    return process


def _normalize_form(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"name": value}
    if isinstance(value, dict):
        form = dict(value)
        form.setdefault("name", form.get("code") or "Форма")
        form["purpose"] = _coerce_text(form.get("purpose"))
        related_process = form.get("related_process")
        if isinstance(related_process, list):
            form["related_process"] = ", ".join(str(item) for item in related_process)
        return form
    return {"name": str(value)}


def _normalize_responsibility(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "subject": str(value),
            "responsibility": "не указано",
            "role_type": NdResponsibilityRoleType.UNKNOWN.value,
            "confidence": ConfidenceLevel.LOW.value,
        }
    item = dict(value)
    item["role_type"] = _normalize_role_type(item.get("role_type"))
    item["confidence"] = _normalize_confidence(item.get("confidence"))
    item.setdefault("subject", "не указано")
    item.setdefault("responsibility", "не указано")
    return item


def _normalize_unknown(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "field": str(value),
            "reason": NdUnknownReason.REQUIRES_HUMAN_CONFIRMATION.value,
        }
    item = dict(value)
    item["reason"] = _normalize_unknown_reason(item.get("reason"))
    item.setdefault("field", "unknown")
    return item


def normalize_document_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    document = normalized.get("document")
    if isinstance(document, dict):
        doc = dict(document)
        doc["document_type_confidence"] = _normalize_confidence(doc.get("document_type_confidence"))
        for field in ("title", "document_code", "version", "status", "purpose"):
            doc[field] = _coerce_text(doc.get(field))
        scope = doc.get("scope")
        if isinstance(scope, dict):
            scope_data = dict(scope)
            scope_data["text"] = _coerce_text(scope_data.get("text"))
            scope_data["departments"] = _ensure_str_list(scope_data.get("departments"))
            scope_data["positions"] = _ensure_str_list(scope_data.get("positions"))
            doc["scope"] = scope_data
        normalized["document"] = doc

    normalized["participants"] = _normalize_participants_block(normalized.get("participants"))
    normalized["processes"] = [
        _normalize_process(item) for item in _ensure_list(normalized.get("processes"))
    ]
    normalized["responsibilities"] = [
        _normalize_responsibility(item) for item in _ensure_list(normalized.get("responsibilities"))
    ]
    normalized["forms"] = [_normalize_form(item) for item in _ensure_list(normalized.get("forms"))]
    normalized["related_departments"] = _ensure_str_list(normalized.get("related_departments"))
    normalized["related_documents"] = _ensure_str_list(normalized.get("related_documents"))
    normalized["related_systems"] = _ensure_str_list(normalized.get("related_systems"))
    normalized["unknowns"] = [_normalize_unknown(item) for item in _ensure_list(normalized.get("unknowns"))]
    return normalized


def parse_document_extraction_result(payload: dict[str, Any] | str) -> DocumentExtractionResult:
    """Провалидировать ответ LLM перед сохранением в БД."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    normalized = normalize_document_extraction_payload(payload)
    return DocumentExtractionResult.model_validate(normalized)


def dump_document_extraction_result(result: DocumentExtractionResult) -> dict[str, Any]:
    return json.loads(result.model_dump_json())
