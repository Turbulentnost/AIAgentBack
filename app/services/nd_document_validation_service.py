from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentVersion
from app.models.enums import NdValidationSeverity, NdValidationStandard
from app.schemas.turbo_smk import NdDocumentValidationReport, NdValidationFinding

_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "sto": ["цель", "область применения", "термин", "ответствен", "ресурс", "риск"],
    "regulation": ["цель", "область применения", "ответствен"],
    "instruction": ["цель", "область применения", "порядок"],
}

_ISO_9001_KEYWORDS = ("владелец процесса", "результатив", "риск", "улучшен")
_STO_34_003_KEYWORDS = ("документирован", "архив", "верси", "ознаком")
_GAZPROM_KEYWORDS = ("процесс", "показател", "ресурс", "контрол")


class NdDocumentValidationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def validate_document(
        self,
        document_id: uuid.UUID,
        *,
        document_version_id: uuid.UUID | None = None,
        standards: list[NdValidationStandard] | None = None,
    ) -> NdDocumentValidationReport:
        document = await self.db.get(Document, document_id)
        if document is None:
            raise ValueError("Документ не найден")
        version = await self._resolve_version(document_id, document_version_id)
        text = document.title or ""
        if version and version.metadata_:
            text += " " + str(version.metadata_.get("extracted_text_preview") or "")
        if document.metadata_:
            text += " " + str(document.metadata_.get("document_name") or "")
        doc_kind = (document.metadata_ or {}).get("qms_document_kind", "instruction")
        text_lower = text.lower()
        checked = standards or [
            NdValidationStandard.STO_34_003,
            NdValidationStandard.ISO_9001,
            NdValidationStandard.STO_GAZPROM_9001,
            NdValidationStandard.TEMPLATE,
        ]
        findings: list[NdValidationFinding] = []
        for section in _REQUIRED_SECTIONS.get(str(doc_kind), _REQUIRED_SECTIONS["instruction"]):
            if section not in text_lower:
                findings.append(
                    NdValidationFinding(
                        code=f"missing_section_{section}",
                        severity=NdValidationSeverity.MAJOR,
                        standard=NdValidationStandard.TEMPLATE,
                        section=section,
                        message=f"Не найден обязательный раздел «{section}»",
                        recommendation=f"Добавьте раздел «{section}» согласно шаблону",
                        requirement_ref="STO-34-003",
                    )
                )
        if not re.search(r"верси[яи]", text_lower):
            findings.append(
                NdValidationFinding(
                    code="missing_version_marker",
                    severity=NdValidationSeverity.WARNING,
                    standard=NdValidationStandard.STO_34_003,
                    message="Не найден номер версии документа",
                    recommendation="Укажите номер версии в колонтитуле и на титульном листе",
                    requirement_ref="STO-34-003",
                )
            )
        findings.extend(self._keyword_findings(text_lower, _ISO_9001_KEYWORDS, NdValidationStandard.ISO_9001))
        findings.extend(self._keyword_findings(text_lower, _STO_34_003_KEYWORDS, NdValidationStandard.STO_34_003))
        findings.extend(self._keyword_findings(text_lower, _GAZPROM_KEYWORDS, NdValidationStandard.STO_GAZPROM_9001))
        findings.extend(self._reference_findings(text))
        critical_count = sum(1 for item in findings if item.severity == NdValidationSeverity.CRITICAL)
        major_count = sum(1 for item in findings if item.severity == NdValidationSeverity.MAJOR)
        return NdDocumentValidationReport(
            document_id=document_id,
            document_version_id=version.id if version else None,
            overall_passed=critical_count == 0 and major_count == 0,
            findings=findings,
            checked_standards=checked,
            generated_at=datetime.now(UTC),
        )

    async def _resolve_version(
        self,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
    ) -> DocumentVersion | None:
        if document_version_id is not None:
            return await self.db.get(DocumentVersion, document_version_id)
        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _keyword_findings(
        self,
        text_lower: str,
        keywords: tuple[str, ...],
        standard: NdValidationStandard,
    ) -> list[NdValidationFinding]:
        findings: list[NdValidationFinding] = []
        for keyword in keywords:
            if keyword not in text_lower:
                findings.append(
                    NdValidationFinding(
                        code=f"missing_keyword_{keyword.replace(' ', '_')}",
                        severity=NdValidationSeverity.WARNING,
                        standard=standard,
                        message=f"Не найдено требование по ключевому признаку «{keyword}»",
                        recommendation="Проверьте соответствие текста выбранному стандарту",
                        requirement_ref=standard.value,
                    )
                )
        return findings

    def _reference_findings(self, text: str) -> list[NdValidationFinding]:
        findings: list[NdValidationFinding] = []
        for match in re.finditer(r"(СТО[\s-][\w/.-]+|ГОСТ[\s-][\w/.-]+|ISO\s*9001)", text, re.IGNORECASE):
            findings.append(
                NdValidationFinding(
                    code="normative_reference_detected",
                    severity=NdValidationSeverity.INFO,
                    standard=NdValidationStandard.STO_34_003,
                    section=match.group(0),
                    message=f"Обнаружена нормативная ссылка: {match.group(0)}",
                    recommendation="Проверьте актуальность ссылочного документа в базе НД",
                )
            )
        return findings
