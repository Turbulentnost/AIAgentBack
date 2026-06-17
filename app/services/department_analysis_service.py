from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nd_control_agent.knowledge_base_access_service import KnowledgeBaseAccessService
from app.core.logging import get_logger
from app.models.enums import (
    DepartmentAnalysisRunStatus,
    DepartmentAnalysisStep,
    NdBuildStatus,
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
    ConfidenceLevel,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.models.nd_control_registry import NdControlDepartment
from app.models.nd_control_structural import DepartmentProfile, DocumentCard, NdRelation, ProcessCard
from app.services.nd_control_department_service import (
    NdControlDepartmentService,
    NdControlDepartmentServiceError,
)
from app.services.nd_document_card_extraction_service import NdDocumentCardExtractionService

logger = get_logger(__name__)

STEP_MESSAGES = {
    DepartmentAnalysisStep.INITIALIZING: "Анализ запускается…",
    DepartmentAnalysisStep.LOADING_KNOWLEDGE_BASES: "Загружаем базы знаний…",
    DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS: "Извлекаем карточки документов…",
    DepartmentAnalysisStep.BUILDING_DEPARTMENT_PROFILE: "Строим профиль отдела…",
    DepartmentAnalysisStep.BUILDING_RELATIONS: "Формируем связи процессов и подразделений…",
    DepartmentAnalysisStep.COMPLETED: "Готово",
    DepartmentAnalysisStep.FAILED: "Ошибка анализа",
}


class DepartmentAnalysisServiceError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DepartmentAnalysisService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.kb_access = KnowledgeBaseAccessService(db)
        self.extraction_service = NdDocumentCardExtractionService(db, kb_access=self.kb_access)
        self.department_service = NdControlDepartmentService(db)

    async def start_department_analysis(
        self,
        department_id: uuid.UUID,
        *,
        force_reextract: bool = False,
    ) -> DepartmentAnalysisRun:
        dept = await self.department_service.get_department_or_raise(department_id)
        run = DepartmentAnalysisRun(
            department_id=dept.id,
            status=DepartmentAnalysisRunStatus.PENDING,
            current_step=DepartmentAnalysisStep.INITIALIZING,
            progress_percent=0,
            summary_json={"force_reextract": force_reextract},
        )
        self.db.add(run)
        await self.db.flush()
        logger.info(
            "nd_control.analysis.started",
            department_id=str(department_id),
            run_id=str(run.id),
        )
        return run

    async def execute_department_analysis(
        self,
        run_id: uuid.UUID,
        *,
        force_reextract: bool = False,
    ) -> DepartmentAnalysisRun:
        run = await self._get_run_or_raise(run_id)
        dept = await self.department_service.get_department_or_raise(run.department_id)
        now = _utcnow()
        run.status = DepartmentAnalysisRunStatus.RUNNING
        run.started_at = run.started_at or now
        run.current_step = DepartmentAnalysisStep.INITIALIZING
        run.progress_percent = calculate_progress_percent(
            DepartmentAnalysisStep.INITIALIZING,
            processed=0,
            total=0,
        )
        await self.db.flush()

        kb_ids = [link.knowledge_base_id for link in dept.knowledge_base_links]
        summary: dict[str, Any] = {
            "force_reextract": force_reextract,
            "knowledge_bases": [],
            "documents": [],
            "profile_status": None,
            "relations_created": 0,
            "processes_created": 0,
        }

        try:
            run.current_step = DepartmentAnalysisStep.LOADING_KNOWLEDGE_BASES
            run.total_knowledge_bases = len(kb_ids)
            run.total_documents = await self.count_department_documents(dept.id)
            run.current_step = DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS
            run.progress_percent = calculate_progress_percent(
                DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS,
                processed=0,
                total=run.total_documents,
            )
            await self.db.flush()
            await self.db.commit()

            extraction_summary = await self.extract_document_cards_for_department(
                dept.id,
                force_reextract=force_reextract,
                progress_callback=lambda counts: self._update_extraction_progress(
                    run,
                    counts=counts,
                ),
            )
            summary["knowledge_bases"] = extraction_summary.get("knowledge_bases", [])
            summary["documents"] = extraction_summary.get("documents", [])
            run.total_documents = int(extraction_summary.get("total_documents", 0))
            run.processed_documents = int(extraction_summary.get("processed", 0))
            run.skipped_documents = int(extraction_summary.get("skipped", 0))
            run.failed_documents = int(extraction_summary.get("failed", 0))
            run.needs_review_documents = int(extraction_summary.get("needs_review", 0))
            run.summary_json = summary
            await self.db.flush()

            run.current_step = DepartmentAnalysisStep.BUILDING_DEPARTMENT_PROFILE
            run.progress_percent = calculate_progress_percent(
                DepartmentAnalysisStep.BUILDING_DEPARTMENT_PROFILE,
                processed=run.processed_documents,
                total=run.total_documents,
            )
            await self.db.flush()
            profile_result = await self.build_department_profile_after_extraction(dept.id)
            summary["profile_status"] = profile_result.get("status")
            summary["processes_created"] = profile_result.get("processes_count", 0)

            run.current_step = DepartmentAnalysisStep.BUILDING_RELATIONS
            run.progress_percent = calculate_progress_percent(
                DepartmentAnalysisStep.BUILDING_RELATIONS,
                processed=run.processed_documents,
                total=run.total_documents,
            )
            await self.db.flush()
            relations_created = await self._build_department_relations(dept, kb_ids)
            summary["relations_created"] = relations_created

            run.summary_json = summary
            run.finished_at = _utcnow()
            run.progress_percent = 100

            if run.total_documents > 0 and run.failed_documents == run.total_documents:
                run.status = DepartmentAnalysisRunStatus.FAILED
                run.current_step = DepartmentAnalysisStep.FAILED
                run.error_message = "Не удалось обработать ни одного документа"
            elif run.failed_documents > 0 or run.needs_review_documents > 0:
                run.status = DepartmentAnalysisRunStatus.COMPLETED_WITH_WARNINGS
                run.current_step = DepartmentAnalysisStep.COMPLETED
            else:
                run.status = DepartmentAnalysisRunStatus.COMPLETED
                run.current_step = DepartmentAnalysisStep.COMPLETED

        except Exception as exc:
            logger.exception("nd_control.analysis.failed", run_id=str(run_id))
            run.status = DepartmentAnalysisRunStatus.FAILED
            run.current_step = DepartmentAnalysisStep.FAILED
            run.error_message = str(exc)
            run.finished_at = _utcnow()
            summary["error"] = str(exc)
            run.summary_json = summary

        await self.db.flush()
        return run

    async def count_department_documents(self, department_id: uuid.UUID) -> int:
        dept = await self.department_service.get_department_or_raise(department_id)
        total = 0
        for link in dept.knowledge_base_links:
            documents_meta = await self.kb_access.list_documents(str(link.knowledge_base_id))
            total += len(documents_meta)
        return total

    async def extract_document_cards_for_department(
        self,
        department_id: uuid.UUID,
        *,
        force_reextract: bool = False,
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        dept = await self.department_service.get_department_or_raise(department_id)
        kb_ids = [link.knowledge_base_id for link in dept.knowledge_base_links]
        kb_summaries: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        totals = {
            "total_documents": await self.count_department_documents(department_id),
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "needs_review": 0,
        }

        for kb_id in kb_ids:
            kb_offset = {key: totals[key] for key in ("processed", "skipped", "failed", "needs_review")}

            async def on_document_complete(kb_summary: dict[str, Any]) -> None:
                if progress_callback is None:
                    return
                counts = {
                    "total_documents": totals["total_documents"],
                    "processed": kb_offset["processed"] + int(kb_summary.get("processed", 0)),
                    "skipped": kb_offset["skipped"] + int(kb_summary.get("skipped", 0)),
                    "failed": kb_offset["failed"] + int(kb_summary.get("failed", 0)),
                    "needs_review": kb_offset["needs_review"] + int(kb_summary.get("needs_review", 0)),
                }
                await progress_callback(counts)

            kb_summary = await self.extract_document_cards_for_knowledge_base(
                kb_id,
                force_reextract=force_reextract,
                on_document_complete=on_document_complete if progress_callback else None,
            )
            kb_summaries.append(kb_summary)
            documents.extend(kb_summary.get("documents", []))
            for key in ("processed", "skipped", "failed", "needs_review"):
                totals[key] += int(kb_summary.get(key, 0))

        return {**totals, "knowledge_bases": kb_summaries, "documents": documents}

    async def extract_document_cards_for_knowledge_base(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        force_reextract: bool = False,
        on_document_complete: Any | None = None,
    ) -> dict[str, Any]:
        kb = await self.db.get(KnowledgeBase, knowledge_base_id)
        kb_name = kb.name if kb else str(knowledge_base_id)
        documents_meta = await self.kb_access.list_documents(str(knowledge_base_id))

        summary = {
            "knowledge_base_id": str(knowledge_base_id),
            "name": kb_name,
            "total_documents": len(documents_meta),
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "needs_review": 0,
            "documents": [],
        }

        for item in documents_meta:
            doc_id = item.document_id
            entry = {
                "document_id": str(doc_id),
                "file_name": item.file_name,
                "status": "pending",
                "message": "",
            }
            existing = await self._get_structural_card(doc_id)
            if existing is not None and existing.extraction_status == NdExtractionStatus.COMPLETED and not force_reextract:
                entry["status"] = "skipped"
                entry["message"] = "Уже извлечено"
                summary["skipped"] += 1
                summary["documents"].append(entry)
                continue

            should_extract = (
                existing is None
                or force_reextract
                or existing.extraction_status
                in {NdExtractionStatus.FAILED, NdExtractionStatus.NEEDS_REVIEW, NdExtractionStatus.PENDING}
            )
            if existing is not None and existing.extraction_status == NdExtractionStatus.COMPLETED and force_reextract:
                should_extract = True

            if not should_extract:
                entry["status"] = "skipped"
                summary["skipped"] += 1
                summary["documents"].append(entry)
                continue

            try:
                card = await self.extraction_service.extract_document_card(str(doc_id))
                if card.extraction_status == NdExtractionStatus.COMPLETED:
                    entry["status"] = "completed"
                    summary["processed"] += 1
                elif card.extraction_status == NdExtractionStatus.NEEDS_REVIEW:
                    entry["status"] = "needs_review"
                    entry["message"] = (card.raw_extracted_json or {}).get("message", "Требует проверки")
                    summary["needs_review"] += 1
                else:
                    entry["status"] = "failed"
                    entry["message"] = (card.raw_extracted_json or {}).get("error", "Ошибка извлечения")
                    summary["failed"] += 1
            except Exception as exc:
                logger.warning(
                    "nd_control.analysis.document_failed",
                    document_id=str(doc_id),
                    error=str(exc),
                )
                entry["status"] = "failed"
                entry["message"] = str(exc)
                summary["failed"] += 1

            summary["documents"].append(entry)
            if on_document_complete is not None:
                await on_document_complete(summary)

        return summary

    async def build_department_profile_after_extraction(self, department_id: uuid.UUID) -> dict[str, Any]:
        dept = await self.department_service.get_department_or_raise(department_id)
        kb_ids = [link.knowledge_base_id for link in dept.knowledge_base_links]
        if not kb_ids:
            return {"status": "failed", "message": "Нет баз знаний"}

        result = await self.db.execute(
            select(DocumentCard).where(DocumentCard.knowledge_base_id.in_(kb_ids))
        )
        cards = list(result.scalars().all())

        purposes = [card.purpose for card in cards if card.purpose]
        scopes = [card.scope_text for card in cards if card.scope_text]
        functions = [
            {
                "name": card.document_code or card.title or card.file_name or str(card.document_id),
                "description": card.purpose,
                "source_document_ids": [str(card.document_id)],
            }
            for card in cards
            if card.purpose or card.title
        ]

        profile = await self.db.scalar(
            select(DepartmentProfile).where(DepartmentProfile.department_id == department_id)
        )
        needs_review = any(
            card.extraction_status in {NdExtractionStatus.NEEDS_REVIEW, NdExtractionStatus.FAILED}
            for card in cards
        )
        build_status = NdBuildStatus.NEEDS_REVIEW if needs_review else NdBuildStatus.COMPLETED

        if profile is None:
            profile = DepartmentProfile(
                department_id=department_id,
                department_name=dept.name,
                summary="; ".join(purposes[:3]) if purposes else None,
                purpose=purposes[0] if purposes else None,
                functions_json=functions,
                source_knowledge_base_ids=[str(kb_id) for kb_id in kb_ids],
                build_status=build_status,
                raw_profile_json={
                    "scopes": scopes,
                    "document_cards_count": len(cards),
                },
            )
            self.db.add(profile)
        else:
            profile.department_name = dept.name
            profile.summary = "; ".join(purposes[:3]) if purposes else profile.summary
            profile.purpose = purposes[0] if purposes else profile.purpose
            profile.functions_json = functions
            profile.source_knowledge_base_ids = [str(kb_id) for kb_id in kb_ids]
            profile.build_status = build_status
            profile.raw_profile_json = {
                **(profile.raw_profile_json or {}),
                "scopes": scopes,
                "document_cards_count": len(cards),
            }

        processes_count = await self.db.scalar(select(func.count(ProcessCard.id)))
        await self.db.flush()
        return {
            "status": build_status.value,
            "profile_id": str(profile.id),
            "processes_count": int(processes_count or 0),
            "document_cards_count": len(cards),
        }

    async def get_latest_run(self, department_id: uuid.UUID) -> DepartmentAnalysisRun | None:
        result = await self.db.execute(
            select(DepartmentAnalysisRun)
            .where(DepartmentAnalysisRun.department_id == department_id)
            .order_by(DepartmentAnalysisRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_analysis_status(self, department_id: uuid.UUID) -> dict[str, Any]:
        await self.department_service.get_department_or_raise(department_id)
        run = await self.get_latest_run(department_id)
        if run is None:
            return {
                "department_id": department_id,
                "run_id": None,
                "status": None,
                "current_step": None,
                "progress_percent": 0,
                "total_documents": 0,
                "processed_documents": 0,
                "skipped_documents": 0,
                "failed_documents": 0,
                "needs_review_documents": 0,
                "message": "Анализ ещё не запускался",
                "summary": {},
            }

        message = STEP_MESSAGES.get(run.current_step, run.current_step.value)
        if run.status == DepartmentAnalysisRunStatus.PENDING and run.started_at is None:
            message = "Ожидание запуска анализа…"
        elif run.status == DepartmentAnalysisRunStatus.FAILED and run.error_message:
            message = run.error_message
        elif run.current_step == DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS and run.total_documents:
            done = (
                run.processed_documents
                + run.skipped_documents
                + run.failed_documents
                + run.needs_review_documents
            )
            if done == 0 and run.status == DepartmentAnalysisRunStatus.RUNNING:
                message = (
                    f"Извлекаем данные из документов через LLM "
                    f"(0 из {run.total_documents}, один документ может занять несколько минут)…"
                )
            else:
                message = f"Обработано {done} из {run.total_documents} документов"

        return {
            "department_id": department_id,
            "run_id": run.id,
            "status": run.status,
            "current_step": run.current_step,
            "progress_percent": run.progress_percent,
            "total_documents": run.total_documents,
            "processed_documents": run.processed_documents,
            "skipped_documents": run.skipped_documents,
            "failed_documents": run.failed_documents,
            "needs_review_documents": run.needs_review_documents,
            "message": message,
            "summary": run.summary_json or {},
        }

    async def get_department_summary(self, department_id: uuid.UUID) -> dict[str, Any]:
        from app.services.nd_control_department_detail_service import NdControlDepartmentDetailService

        dept = await self.department_service.get_department_or_raise(department_id)
        detail = NdControlDepartmentDetailService(self.db)
        scope = await detail.get_department_scope(department_id)
        run = await self.get_latest_run(department_id)
        kb_ids = scope["kb_ids"]

        cards_count = 0
        documents_count = 0
        if kb_ids:
            cards_count = int(
                await self.db.scalar(
                    select(func.count(DocumentCard.id)).where(DocumentCard.knowledge_base_id.in_(kb_ids))
                )
                or 0
            )
            for kb_id in kb_ids:
                docs_meta = await self.kb_access.list_documents(str(kb_id))
                documents_count += len(docs_meta)

        processes_count = await detail.count_department_processes(scope)
        relations_count = await detail.count_department_relations(scope)
        pending_review = await detail.count_department_pending_review(scope)
        kb_items = await detail.get_knowledge_base_summaries(department_id)

        analysis_status = run.status.value if run else None
        if run and run.status == DepartmentAnalysisRunStatus.COMPLETED_WITH_WARNINGS:
            analysis_status = "needs_review"

        last_analysis_at = None
        if run:
            last_analysis_at = run.finished_at or run.started_at or run.created_at

        return {
            "department_id": department_id,
            "department_name": dept.name,
            "analysis_status": analysis_status,
            "knowledge_bases": kb_items,
            "knowledge_bases_count": len(kb_ids),
            "documents_count": documents_count,
            "document_cards_count": cards_count,
            "processes_count": processes_count,
            "relations_count": relations_count,
            "pending_review_count": pending_review,
            "last_analysis_at": last_analysis_at,
            "last_analysis_run": run,
        }

    async def _build_department_relations(
        self,
        dept: NdControlDepartment,
        kb_ids: list[uuid.UUID],
    ) -> int:
        created = 0
        result = await self.db.execute(
            select(ProcessCard).where(ProcessCard.source_document_ids.is_not(None))
        )
        processes = list(result.scalars().all())
        for process in processes:
            if await self._relation_exists(
                source_type=NdGraphEntityType.DEPARTMENT,
                source_id=dept.id,
                source_name=dept.name,
                relation_type=NdRelationType.DEPARTMENT_OWNS_PROCESS,
                target_type=NdGraphEntityType.PROCESS,
                target_id=process.id,
                target_name=process.canonical_name,
            ):
                continue
            self.db.add(
                NdRelation(
                    source_type=NdGraphEntityType.DEPARTMENT,
                    source_id=dept.id,
                    source_name=dept.name,
                    relation_type=NdRelationType.DEPARTMENT_OWNS_PROCESS,
                    target_type=NdGraphEntityType.PROCESS,
                    target_id=process.id,
                    target_name=process.canonical_name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence_json=[{"source": "department_profile_build"}],
                    is_confirmed=False,
                )
            )
            created += 1
        await self.db.flush()
        return created

    async def _update_extraction_progress(
        self,
        run: DepartmentAnalysisRun,
        *,
        counts: dict[str, int],
    ) -> None:
        run.current_step = DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS
        run.total_documents = int(counts.get("total_documents", run.total_documents))
        run.processed_documents = int(counts.get("processed", 0))
        run.skipped_documents = int(counts.get("skipped", 0))
        run.failed_documents = int(counts.get("failed", 0))
        run.needs_review_documents = int(counts.get("needs_review", 0))
        done = (
            run.processed_documents
            + run.skipped_documents
            + run.failed_documents
            + run.needs_review_documents
        )
        run.progress_percent = calculate_progress_percent(
            DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS,
            processed=done,
            total=run.total_documents,
        )
        await self.db.flush()
        await self.db.commit()

    async def _get_structural_card(self, document_id: uuid.UUID) -> DocumentCard | None:
        return await self.db.scalar(select(DocumentCard).where(DocumentCard.document_id == document_id))

    async def _get_run_or_raise(self, run_id: uuid.UUID) -> DepartmentAnalysisRun:
        run = await self.db.get(DepartmentAnalysisRun, run_id)
        if run is None:
            raise DepartmentAnalysisServiceError("Запуск анализа не найден")
        return run

    async def _relation_exists(
        self,
        *,
        source_type: NdGraphEntityType,
        source_id: uuid.UUID | None,
        source_name: str,
        relation_type: NdRelationType,
        target_type: NdGraphEntityType,
        target_id: uuid.UUID | None,
        target_name: str,
    ) -> bool:
        stmt = select(NdRelation.id).where(
            NdRelation.source_type == source_type,
            NdRelation.relation_type == relation_type,
            NdRelation.target_type == target_type,
            NdRelation.source_name == source_name,
            NdRelation.target_name == target_name,
        )
        if source_id is not None:
            stmt = stmt.where(NdRelation.source_id == source_id)
        if target_id is not None:
            stmt = stmt.where(NdRelation.target_id == target_id)
        result = await self.db.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None


def calculate_progress_percent(
    step: DepartmentAnalysisStep,
    *,
    processed: int,
    total: int,
) -> int:
    if step == DepartmentAnalysisStep.INITIALIZING:
        return 3
    if step == DepartmentAnalysisStep.LOADING_KNOWLEDGE_BASES:
        return 8
    if step == DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS:
        if total <= 0:
            return 15
        ratio = min(1.0, max(0.0, processed / total))
        return int(10 + ratio * 65)
    if step == DepartmentAnalysisStep.BUILDING_DEPARTMENT_PROFILE:
        return 82
    if step == DepartmentAnalysisStep.BUILDING_RELATIONS:
        return 94
    if step == DepartmentAnalysisStep.COMPLETED:
        return 100
    return 0
