from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_card import QmsDocumentCard
from app.models.enums import DocumentCardStatus, NdReportKind
from app.models.nd_change import NdChangeRequest
from app.schemas.turbo_smk import NdReportResult

_REPORT_TITLES: dict[NdReportKind, str] = {
    NdReportKind.ACTIVE_REGISTRY: "Реестр действующих НД",
    NdReportKind.ARCHIVE_REGISTRY: "Реестр архивных НД",
    NdReportKind.NEEDS_UPDATE_REGISTRY: "Реестр НД, требующих актуализации",
    NdReportKind.CHANGE_NOTICE_REGISTRY: "Реестр извещений об изменении",
    NdReportKind.NEW_VERSION_REGISTRY: "Реестр новых версий НД",
    NdReportKind.OVERDUE_APPROVALS: "Отчёт по просроченным согласованиям",
    NdReportKind.OVERDUE_ACKNOWLEDGEMENT: "Отчёт по просроченному ознакомлению",
    NdReportKind.DEPARTMENT_ACK_GAPS: "Отчёт по подразделениям без ознакомления",
    NdReportKind.DOCS_WITHOUT_OWNER: "Отчёт по документам без владельца процесса",
    NdReportKind.DOCS_WITHOUT_DIAGRAM: "Отчёт по документам без блок-схемы",
    NdReportKind.DOCS_WITH_STALE_REFS: "Отчёт по документам с устаревшими ссылками",
    NdReportKind.DUPLICATE_REQUIREMENTS: "Отчёт по дублирующим требованиям",
    NdReportKind.DMI_EFFECTIVENESS: "Отчёт по результативности процесса УДИ",
    NdReportKind.DOCUMENT_QUALITY: "Отчёт по качеству оформления документов",
    NdReportKind.EXECUTION_DISCIPLINE: "Отчёт по исполнительской дисциплине",
    NdReportKind.SPU_REMARKS: "Отчёт по количеству замечаний СПУ",
    NdReportKind.RETURNED_FOR_REVISION: "Отчёт по возвратам на доработку",
    NdReportKind.CROSS_PROCESS_CHANGES: "Отчёт по изменениям смежных процессов",
    NdReportKind.IMPLEMENTATION_RISKS: "Отчёт по рискам внедрения изменений",
    NdReportKind.MANAGEMENT_REVIEW: "Отчёт для анализа со стороны руководства",
}


class NdReportsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(self, kind: NdReportKind) -> NdReportResult:
        rows: list[dict]
        summary: dict
        if kind == NdReportKind.ACTIVE_REGISTRY:
            rows, summary = await self._document_registry([DocumentCardStatus.ACTIVE])
        elif kind == NdReportKind.ARCHIVE_REGISTRY:
            rows, summary = await self._document_registry([DocumentCardStatus.ARCHIVED, DocumentCardStatus.SUPERSEDED])
        elif kind == NdReportKind.NEEDS_UPDATE_REGISTRY:
            rows, summary = await self._document_registry([DocumentCardStatus.NEEDS_UPDATE])
        elif kind == NdReportKind.DOCS_WITHOUT_OWNER:
            rows, summary = await self._docs_without_owner()
        elif kind == NdReportKind.DOCS_WITHOUT_DIAGRAM:
            rows, summary = await self._docs_without_diagram()
        elif kind == NdReportKind.CHANGE_NOTICE_REGISTRY:
            rows, summary = await self._change_notice_registry()
        else:
            rows, summary = await self._placeholder_report(kind)
        return NdReportResult(
            kind=kind.value,
            title=_REPORT_TITLES[kind],
            generated_at=datetime.now(UTC),
            rows=rows,
            summary=summary,
        )

    async def list_available(self) -> list[dict]:
        return [{"kind": kind.value, "title": title} for kind, title in _REPORT_TITLES.items()]

    async def _document_registry(self, statuses: list[DocumentCardStatus]) -> tuple[list[dict], dict]:
        result = await self.db.execute(
            select(QmsDocumentCard).where(QmsDocumentCard.status.in_(statuses)).limit(500)
        )
        rows = [
            {
                "document_code": card.document_code,
                "document_name": card.document_name,
                "status": card.status.value,
                "process_owner": card.process_owner,
            }
            for card in result.scalars().all()
        ]
        return rows, {"count": len(rows)}

    async def _docs_without_owner(self) -> tuple[list[dict], dict]:
        result = await self.db.execute(
            select(QmsDocumentCard).where(QmsDocumentCard.process_owner.is_(None)).limit(500)
        )
        rows = [
            {"document_code": card.document_code, "document_name": card.document_name}
            for card in result.scalars().all()
        ]
        return rows, {"count": len(rows)}

    async def _docs_without_diagram(self) -> tuple[list[dict], dict]:
        result = await self.db.execute(
            select(QmsDocumentCard).where(QmsDocumentCard.has_process_diagram.is_(False)).limit(500)
        )
        rows = [
            {"document_code": card.document_code, "document_name": card.document_name}
            for card in result.scalars().all()
        ]
        return rows, {"count": len(rows)}

    async def _change_notice_registry(self) -> tuple[list[dict], dict]:
        result = await self.db.execute(select(NdChangeRequest).order_by(NdChangeRequest.created_at.desc()).limit(500))
        rows = [
            {
                "number": item.number,
                "status": item.status.value,
                "change_text": item.change_text[:200],
            }
            for item in result.scalars().all()
        ]
        return rows, {"count": len(rows)}

    async def _placeholder_report(self, kind: NdReportKind) -> tuple[list[dict], dict]:
        count = await self.db.scalar(select(func.count()).select_from(QmsDocumentCard)) or 0
        return [], {"kind": kind.value, "available_documents": int(count), "status": "stub"}
