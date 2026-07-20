from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nd_control_agent.knowledge_base_access_service import KnowledgeBaseAccessService
from app.models.enums import (
    ConfidenceLevel,
    NdExtractionStatus,
    NdGraphEntityType,
    NdStructuralDocumentType,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_control_department_service import (
    NdControlDepartmentService,
    NdControlDepartmentServiceError,
)
from app.services.nd_relation_display_mapper import (
    CONFIDENCE_LABELS,
    RelationResolutionCache,
    evidence_has_content,
    format_document_display_name,
    map_relation_to_display,
)
from app.utils.smk_document_classification import (
    get_document_level_label,
    get_document_type_label,
)


class NdControlDepartmentDetailServiceError(Exception):
    pass


def _parse_uuid_list(values: list | None) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    for item in values or []:
        try:
            parsed.append(uuid.UUID(str(item)))
        except (ValueError, TypeError, AttributeError):
            continue
    return parsed


def _normalize_string_list(items: list | None) -> list[str]:
    if not items:
        return []
    normalized: list[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append(text)
            continue
        if isinstance(item, dict):
            label = item.get("name") or item.get("title") or item.get("label") or item.get("action")
            if label:
                normalized.append(str(label).strip())
            continue
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


from app.services.nd_process_display_mapper import (
    EXTRACTION_STATUS_LABELS,
    confidence_sort_key,
    normalize_action_details,
    owner_status_label,
    process_matches_query,
    systems_preview,
)


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
        document_type: str | None = None,
        document_level: str | None = None,
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
        if document_type:
            try:
                type_value = NdStructuralDocumentType(document_type)
                stmt = stmt.where(DocumentCard.document_type == type_value)
                count_stmt = count_stmt.where(DocumentCard.document_type == type_value)
            except ValueError:
                pass
        if document_level:
            stmt = stmt.where(DocumentCard.document_level == document_level)
            count_stmt = count_stmt.where(DocumentCard.document_level == document_level)
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
            "document_type_label": get_document_type_label(card.document_type),
            "document_type_confidence": (
                card.document_type_confidence.value if card.document_type_confidence else None
            ),
            "document_level": card.document_level,
            "document_level_label": get_document_level_label(card.document_level),
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
        sort_key: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        scope = await self.get_department_scope(department_id)
        process_ids = scope["process_ids"]
        if not process_ids:
            return [], 0

        stmt = select(ProcessCard).where(ProcessCard.id.in_(process_ids))

        if filter_key == "owner_confirmed":
            stmt = stmt.where(ProcessCard.owner_confirmed.is_(True))
        elif filter_key in {"owner_unconfirmed", "needs_review"}:
            stmt = stmt.where(ProcessCard.owner_confirmed.is_(False))
        elif filter_key == "high_confidence":
            stmt = stmt.where(ProcessCard.owner_confidence == ConfidenceLevel.HIGH)
        elif filter_key == "medium_confidence":
            stmt = stmt.where(ProcessCard.owner_confidence == ConfidenceLevel.MEDIUM)
        elif filter_key == "low_confidence":
            stmt = stmt.where(ProcessCard.owner_confidence == ConfidenceLevel.LOW)

        result = await self.db.execute(stmt.order_by(ProcessCard.canonical_name))
        processes = list(result.scalars().all())
        items = [await self._process_item(process, scope) for process in processes]

        if query:
            items = [item for item in items if process_matches_query(item, query)]

        if filter_key == "needs_review":
            items = [item for item in items if item["needs_review"]]
        elif filter_key == "has_relations":
            items = [item for item in items if item["relations_count"] > 0]
        elif filter_key == "no_relations":
            items = [item for item in items if item["relations_count"] == 0]

        sort = sort_key or "name"
        if sort == "confidence":
            items.sort(key=lambda item: (confidence_sort_key(item["owner"]["confidence"]), item["name"]))
        elif sort == "relations_count":
            items.sort(key=lambda item: (-item["relations_count"], item["name"]))
        elif sort == "documents_count":
            items.sort(key=lambda item: (-item["source_documents_count"], item["name"]))
        elif sort == "needs_review":
            items.sort(key=lambda item: (not item["needs_review"], item["name"]))
        else:
            items.sort(key=lambda item: item["name"].lower())

        total = len(items)
        start = (page - 1) * size
        return items[start : start + size], total

    async def _process_relations_summary(self, process_id: uuid.UUID) -> dict[str, int]:
        result = await self.db.execute(
            select(NdRelation).where(
                or_(
                    (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id == process_id),
                    (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id == process_id),
                )
            )
        )
        relations = list(result.scalars().all())
        confirmed = sum(1 for relation in relations if relation.is_confirmed)
        without_evidence = sum(1 for relation in relations if not evidence_has_content(relation.evidence_json))
        total = len(relations)
        return {
            "total": total,
            "confirmed": confirmed,
            "unconfirmed": total - confirmed,
            "without_evidence": without_evidence,
        }

    async def _process_item(self, process: ProcessCard, scope: dict[str, Any]) -> dict[str, Any]:
        source_doc_ids = _parse_uuid_list(process.source_document_ids)
        source_documents: list[dict[str, Any]] = []
        if source_doc_ids:
            result = await self.db.execute(
                select(DocumentCard).where(DocumentCard.document_id.in_(source_doc_ids))
            )
            for card in result.scalars().all():
                source_documents.append(
                    {
                        "document_id": card.document_id,
                        "document_code": card.document_code,
                        "title": card.title or card.file_name,
                        "display_name": format_document_display_name(card),
                        "document_type": card.document_type.value if card.document_type else None,
                        "extraction_status": card.extraction_status.value,
                        "extraction_status_label": EXTRACTION_STATUS_LABELS.get(
                            card.extraction_status.value, card.extraction_status.value
                        ),
                    }
                )
        source_count = len(process.source_document_ids or [])
        relations_summary = await self._process_relations_summary(process.id)
        relations_count = relations_summary["total"]
        pending_relations = relations_summary["unconfirmed"]

        inputs = _normalize_string_list(process.inputs_json)
        outputs = _normalize_string_list(process.outputs_json)
        forms = _normalize_string_list(process.forms_json)
        systems = _normalize_string_list(process.systems_json)
        resources = _normalize_string_list(process.resources_json)
        action_details = normalize_action_details(process.actions_json)
        action_names = [item["name"] for item in action_details]

        owner_confidence = process.owner_confidence.value if process.owner_confidence else None
        owner_confidence_label = (
            CONFIDENCE_LABELS.get(process.owner_confidence) if process.owner_confidence else None
        )
        needs_review = not process.owner_confirmed or pending_relations > 0
        owner_status_label_value = owner_status_label(
            confirmed=process.owner_confirmed,
            candidate=process.owner_candidate,
            pending_relations=pending_relations,
        )
        owner = {
            "candidate": process.owner_candidate,
            "confirmed": process.owner_confirmed,
            "confidence": owner_confidence,
            "confidence_label": owner_confidence_label,
            "status_label": owner_status_label_value,
            "reason": None,
        }

        return {
            "process_id": process.id,
            "name": process.canonical_name,
            "canonical_name": process.canonical_name,
            "description": process.description,
            "goal": process.goal,
            "owner": owner,
            "owner_candidate": process.owner_candidate,
            "owner_confirmed": process.owner_confirmed,
            "owner_confidence": owner_confidence,
            "owner_confidence_label": owner_confidence_label,
            "owner_status_label": owner_status_label_value,
            "source_documents": source_documents,
            "source_document_names": [doc["display_name"] for doc in source_documents],
            "source_documents_count": source_count,
            "inputs": inputs,
            "outputs": outputs,
            "actions": action_details,
            "action_names": action_names,
            "forms": forms,
            "systems": systems,
            "resources": resources,
            "systems_preview": systems_preview(systems, forms),
            "relations_count": relations_count,
            "relations_summary": relations_summary,
            "forms_count": len(forms),
            "systems_count": len(systems),
            "needs_review": needs_review,
            "pending_relations_count": pending_relations,
        }

    async def _load_resolution_cache(
        self,
        relations: list[NdRelation],
        scope: dict[str, Any],
    ) -> RelationResolutionCache:
        cache = RelationResolutionCache()
        cache.departments_by_id[scope["department"].id] = scope["department"].name

        doc_ids: set[uuid.UUID] = set(scope.get("document_ids") or [])
        process_ids: set[uuid.UUID] = set(scope.get("process_ids") or [])
        for relation in relations:
            if relation.source_type == NdGraphEntityType.DOCUMENT and relation.source_id:
                doc_ids.add(relation.source_id)
            if relation.target_type == NdGraphEntityType.DOCUMENT and relation.target_id:
                doc_ids.add(relation.target_id)
            if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id:
                process_ids.add(relation.source_id)
            if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id:
                process_ids.add(relation.target_id)

        if doc_ids:
            result = await self.db.execute(
                select(DocumentCard).where(DocumentCard.document_id.in_(doc_ids))
            )
            for card in result.scalars().all():
                cache.documents_by_id[card.document_id] = card
        if process_ids:
            result = await self.db.execute(
                select(ProcessCard).where(ProcessCard.id.in_(process_ids))
            )
            for process in result.scalars().all():
                cache.processes_by_id[process.id] = process
        return cache

    def _apply_relation_display_filters(
        self,
        items: list[dict[str, Any]],
        filter_key: str | None,
    ) -> list[dict[str, Any]]:
        key = filter_key or "primary"
        if key == "all":
            return items
        if key == "primary":
            return [
                item
                for item in items
                if item["is_primary_relation"] and not item["is_weak_relation"]
            ]
        if key == "service":
            return [item for item in items if item["is_service_relation"] or item["is_weak_relation"]]
        if key == "no_evidence":
            return [item for item in items if not item["has_evidence"]]
        if key == "unconfirmed":
            return [item for item in items if not item["is_confirmed"]]
        if key == "confirmed":
            return [item for item in items if item["is_confirmed"]]
        return items

    async def list_relations(
        self,
        department_id: uuid.UUID,
        *,
        query: str | None = None,
        filter_key: str | None = None,
        relation_type: str | None = None,
        confidence: str | None = None,
        extraction_type: str | None = None,
        process_id: uuid.UUID | None = None,
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
        if relation_type:
            stmt = stmt.where(NdRelation.relation_type == relation_type)
        if confidence:
            stmt = stmt.where(NdRelation.confidence == confidence)
        if extraction_type:
            stmt = stmt.where(NdRelation.extraction_type == extraction_type)
        if process_id:
            stmt = stmt.where(
                or_(
                    (NdRelation.source_type == NdGraphEntityType.PROCESS)
                    & (NdRelation.source_id == process_id),
                    (NdRelation.target_type == NdGraphEntityType.PROCESS)
                    & (NdRelation.target_id == process_id),
                )
            )

        result = await self.db.execute(stmt.order_by(NdRelation.created_at.desc()))
        relations = list(result.scalars().all())
        cache = await self._load_resolution_cache(relations, scope)
        items = [map_relation_to_display(relation, cache) for relation in relations]
        items = self._apply_relation_display_filters(items, filter_key)
        total = len(items)
        start = (page - 1) * size
        return items[start : start + size], total

    def _relation_item(self, relation: NdRelation, cache: RelationResolutionCache) -> dict[str, Any]:
        return map_relation_to_display(relation, cache)

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
                        "confidence_label": (
                            CONFIDENCE_LABELS.get(process.owner_confidence)
                            if process.owner_confidence
                            else None
                        ),
                        "evidence": None,
                    }
                )

        relations: list[dict[str, Any]] = []
        important_relations: list[dict[str, Any]] = []
        relations_without_evidence: list[dict[str, Any]] = []
        weak_relations: list[dict[str, Any]] = []
        if kb_ids:
            rel_stmt = select(NdRelation).where(
                self._department_relation_filter(scope),
                NdRelation.is_confirmed.is_(False),
            )
            if query:
                pattern = f"%{query.strip()}%"
                rel_stmt = rel_stmt.where(
                    or_(NdRelation.source_name.ilike(pattern), NdRelation.target_name.ilike(pattern))
                )
            pending_relations = list((await self.db.execute(rel_stmt.limit(500))).scalars().all())
            cache = await self._load_resolution_cache(pending_relations, scope)
            for relation in pending_relations:
                item = map_relation_to_display(relation, cache)
                relations.append(item)
                if item["is_weak_relation"]:
                    weak_relations.append(item)
                elif not item["has_evidence"]:
                    relations_without_evidence.append(item)
                elif item["is_primary_relation"]:
                    important_relations.append(item)

        extraction_errors: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        if kb_ids:
            doc_stmt = select(DocumentCard).where(
                DocumentCard.knowledge_base_id.in_(kb_ids),
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
                if card.extraction_status == NdExtractionStatus.FAILED:
                    raw = card.raw_extracted_json or {}
                    extraction_errors.append(
                        {
                            "document_card_id": card.id,
                            "document_id": card.document_id,
                            "document_code": card.document_code,
                            "title": card.title or card.file_name,
                            "reason": raw.get("error") or "Ошибка извлечения",
                            "extraction_status": card.extraction_status.value,
                        }
                    )
                if card.extraction_status == NdExtractionStatus.NEEDS_REVIEW:
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
            "important_relations": important_relations,
            "relations_without_evidence": relations_without_evidence,
            "weak_relations": weak_relations,
            "extraction_errors": extraction_errors,
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

    async def bulk_approve_relations(self, relation_ids: list[uuid.UUID]) -> dict[str, Any]:
        approved: list[uuid.UUID] = []
        skipped: list[uuid.UUID] = []
        cache = RelationResolutionCache()
        for relation_id in relation_ids:
            relation = await self.db.get(NdRelation, relation_id)
            if relation is None:
                skipped.append(relation_id)
                continue
            display = map_relation_to_display(relation, cache)
            if not display["can_bulk_approve"]:
                skipped.append(relation_id)
                continue
            relation.is_confirmed = True
            approved.append(relation_id)
        await self.db.flush()
        return {"approved": approved, "skipped": skipped}

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
