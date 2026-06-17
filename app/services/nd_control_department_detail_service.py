from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nd_control_agent.knowledge_base_access_service import KnowledgeBaseAccessService
from app.models.enums import (
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationType,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_control_department_service import (
    NdControlDepartmentService,
    NdControlDepartmentServiceError,
)

RELATION_TYPE_LABELS: dict[NdRelationType, str] = {
    NdRelationType.DEPARTMENT_OWNS_PROCESS: "Отдел владеет процессом",
    NdRelationType.DEPARTMENT_PARTICIPATES_IN_PROCESS: "Отдел участвует в процессе",
    NdRelationType.DOCUMENT_REGULATES_PROCESS: "Документ регулирует процесс",
    NdRelationType.PROCESS_USES_FORM: "Процесс использует форму",
    NdRelationType.PROCESS_USES_SYSTEM: "Процесс использует систему",
    NdRelationType.PROCESS_HAS_ROLE: "В процессе есть роль",
    NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION: "Роль отвечает за действие",
    NdRelationType.PROCESS_PRODUCES_OUTPUT: "Процесс создаёт результат",
    NdRelationType.PROCESS_CONSUMES_INPUT: "Процесс потребляет вход",
    NdRelationType.PROCESS_RELATED_TO_PROCESS: "Связанный процесс",
    NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT: "Документ упоминает отдел",
}


class NdControlDepartmentDetailServiceError(Exception):
    pass


class NdControlDepartmentDetailService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.department_service = NdControlDepartmentService(db)
        self.kb_access = KnowledgeBaseAccessService(db)

    async def get_department_scope(self, department_id: uuid.UUID) -> dict[str, Any]:
        dept = await self.department_service.get_department_or_raise(department_id)
        kb_ids = [link.knowledge_base_id for link in dept.knowledge_base_links]
        doc_ids: list[uuid.UUID] = []
        if kb_ids:
            result = await self.db.execute(
                select(DocumentCard.document_id).where(DocumentCard.knowledge_base_id.in_(kb_ids))
            )
            doc_ids = list(result.scalars().all())

        doc_id_strs = {str(item) for item in doc_ids}
        process_ids: list[uuid.UUID] = []
        if doc_id_strs:
            processes = list(
                (await self.db.execute(select(ProcessCard).where(ProcessCard.source_document_ids.is_not(None))))
                .scalars()
                .all()
            )
            for process in processes:
                sources = process.source_document_ids or []
                if any(str(source) in doc_id_strs for source in sources):
                    process_ids.append(process.id)

        return {
            "department": dept,
            "kb_ids": kb_ids,
            "document_ids": doc_ids,
            "process_ids": process_ids,
        }

    def _department_relation_filter(self, scope: dict[str, Any]):
        dept = scope["department"]
        doc_ids: list[uuid.UUID] = scope["document_ids"]
        process_ids: list[uuid.UUID] = scope["process_ids"]
        clauses = [
            (NdRelation.source_type == NdGraphEntityType.DEPARTMENT) & (NdRelation.source_id == dept.id),
            (NdRelation.target_type == NdGraphEntityType.DEPARTMENT) & (NdRelation.target_id == dept.id),
        ]
        if doc_ids:
            clauses.extend(
                [
                    (NdRelation.source_type == NdGraphEntityType.DOCUMENT) & (NdRelation.source_id.in_(doc_ids)),
                    (NdRelation.target_type == NdGraphEntityType.DOCUMENT) & (NdRelation.target_id.in_(doc_ids)),
                ]
            )
        if process_ids:
            clauses.extend(
                [
                    (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id.in_(process_ids)),
                    (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id.in_(process_ids)),
                ]
            )
        return or_(*clauses)

    async def count_department_relations(self, scope: dict[str, Any]) -> int:
        if not scope["kb_ids"]:
            return 0
        return int(
            await self.db.scalar(
                select(func.count()).select_from(NdRelation).where(self._department_relation_filter(scope))
            )
            or 0
        )

    async def count_department_pending_review(self, scope: dict[str, Any]) -> int:
        if not scope["kb_ids"]:
            return 0
        relation_pending = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdRelation)
                .where(self._department_relation_filter(scope), NdRelation.is_confirmed.is_(False))
            )
            or 0
        )
        process_pending = 0
        if scope["process_ids"]:
            process_pending = int(
                await self.db.scalar(
                    select(func.count())
                    .select_from(ProcessCard)
                    .where(
                        ProcessCard.id.in_(scope["process_ids"]),
                        ProcessCard.owner_confirmed.is_(False),
                    )
                )
                or 0
            )
        kb_ids = scope["kb_ids"]
        doc_needs_review = int(
            await self.db.scalar(
                select(func.count())
                .select_from(DocumentCard)
                .where(
                    DocumentCard.knowledge_base_id.in_(kb_ids),
                    DocumentCard.extraction_status == NdExtractionStatus.NEEDS_REVIEW,
                )
            )
            or 0
        )
        return relation_pending + process_pending + doc_needs_review

    async def count_department_processes(self, scope: dict[str, Any]) -> int:
        return len(scope["process_ids"])

    async def list_document_cards(
        self,
        department_id: uuid.UUID,
        *,
        query: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        scope = await self.get_department_scope(department_id)
        kb_ids = scope["kb_ids"]
        if not kb_ids:
            return [], 0

        stmt = select(DocumentCard).where(DocumentCard.knowledge_base_id.in_(kb_ids))
        count_stmt = select(func.count()).select_from(DocumentCard).where(DocumentCard.knowledge_base_id.in_(kb_ids))
        if query:
            pattern = f"%{query.strip()}%"
            filter_clause = or_(
                DocumentCard.document_code.ilike(pattern),
                DocumentCard.title.ilike(pattern),
                DocumentCard.file_name.ilike(pattern),
            )
            stmt = stmt.where(filter_clause)
            count_stmt = count_stmt.where(filter_clause)
        total = int(await self.db.scalar(count_stmt) or 0)
        result = await self.db.execute(
            stmt.order_by(DocumentCard.updated_at.desc()).offset((page - 1) * size).limit(size)
        )
        cards = list(result.scalars().all())
        items = [await self._document_card_item(card, scope) for card in cards]
        return items, total

    async def _document_card_item(self, card: DocumentCard, scope: dict[str, Any]) -> dict[str, Any]:
        processes_count = 0
        doc_id_str = str(card.document_id)
        if scope["process_ids"]:
            processes = list(
                (
                    await self.db.execute(
                        select(ProcessCard).where(ProcessCard.id.in_(scope["process_ids"]))
                    )
                )
                .scalars()
                .all()
            )
            for process in processes:
                if any(str(source) == doc_id_str for source in (process.source_document_ids or [])):
                    processes_count += 1

        relations_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdRelation)
                .where(
                    or_(
                        (NdRelation.source_type == NdGraphEntityType.DOCUMENT)
                        & (NdRelation.source_id == card.document_id),
                        (NdRelation.target_type == NdGraphEntityType.DOCUMENT)
                        & (NdRelation.target_id == card.document_id),
                    )
                )
            )
            or 0
        )
        needs_review_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdRelation)
                .where(
                    or_(
                        (NdRelation.source_type == NdGraphEntityType.DOCUMENT)
                        & (NdRelation.source_id == card.document_id),
                        (NdRelation.target_type == NdGraphEntityType.DOCUMENT)
                        & (NdRelation.target_id == card.document_id),
                    ),
                    NdRelation.is_confirmed.is_(False),
                )
            )
            or 0
        )
        if card.extraction_status == NdExtractionStatus.NEEDS_REVIEW:
            needs_review_count += 1

        confidence_label = None
        if card.extraction_confidence is not None:
            value = float(card.extraction_confidence)
            if value >= 0.8:
                confidence_label = "high"
            elif value >= 0.55:
                confidence_label = "medium"
            else:
                confidence_label = "low"

        return {
            "document_card_id": card.id,
            "document_id": card.document_id,
            "knowledge_base_id": card.knowledge_base_id,
            "file_name": card.file_name,
            "document_code": card.document_code,
            "title": card.title,
            "document_type": card.document_type.value if card.document_type else None,
            "version": card.version,
            "status": card.status.value if card.status else None,
            "extraction_status": card.extraction_status.value,
            "extraction_confidence": confidence_label,
            "processes_count": processes_count,
            "relations_count": relations_count,
            "needs_review_count": needs_review_count,
            "updated_at": card.updated_at,
            "purpose": card.purpose,
            "raw_extracted_json": card.raw_extracted_json,
        }

    async def list_processes(
        self,
        department_id: uuid.UUID,
        *,
        query: str | None = None,
        filter_key: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        scope = await self.get_department_scope(department_id)
        process_ids = scope["process_ids"]
        if not process_ids:
            return [], 0

        stmt = select(ProcessCard).where(ProcessCard.id.in_(process_ids))
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    ProcessCard.canonical_name.ilike(pattern),
                    ProcessCard.goal.ilike(pattern),
                    ProcessCard.owner_candidate.ilike(pattern),
                )
            )
        if filter_key == "needs_review":
            stmt = stmt.where(ProcessCard.owner_confirmed.is_(False))

        count_stmt = select(func.count()).select_from(ProcessCard).where(ProcessCard.id.in_(process_ids))
        if query:
            pattern = f"%{query.strip()}%"
            name_filter = or_(
                ProcessCard.canonical_name.ilike(pattern),
                ProcessCard.goal.ilike(pattern),
                ProcessCard.owner_candidate.ilike(pattern),
            )
            stmt = stmt.where(name_filter)
            count_stmt = count_stmt.where(name_filter)
        if filter_key == "owner_confirmed":
            stmt = stmt.where(ProcessCard.owner_confirmed.is_(True))
            count_stmt = count_stmt.where(ProcessCard.owner_confirmed.is_(True))
        elif filter_key in {"owner_unconfirmed", "needs_review"}:
            stmt = stmt.where(ProcessCard.owner_confirmed.is_(False))
            count_stmt = count_stmt.where(ProcessCard.owner_confirmed.is_(False))

        total = int(await self.db.scalar(count_stmt) or 0)
        result = await self.db.execute(
            stmt.order_by(ProcessCard.canonical_name).offset((page - 1) * size).limit(size)
        )
        processes = list(result.scalars().all())
        items = [await self._process_item(process, scope) for process in processes]
        return items, total

    async def _process_item(self, process: ProcessCard, scope: dict[str, Any]) -> dict[str, Any]:
        source_count = len(process.source_document_ids or [])
        relations_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdRelation)
                .where(
                    or_(
                        (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id == process.id),
                        (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id == process.id),
                    )
                )
            )
            or 0
        )
        pending_relations = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdRelation)
                .where(
                    or_(
                        (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id == process.id),
                        (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id == process.id),
                    ),
                    NdRelation.is_confirmed.is_(False),
                )
            )
            or 0
        )
        return {
            "process_id": process.id,
            "canonical_name": process.canonical_name,
            "description": process.description,
            "goal": process.goal,
            "owner_candidate": process.owner_candidate,
            "owner_confirmed": process.owner_confirmed,
            "owner_confidence": process.owner_confidence.value if process.owner_confidence else None,
            "source_documents_count": source_count,
            "relations_count": relations_count,
            "forms_count": len(process.forms_json or []),
            "systems_count": len(process.systems_json or []),
            "needs_review": not process.owner_confirmed or pending_relations > 0,
            "pending_relations_count": pending_relations,
        }

    async def list_relations(
        self,
        department_id: uuid.UUID,
        *,
        query: str | None = None,
        filter_key: str | None = None,
        relation_type: str | None = None,
        confidence: str | None = None,
        extraction_type: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        scope = await self.get_department_scope(department_id)
        if not scope["kb_ids"]:
            return [], 0

        stmt = select(NdRelation).where(self._department_relation_filter(scope))
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(NdRelation.source_name.ilike(pattern), NdRelation.target_name.ilike(pattern))
            )
        if filter_key == "unconfirmed":
            stmt = stmt.where(NdRelation.is_confirmed.is_(False))
        elif filter_key == "confirmed":
            stmt = stmt.where(NdRelation.is_confirmed.is_(True))
        if relation_type:
            stmt = stmt.where(NdRelation.relation_type == relation_type)
        if confidence:
            stmt = stmt.where(NdRelation.confidence == confidence)
        if extraction_type:
            stmt = stmt.where(NdRelation.extraction_type == extraction_type)

        count_stmt = select(func.count()).select_from(NdRelation).where(self._department_relation_filter(scope))
        if query:
            pattern = f"%{query.strip()}%"
            rel_filter = or_(NdRelation.source_name.ilike(pattern), NdRelation.target_name.ilike(pattern))
            count_stmt = count_stmt.where(rel_filter)
        if filter_key == "unconfirmed":
            count_stmt = count_stmt.where(NdRelation.is_confirmed.is_(False))
        elif filter_key == "confirmed":
            count_stmt = count_stmt.where(NdRelation.is_confirmed.is_(True))
        if relation_type:
            count_stmt = count_stmt.where(NdRelation.relation_type == relation_type)
        if confidence:
            count_stmt = count_stmt.where(NdRelation.confidence == confidence)
        if extraction_type:
            count_stmt = count_stmt.where(NdRelation.extraction_type == extraction_type)

        total = int(await self.db.scalar(count_stmt) or 0)
        result = await self.db.execute(
            stmt.order_by(NdRelation.created_at.desc()).offset((page - 1) * size).limit(size)
        )
        relations = list(result.scalars().all())
        return [self._relation_item(relation) for relation in relations], total

    def _relation_item(self, relation: NdRelation) -> dict[str, Any]:
        evidence = (relation.evidence_json or [{}])[0] if relation.evidence_json else {}
        review_status = "confirmed" if relation.is_confirmed else "pending"
        return {
            "relation_id": relation.id,
            "source_type": relation.source_type.value,
            "source_name": relation.source_name,
            "relation_type": relation.relation_type.value,
            "relation_type_label": RELATION_TYPE_LABELS.get(
                relation.relation_type, relation.relation_type.value
            ),
            "target_type": relation.target_type.value,
            "target_name": relation.target_name,
            "confidence": relation.confidence.value,
            "extraction_type": relation.extraction_type.value,
            "is_confirmed": relation.is_confirmed,
            "review_status": review_status,
            "evidence": evidence,
            "created_at": relation.created_at,
        }

    async def list_review_pending(
        self,
        department_id: uuid.UUID,
        *,
        query: str | None = None,
        filter_key: str | None = None,
    ) -> dict[str, Any]:
        scope = await self.get_department_scope(department_id)
        kb_ids = scope["kb_ids"]
        process_ids = scope["process_ids"]

        process_owners: list[dict[str, Any]] = []
        if process_ids:
            stmt = select(ProcessCard).where(
                ProcessCard.id.in_(process_ids),
                ProcessCard.owner_confirmed.is_(False),
            )
            if query:
                pattern = f"%{query.strip()}%"
                stmt = stmt.where(
                    or_(
                        ProcessCard.canonical_name.ilike(pattern),
                        ProcessCard.owner_candidate.ilike(pattern),
                    )
                )
            for process in (await self.db.execute(stmt)).scalars().all():
                process_owners.append(
                    {
                        "process_id": process.id,
                        "process_name": process.canonical_name,
                        "owner_candidate": process.owner_candidate,
                        "confidence": process.owner_confidence.value if process.owner_confidence else None,
                        "evidence": None,
                    }
                )

        relations: list[dict[str, Any]] = []
        if kb_ids:
            rel_stmt = select(NdRelation).where(
                self._department_relation_filter(scope),
                NdRelation.is_confirmed.is_(False),
            )
            if filter_key == "high_confidence":
                rel_stmt = rel_stmt.where(NdRelation.confidence == "high")
            elif filter_key == "department_process":
                rel_stmt = rel_stmt.where(
                    NdRelation.relation_type == NdRelationType.DEPARTMENT_OWNS_PROCESS
                )
            elif filter_key == "document_process":
                rel_stmt = rel_stmt.where(
                    NdRelation.relation_type == NdRelationType.DOCUMENT_REGULATES_PROCESS
                )
            if query:
                pattern = f"%{query.strip()}%"
                rel_stmt = rel_stmt.where(
                    or_(NdRelation.source_name.ilike(pattern), NdRelation.target_name.ilike(pattern))
                )
            for relation in (await self.db.execute(rel_stmt.limit(200))).scalars().all():
                relations.append(self._relation_item(relation))

        documents: list[dict[str, Any]] = []
        if kb_ids:
            doc_stmt = select(DocumentCard).where(
                DocumentCard.knowledge_base_id.in_(kb_ids),
                DocumentCard.extraction_status == NdExtractionStatus.NEEDS_REVIEW,
            )
            if query:
                pattern = f"%{query.strip()}%"
                doc_stmt = doc_stmt.where(
                    or_(
                        DocumentCard.document_code.ilike(pattern),
                        DocumentCard.title.ilike(pattern),
                        DocumentCard.file_name.ilike(pattern),
                    )
                )
            for card in (await self.db.execute(doc_stmt)).scalars().all():
                raw = card.raw_extracted_json or {}
                documents.append(
                    {
                        "document_card_id": card.id,
                        "document_id": card.document_id,
                        "document_code": card.document_code,
                        "title": card.title or card.file_name,
                        "reason": raw.get("message") or raw.get("error") or "Требует проверки",
                        "extraction_status": card.extraction_status.value,
                    }
                )

        return {
            "process_owners": process_owners,
            "relations": relations,
            "documents": documents,
            "conflicts": [],
        }

    async def list_analysis_runs(
        self,
        department_id: uuid.UUID,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        await self.department_service.get_department_or_raise(department_id)
        base = select(DepartmentAnalysisRun).where(DepartmentAnalysisRun.department_id == department_id)
        total = int(await self.db.scalar(select(func.count()).select_from(DepartmentAnalysisRun).where(
            DepartmentAnalysisRun.department_id == department_id
        )) or 0)
        result = await self.db.execute(
            base.order_by(DepartmentAnalysisRun.created_at.desc()).offset((page - 1) * size).limit(size)
        )
        items = []
        for run in result.scalars().all():
            duration_seconds = None
            if run.started_at and run.finished_at:
                duration_seconds = int((run.finished_at - run.started_at).total_seconds())
            summary = run.summary_json or {}
            items.append(
                {
                    "run_id": run.id,
                    "started_at": run.started_at or run.created_at,
                    "finished_at": run.finished_at,
                    "status": run.status.value,
                    "total_documents": run.total_documents,
                    "processed_documents": run.processed_documents,
                    "skipped_documents": run.skipped_documents,
                    "failed_documents": run.failed_documents,
                    "needs_review_documents": run.needs_review_documents,
                    "processes_created": int(summary.get("processes_created", 0)),
                    "relations_created": int(summary.get("relations_created", 0)),
                    "duration_seconds": duration_seconds,
                    "error_message": run.error_message,
                }
            )
        return items, total

    async def get_knowledge_base_summaries(self, department_id: uuid.UUID) -> list[dict[str, Any]]:
        scope = await self.get_department_scope(department_id)
        items: list[dict[str, Any]] = []
        for kb_id in scope["kb_ids"]:
            kb = await self.db.get(KnowledgeBase, kb_id)
            docs_meta = await self.kb_access.list_documents(str(kb_id))
            total_docs = len(docs_meta)
            cards = list(
                (
                    await self.db.execute(
                        select(DocumentCard).where(DocumentCard.knowledge_base_id == kb_id)
                    )
                )
                .scalars()
                .all()
            )
            processed = sum(
                1
                for card in cards
                if card.extraction_status
                in {NdExtractionStatus.COMPLETED, NdExtractionStatus.NEEDS_REVIEW}
            )
            failed = sum(1 for card in cards if card.extraction_status == NdExtractionStatus.FAILED)
            if total_docs == 0:
                status = "empty"
            elif failed > 0:
                status = "error"
            elif processed >= total_docs and total_docs > 0:
                status = "ready"
            elif processed > 0:
                status = "partial"
            else:
                status = "pending"
            items.append(
                {
                    "id": str(kb_id),
                    "name": kb.name if kb else str(kb_id),
                    "description": kb.description if kb else None,
                    "documents_count": total_docs,
                    "processed_count": processed,
                    "failed_count": failed,
                    "status": status,
                }
            )
        return items

    async def approve_relation(self, relation_id: uuid.UUID) -> NdRelation:
        relation = await self.db.get(NdRelation, relation_id)
        if relation is None:
            raise NdControlDepartmentDetailServiceError("Связь не найдена")
        relation.is_confirmed = True
        await self.db.flush()
        return relation

    async def reject_relation(self, relation_id: uuid.UUID) -> None:
        relation = await self.db.get(NdRelation, relation_id)
        if relation is None:
            raise NdControlDepartmentDetailServiceError("Связь не найдена")
        await self.db.delete(relation)
        await self.db.flush()

    async def confirm_process_owner(self, process_id: uuid.UUID, owner_name: str | None = None) -> ProcessCard:
        process = await self.db.get(ProcessCard, process_id)
        if process is None:
            raise NdControlDepartmentDetailServiceError("Процесс не найден")
        if owner_name:
            process.owner_candidate = owner_name
        process.owner_confirmed = True
        await self.db.flush()
        return process
