from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_card import QmsDocumentCard
from app.models.nd_control_structural import NdRelation, ProcessCard
from app.schemas.turbo_smk import NdImpactAnalysisReport, NdImpactItem


class NdImpactAnalysisService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(
        self,
        *,
        document_id: uuid.UUID | None = None,
        change_text: str | None = None,
    ) -> NdImpactAnalysisReport:
        terms = [term.strip() for term in (change_text or "").split() if len(term.strip()) > 3][:12]
        affected_documents: list[NdImpactItem] = []
        if document_id is not None:
            card = await self.db.scalar(
                select(QmsDocumentCard).where(QmsDocumentCard.document_id == document_id)
            )
            if card and card.related_documents:
                for rel in card.related_documents:
                    if isinstance(rel, dict):
                        affected_documents.append(
                            NdImpactItem(
                                entity_type="document",
                                entity_id=str(rel.get("code") or rel.get("id") or ""),
                                title=str(rel.get("name") or rel.get("code") or "Связанный документ"),
                                impact_level="medium",
                                reason="Прямая связь в карточке НД",
                            )
                        )
        if terms:
            pattern = f"%{terms[0]}%"
            result = await self.db.execute(
                select(QmsDocumentCard)
                .where(
                    or_(
                        QmsDocumentCard.document_name.ilike(pattern),
                        QmsDocumentCard.document_code.ilike(pattern),
                    )
                )
                .limit(10)
            )
            for card in result.scalars().all():
                affected_documents.append(
                    NdImpactItem(
                        entity_type="document",
                        entity_id=card.document_code,
                        title=card.document_name,
                        impact_level="low",
                        reason="Семантическое совпадение по тексту изменения",
                    )
                )
        process_result = await self.db.execute(select(ProcessCard).limit(20))
        affected_processes = [
            NdImpactItem(
                entity_type="process",
                entity_id=str(process.id),
                title=process.canonical_name,
                impact_level="medium"
                if terms and process.canonical_name and terms[0].lower() in process.canonical_name.lower()
                else "low",
                reason="Анализ пересечения процессов",
            )
            for process in process_result.scalars().all()
        ]
        relation_result = await self.db.execute(select(NdRelation).limit(20))
        adjacent_departments = sorted(
            {
                str(item.source_label or item.target_label)
                for item in relation_result.scalars().all()
                if item.source_label or item.target_label
            }
        )
        recommendation = "change_notice"
        if len(affected_documents) > 5:
            recommendation = "new_version"
        process_owners: list[str] = []
        if document_id is not None:
            owner_card = await self.db.scalar(
                select(QmsDocumentCard).where(QmsDocumentCard.document_id == document_id)
            )
            if owner_card and owner_card.process_owner:
                process_owners = [owner_card.process_owner]
        return NdImpactAnalysisReport(
            affected_processes=affected_processes[:10],
            affected_documents=affected_documents[:10],
            process_owners=process_owners,
            adjacent_departments=adjacent_departments[:10],
            record_forms=[],
            diagrams=[],
            acknowledgement_targets=[],
            risks=["Требуется подтверждение владельца процесса"] if affected_documents else [],
            recommendation=recommendation,
            suggested_route=[
                "author",
                "department_head",
                "process_management_specialist",
                "quality_deputy",
                "director",
            ],
        )
