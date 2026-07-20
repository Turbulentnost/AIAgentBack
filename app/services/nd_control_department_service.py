from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    DepartmentAnalysisRunStatus,
    KnowledgeBaseAccessType,
    NdChangeJournalEventType,
    NdChangeJournalSource,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.models.nd_control_registry import (
    NdControlDepartment,
    NdControlDepartmentKnowledgeBase,
)
from app.models.nd_control_structural import DocumentCard
from app.models.user import User
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.nd_change_journal_service import NdChangeJournalService
from app.services.nd_control_permission import can_manage_nd_control_departments
from app.services.nd_document_card_service import NdDocumentCardService


class NdControlDepartmentServiceError(Exception):
    pass


class NdControlDepartmentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.card_service = NdDocumentCardService(db)
        self.kb_access = KnowledgeBaseAccessService(db)

    async def list_departments(self, *, active_only: bool = True) -> list[dict]:
        stmt = select(NdControlDepartment).order_by(
            NdControlDepartment.sort_order,
            NdControlDepartment.name,
        )
        if active_only:
            stmt = stmt.where(NdControlDepartment.is_active.is_(True))
        result = await self.db.execute(
            stmt.options(selectinload(NdControlDepartment.knowledge_base_links))
        )
        departments = list(result.scalars().unique().all())
        run_rows = await self._latest_runs_by_department([dept.id for dept in departments])
        detail_service = None
        items: list[dict] = []
        for dept in departments:
            kb_count = len(dept.knowledge_base_links)
            kb_ids = [link.knowledge_base_id for link in dept.knowledge_base_links]
            cards_count = 0
            documents_count = 0
            processes_count = 0
            pending_review_count = 0
            if kb_ids:
                from app.services.nd_control_department_detail_service import NdControlDepartmentDetailService

                if detail_service is None:
                    detail_service = NdControlDepartmentDetailService(self.db)
                scope = await detail_service.get_department_scope(dept.id)
                cards_count = int(
                    await self.db.scalar(
                        select(func.count())
                        .select_from(DocumentCard)
                        .where(DocumentCard.knowledge_base_id.in_(kb_ids))
                    )
                    or 0
                )
                for kb_id in kb_ids:
                    docs_meta = await detail_service.kb_access.list_documents(str(kb_id))
                    documents_count += len(docs_meta)
                processes_count = await detail_service.count_department_processes(scope)
                pending_review_count = await detail_service.count_department_pending_review(scope)
            latest_run = run_rows.get(dept.id)
            items.append(
                {
                    "department": dept,
                    "knowledge_bases_count": kb_count,
                    "cards_count": cards_count,
                    "documents_count": documents_count,
                    "processes_count": processes_count,
                    "pending_review_count": pending_review_count,
                    "knowledge_base_ids": [link.knowledge_base_id for link in dept.knowledge_base_links],
                    "analysis_status": latest_run.status.value if latest_run else None,
                    "analysis_progress_percent": latest_run.progress_percent if latest_run else None,
                }
            )
        return items

    async def _latest_runs_by_department(
        self,
        department_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, DepartmentAnalysisRun]:
        if not department_ids:
            return {}
        result = await self.db.execute(
            select(DepartmentAnalysisRun)
            .where(DepartmentAnalysisRun.department_id.in_(department_ids))
            .order_by(DepartmentAnalysisRun.department_id, DepartmentAnalysisRun.created_at.desc())
        )
        runs = list(result.scalars().all())
        latest: dict[uuid.UUID, DepartmentAnalysisRun] = {}
        for run in runs:
            if run.department_id not in latest:
                latest[run.department_id] = run
        return latest

    async def get_department(self, department_id: uuid.UUID) -> NdControlDepartment | None:
        return await self.db.scalar(
            select(NdControlDepartment)
            .where(NdControlDepartment.id == department_id)
            .options(selectinload(NdControlDepartment.knowledge_base_links))
        )

    async def get_department_or_raise(self, department_id: uuid.UUID) -> NdControlDepartment:
        dept = await self.get_department(department_id)
        if dept is None or not dept.is_active:
            raise NdControlDepartmentServiceError("Отдел агента не найден")
        return dept

    async def _validate_kb_access(self, user: User, knowledge_base_ids: list[uuid.UUID]) -> None:
        if not knowledge_base_ids:
            raise NdControlDepartmentServiceError("Нужно выбрать хотя бы одну базу знаний")
        for kb_id in knowledge_base_ids:
            kb = await self.db.get(KnowledgeBase, kb_id)
            if kb is None or kb.deleted_at is not None:
                raise NdControlDepartmentServiceError(f"База знаний {kb_id} не найдена")
            access = await self.kb_access.can_access_knowledge_base(
                user=user,
                knowledge_base=kb,
                required_access=KnowledgeBaseAccessType.READ,
            )
            if not access.allowed:
                raise NdControlDepartmentServiceError(f"Нет доступа к базе знаний «{kb.name}»")

    async def create_department(
        self,
        *,
        name: str,
        knowledge_base_ids: list[uuid.UUID],
        current_user: User,
        description: str | None = None,
    ) -> NdControlDepartment:
        if not await can_manage_nd_control_departments(self.db, current_user):
            raise NdControlDepartmentServiceError("Недостаточно прав для создания отдела")
        clean_name = name.strip()
        if not clean_name:
            raise NdControlDepartmentServiceError("Укажите название отдела")
        existing = await self.db.scalar(
            select(NdControlDepartment).where(
                NdControlDepartment.name.ilike(clean_name),
                NdControlDepartment.is_active.is_(True),
            )
        )
        if existing is not None:
            raise NdControlDepartmentServiceError("Отдел с таким названием уже существует")

        await self._validate_kb_access(current_user, knowledge_base_ids)
        dept = NdControlDepartment(
            name=clean_name,
            description=description,
            created_by_user_id=current_user.id,
        )
        self.db.add(dept)
        await self.db.flush()

        for kb_id in knowledge_base_ids:
            self.db.add(
                NdControlDepartmentKnowledgeBase(
                    department_id=dept.id,
                    knowledge_base_id=kb_id,
                )
            )
        await self.db.flush()
        await self.db.refresh(dept, attribute_names=["knowledge_base_links"])

        for kb_id in knowledge_base_ids:
            await self.card_service.backfill_cards_for_department_kb(dept, kb_id)
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CONTROL_DEPARTMENT_CREATED,
            actor_user_id=current_user.id,
            resource_type="nd_control_department",
            resource_id=dept.id,
            summary=f"Создан отдел агента НД «{dept.name}»",
            source=NdChangeJournalSource.MANUAL,
            payload={"knowledge_base_ids": [str(kb_id) for kb_id in knowledge_base_ids]},
        )
        return dept

    async def update_department(
        self,
        department_id: uuid.UUID,
        *,
        current_user: User,
        name: str | None = None,
        description: str | None = None,
        sort_order: int | None = None,
    ) -> NdControlDepartment:
        if not await can_manage_nd_control_departments(self.db, current_user):
            raise NdControlDepartmentServiceError("Недостаточно прав")
        dept = await self.get_department_or_raise(department_id)
        if name is not None:
            clean = name.strip()
            if not clean:
                raise NdControlDepartmentServiceError("Укажите название отдела")
            dept.name = clean
        if description is not None:
            dept.description = description
        if sort_order is not None:
            dept.sort_order = sort_order
        await self.db.flush()
        return dept

    async def delete_department(self, department_id: uuid.UUID, *, current_user: User) -> None:
        if not await can_manage_nd_control_departments(self.db, current_user):
            raise NdControlDepartmentServiceError("Недостаточно прав")
        dept = await self.get_department_or_raise(department_id)
        dept.is_active = False
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CONTROL_DEPARTMENT_DELETED,
            actor_user_id=current_user.id,
            resource_type="nd_control_department",
            resource_id=dept.id,
            summary=f"Удалён отдел агента НД «{dept.name}»",
            source=NdChangeJournalSource.MANUAL,
        )
        await self.db.flush()

    async def set_department_knowledge_bases(
        self,
        department_id: uuid.UUID,
        knowledge_base_ids: list[uuid.UUID],
        *,
        current_user: User,
    ) -> NdControlDepartment:
        if not await can_manage_nd_control_departments(self.db, current_user):
            raise NdControlDepartmentServiceError("Недостаточно прав")
        dept = await self.get_department_or_raise(department_id)
        await self._validate_kb_access(current_user, knowledge_base_ids)

        existing_ids = {link.knowledge_base_id for link in dept.knowledge_base_links}
        new_ids = set(knowledge_base_ids)

        for link in list(dept.knowledge_base_links):
            if link.knowledge_base_id not in new_ids:
                await self.db.delete(link)
        await self.db.flush()

        for kb_id in new_ids - existing_ids:
            self.db.add(
                NdControlDepartmentKnowledgeBase(
                    department_id=dept.id,
                    knowledge_base_id=kb_id,
                )
            )
        await self.db.flush()
        await self.db.refresh(dept, attribute_names=["knowledge_base_links"])

        for kb_id in new_ids:
            await self.card_service.backfill_cards_for_department_kb(dept, kb_id)
        return dept
