from __future__ import annotations

import re

from app.models.enums import QmsDocumentKind, QmsLevel

DOCUMENT_CODE_RE = re.compile(
    r"(?:СТО|И|РГ|ПЛ|ДИ|РИ|ПП|ВС|П)(?:-[A-ZА-ЯЁ0-9]+)+",
    re.IGNORECASE,
)

PREFIX_TO_DOCUMENT_KIND: dict[str, QmsDocumentKind] = {
    "ПП": QmsDocumentKind.POLICY,
    "ПЛ": QmsDocumentKind.PROVISION,
    "РГ": QmsDocumentKind.REGULATION,
    "СТО": QmsDocumentKind.STO,
    "ВС": QmsDocumentKind.STO,
    "И": QmsDocumentKind.INSTRUCTION,
    "П": QmsDocumentKind.INSTRUCTION,
    "ДИ": QmsDocumentKind.INSTRUCTION,
    "РИ": QmsDocumentKind.INSTRUCTION,
}

QMS_LEVEL_BY_DOCUMENT_KIND: dict[QmsDocumentKind, QmsLevel] = {
    QmsDocumentKind.POLICY: QmsLevel.STRATEGIC,
    QmsDocumentKind.PROVISION: QmsLevel.ORGANIZATIONAL,
    QmsDocumentKind.REGULATION: QmsLevel.PROCESS,
    QmsDocumentKind.STO: QmsLevel.TECHNICAL,
    QmsDocumentKind.INSTRUCTION: QmsLevel.OPERATIONAL,
}

DOCUMENT_KIND_LABELS: dict[QmsDocumentKind, str] = {
    QmsDocumentKind.POLICY: "Политика",
    QmsDocumentKind.PROVISION: "Положение",
    QmsDocumentKind.REGULATION: "Регламент",
    QmsDocumentKind.STO: "СТО",
    QmsDocumentKind.INSTRUCTION: "Инструкция",
}

QMS_LEVEL_LABELS: dict[QmsLevel, str] = {
    QmsLevel.STRATEGIC: "Стратегический",
    QmsLevel.ORGANIZATIONAL: "Организационный",
    QmsLevel.PROCESS: "Процессный",
    QmsLevel.TECHNICAL: "Технический",
    QmsLevel.OPERATIONAL: "Операционный",
}


def extract_document_code(*, title: str | None, original_filename: str | None, metadata: dict | None) -> str | None:
    if metadata:
        code = metadata.get("code") or metadata.get("document_code")
        if code:
            return str(code).strip().upper()
    for source in (original_filename, title):
        if not source:
            continue
        matches = DOCUMENT_CODE_RE.findall(source)
        if matches:
            return matches[-1].upper()
    return None


def infer_document_kind(document_code: str | None) -> QmsDocumentKind:
    if not document_code:
        return QmsDocumentKind.STO
    prefix = document_code.split("-", 1)[0].upper()
    return PREFIX_TO_DOCUMENT_KIND.get(prefix, QmsDocumentKind.STO)


def infer_qms_level(document_kind: QmsDocumentKind) -> QmsLevel:
    return QMS_LEVEL_BY_DOCUMENT_KIND[document_kind]


def fallback_document_code(document_id: str) -> str:
    return f"ND-{document_id[:8].upper()}"
