from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import PurePath

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.nd_control_agent.config import AGENT_ID
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import (
    KnowledgeBaseAccessType,
    KnowledgeBaseStatus,
    NdChangeApprovalStatus,
    NdChangeDraftFileStatus,
    NdChangeJournalEventType,
    NdChangeJournalSource,
    NdChangeLocationStatus,
    NdChangeOperationStatus,
    NdChangeRequestStatus,
    NdChangeResultStatus,
)
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource
from app.models.nd_change import (
    NdChangeApprovalParticipant,
    NdChangeApprovalRoute,
    NdChangeCandidateDocument,
    NdChangeDraftFile,
    NdChangeOperation,
    NdChangeRequest,
    NdChangeResult,
    NdChangeTargetLocation,
)
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.document_editing import DocumentEditService
from app.services.document_editing.schemas import LocatedChange
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.nd_change_journal_service import NdChangeJournalService

DOCUMENT_CODE_RE = re.compile(r"\b(?:СТО|И|РГ|ПЛ|ДИ|РИ|ПП)-\d{2}-\d{3}\b", re.IGNORECASE)


class NdChangeServiceError(ValueError):
    pass


class NdChangeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def create(self, payload, *, current_user: User) -> NdChangeRequest:
        request = NdChangeRequest(
            number=await self._next_number(),
            reason=payload.reason,
            release_date=payload.release_date,
            effective_date=payload.effective_date,
            change_text=payload.change_text,
            initiator_user_id=current_user.id,
            department_id=payload.department_id or current_user.department_id,
            status=NdChangeRequestStatus.DRAFT,
            selected_document_id=payload.assumed_document_id,
            metadata_={
                **(payload.metadata or {}),
                "assumed_document_code": payload.assumed_document_code,
                "attachments": payload.attachments,
                "distribution_list": payload.distribution_list,
                "initiator_comment": payload.initiator_comment,
                "user_reviewed": False,
            },
        )
        self.db.add(request)
        await self.db.flush()
        await self.audit.log(
            action="nd_change_request_created",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"number": request.number},
        )
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CHANGE_REQUEST_CREATED,
            actor_user_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=request.id,
            department_id=request.department_id,
            document_id=request.selected_document_id,
            document_code=payload.assumed_document_code,
            summary=f"Создана заявка на изменение НД №{request.number}",
            source=NdChangeJournalSource.ND_CHANGE_WORKFLOW,
            payload={
                "number": request.number,
                "reason": request.reason,
                "assumed_document_code": payload.assumed_document_code,
            },
        )
        return request

    async def list(self, *, current_user: User) -> list[NdChangeRequest]:
        stmt = select(NdChangeRequest).order_by(NdChangeRequest.created_at.desc())
        if not current_user.is_superuser:
            stmt = stmt.where(
                or_(
                    NdChangeRequest.initiator_user_id == current_user.id,
                    NdChangeRequest.department_id == current_user.department_id,
                )
            )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_or_raise(self, request_id: uuid.UUID) -> NdChangeRequest:
        request = await self.db.get(NdChangeRequest, request_id)
        if request is None:
            raise NdChangeServiceError("Заявка на изменение НД не найдена")
        return request

    async def get_full(self, request_id: uuid.UUID) -> NdChangeRequest:
        result = await self.db.execute(
            select(NdChangeRequest)
            .where(NdChangeRequest.id == request_id)
            .options(
                selectinload(NdChangeRequest.candidates),
                selectinload(NdChangeRequest.target_locations),
                selectinload(NdChangeRequest.operations),
                selectinload(NdChangeRequest.draft_files),
                selectinload(NdChangeRequest.approval_routes).selectinload(NdChangeApprovalRoute.participants),
                selectinload(NdChangeRequest.results),
            )
        )
        request = result.scalar_one_or_none()
        if request is None:
            raise NdChangeServiceError("Заявка на изменение НД не найдена")
        return request

    async def detect_document(self, request_id: uuid.UUID, *, current_user: User) -> list[NdChangeCandidateDocument]:
        request = await self.get_or_raise(request_id)
        self._validate_required_fields(request)
        request.status = NdChangeRequestStatus.DETECTING_DOCUMENT
        await self.audit.log(
            action="nd_document_detection_started",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
        )
        await self._clear_candidates(request.id)

        candidates = await self._build_candidates(request, current_user)
        for rank, item in enumerate(candidates, start=1):
            self.db.add(
                NdChangeCandidateDocument(
                    change_request_id=request.id,
                    document_id=item["document"].id,
                    document_version_id=item["version_id"],
                    score=item["score"],
                    rank=rank,
                    match_reason=item["reason"],
                    matched_fragments=item["fragments"],
                    is_selected=False,
                )
            )
        await self.db.flush()

        saved = await self._load_candidates(request.id)
        top = saved[0] if saved else None
        request.detection_confidence = top.score if top else 0
        request.requires_manual_document_selection = False
        if top and top.score >= 0.80:
            await self.select_document(
                request.id,
                document_id=top.document_id,
                document_version_id=top.document_version_id,
                current_user=current_user,
                auto_selected=True,
            )
        elif top and top.score >= 0.55:
            request.status = NdChangeRequestStatus.REQUIRES_MANUAL_DOCUMENT_SELECTION
            request.requires_manual_document_selection = True
        else:
            request.status = NdChangeRequestStatus.REQUIRES_MANUAL_DOCUMENT_SELECTION
            request.requires_manual_document_selection = True

        await self.audit.log(
            action="nd_document_candidates_found",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"count": len(saved), "top_score": request.detection_confidence},
        )
        return saved

    async def select_document(
        self,
        request_id: uuid.UUID,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        current_user: User,
        auto_selected: bool = False,
    ) -> NdChangeRequest:
        request = await self.get_or_raise(request_id)
        await self._ensure_document_access(document_id, current_user)
        request.selected_document_id = document_id
        request.selected_document_version_id = document_version_id
        request.requires_manual_document_selection = False
        request.status = NdChangeRequestStatus.DOCUMENT_SELECTED
        for candidate in await self._load_candidates(request.id):
            candidate.is_selected = candidate.document_id == document_id
            if candidate.is_selected:
                request.detection_confidence = candidate.score
        await self.audit.log(
            action="nd_document_selected",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"document_id": str(document_id), "auto_selected": auto_selected},
        )
        await self.db.flush()
        return request

    async def find_location(self, request_id: uuid.UUID, *, current_user: User) -> list[NdChangeTargetLocation]:
        request = await self.get_or_raise(request_id)
        document_id = request.selected_document_id
        if not document_id:
            raise NdChangeServiceError("Сначала выберите документ")
        await self._ensure_document_access(document_id, current_user)
        request.status = NdChangeRequestStatus.LOCATING_CHANGE_PLACE
        await self._clear_locations(request.id)

        locations = await DocumentEditService(self.db).locate_change_place(
            document_id=document_id,
            document_version_id=request.selected_document_version_id,
            change_text=request.change_text,
        )
        for location in locations:
            self.db.add(
                NdChangeTargetLocation(
                    change_request_id=request.id,
                    document_id=location.document_id,
                    document_version_id=location.document_version_id,
                    section_number=location.section_number,
                    section_title=location.section_title,
                    page_number=location.page_number,
                    chunk_id=location.chunk_id,
                    location_type=location.location_type,
                    current_text=location.current_text,
                    confidence=location.confidence,
                    status=NdChangeLocationStatus.FOUND if location.confidence >= 0.8 else NdChangeLocationStatus.CANDIDATE,
                )
            )
        await self.db.flush()
        saved = await self._load_locations(request.id)
        high = [item for item in saved if (item.confidence or 0) >= 0.8]
        request.requires_manual_location_selection = len(high) != 1
        request.status = (
            NdChangeRequestStatus.LOCATING_CHANGE_PLACE
            if len(high) == 1
            else NdChangeRequestStatus.REQUIRES_MANUAL_LOCATION_SELECTION
        )
        await self.audit.log(
            action="nd_change_location_found" if saved else "nd_change_location_manual_selection_required",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"count": len(saved)},
        )
        return saved

    async def apply_changes(
        self,
        request_id: uuid.UUID,
        *,
        current_user: User,
        location_id: uuid.UUID | None = None,
        mark_user_reviewed: bool = False,
    ) -> NdChangeRequest:
        request = await self.get_or_raise(request_id)
        if not request.selected_document_id:
            raise NdChangeServiceError("Документ не выбран")
        document = await self.db.get(Document, request.selected_document_id)
        if document is None:
            raise NdChangeServiceError("Документ не найден")
        await self._ensure_document_access(document.id, current_user)
        request.status = NdChangeRequestStatus.APPLYING_CHANGES

        location_model = await self._select_location(request.id, location_id)
        location = (
            LocatedChange(
                document_id=location_model.document_id,
                document_version_id=location_model.document_version_id,
                section_number=location_model.section_number,
                section_title=location_model.section_title,
                page_number=location_model.page_number,
                chunk_id=location_model.chunk_id,
                location_type=location_model.location_type,
                current_text=location_model.current_text,
                confidence=location_model.confidence or 0,
                status=location_model.status.value,
            )
            if location_model
            else None
        )
        metadata = request.metadata_ or {}
        edit_result = await DocumentEditService(self.db).apply_change(
            change_request_id=request.id,
            request_number=request.number,
            document_id=document.id,
            document_version_id=request.selected_document_version_id,
            document_title=document.title,
            reason=request.reason,
            release_date=request.release_date,
            effective_date=request.effective_date,
            change_text=request.change_text,
            location=location,
            attachments=metadata.get("attachments") or [],
            distribution_list=metadata.get("distribution_list") or [],
            initiator_comment=metadata.get("initiator_comment"),
        )
        draft_file = self._add_artifact(request, document, edit_result.draft_file, file_type="draft")
        notice_file = self._add_artifact(request, document, edit_result.notice_file, file_type="notice")
        operation = self._add_operation(request, location_model, edit_result)
        result = NdChangeResult(
            change_request_id=request.id,
            agent_id=AGENT_ID,
            status=NdChangeResultStatus.READY_FOR_USER_REVIEW,
            summary="Сформированы проект новой редакции, diff и извещение об изменении",
            confidence=request.detection_confidence,
            selected_document_id=document.id,
            draft_file_id=draft_file.id,
            change_notice_file_id=notice_file.id,
            warnings=edit_result.warnings,
            actions=edit_result.actions,
            metadata_={
                "operation_id": str(operation.id),
                "related_documents": await self._find_related_documents(document),
            },
        )
        self.db.add(result)
        metadata["user_reviewed"] = bool(mark_user_reviewed)
        request.metadata_ = metadata
        request.status = NdChangeRequestStatus.READY_FOR_USER_REVIEW
        await self.audit.log(
            action="nd_change_applied_to_draft",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"draft_object_name": edit_result.draft_file.object_name},
        )
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CHANGE_DRAFT_APPLIED,
            actor_user_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=request.id,
            department_id=request.department_id,
            document_id=document.id,
            document_name=document.title or document.original_filename,
            summary=f"Сформирован проект изменения НД по заявке №{request.number}",
            source=NdChangeJournalSource.ND_CHANGE_WORKFLOW,
            payload={
                "draft_object_name": edit_result.draft_file.object_name,
                "diff": operation.diff,
                "warnings": edit_result.warnings,
            },
        )
        await self.audit.log(
            action="nd_diff_generated",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
        )
        await self.audit.log(
            action="nd_change_notice_generated",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
        )
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CHANGE_NOTICE_GENERATED,
            actor_user_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=request.id,
            department_id=request.department_id,
            document_id=document.id,
            document_name=document.title or document.original_filename,
            summary=f"Сформировано извещение об изменении по заявке №{request.number}",
            source=NdChangeJournalSource.ND_CHANGE_WORKFLOW,
            payload={"notice_object_name": edit_result.notice_file.object_name},
        )
        await self.db.flush()
        return request

    async def send_to_approval(
        self,
        request_id: uuid.UUID,
        *,
        current_user: User,
        approval_user_ids: list[uuid.UUID],
    ) -> NdChangeApprovalRoute:
        request = await self.get_full(request_id)
        metadata = request.metadata_ or {}
        if request.status != NdChangeRequestStatus.READY_FOR_USER_REVIEW or not request.draft_files:
            raise NdChangeServiceError("Перед отправкой нужно сформировать и просмотреть итоговые файлы")
        if not metadata.get("user_reviewed"):
            raise NdChangeServiceError("Нельзя отправить на согласование без подтверждения просмотра пользователем")

        route = NdChangeApprovalRoute(
            change_request_id=request.id,
            status=NdChangeApprovalStatus.SENT,
            created_by_user_id=current_user.id,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(route)
        await self.db.flush()
        for order, user_id in enumerate(approval_user_ids, start=1):
            self.db.add(
                NdChangeApprovalParticipant(
                    approval_route_id=route.id,
                    user_id=user_id,
                    role_name="Согласующий",
                    approval_order=order,
                    status=NdChangeApprovalStatus.SENT,
                )
            )
        request.status = NdChangeRequestStatus.SENT_TO_APPROVAL
        for result in request.results:
            result.status = NdChangeResultStatus.SENT_TO_APPROVAL
        await self.audit.log(
            action="nd_sent_to_approval",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
            payload={"participants": [str(item) for item in approval_user_ids]},
        )
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.ND_CHANGE_REQUEST_COMPLETED,
            actor_user_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=request.id,
            department_id=request.department_id,
            document_id=request.selected_document_id,
            summary=f"Заявка на изменение НД №{request.number} отправлена на согласование",
            source=NdChangeJournalSource.ND_CHANGE_WORKFLOW,
            payload={"participants": [str(item) for item in approval_user_ids]},
        )
        await self.db.flush()
        return route

    async def mark_user_reviewed(self, request_id: uuid.UUID, *, current_user: User) -> NdChangeRequest:
        request = await self.get_or_raise(request_id)
        metadata = request.metadata_ or {}
        metadata["user_reviewed"] = True
        request.metadata_ = metadata
        await self.audit.log(
            action="nd_user_review_completed",
            actor_id=current_user.id,
            resource_type="nd_change_request",
            resource_id=str(request.id),
        )
        await self.db.flush()
        return request

    async def _build_candidates(self, request: NdChangeRequest, user: User) -> list[dict]:
        accessible_docs = await self._accessible_documents(user)
        if not accessible_docs:
            return []
        code_matches = {item.upper() for item in DOCUMENT_CODE_RE.findall(request.change_text)}
        metadata_code = (request.metadata_ or {}).get("assumed_document_code")
        if metadata_code:
            code_matches.add(str(metadata_code).upper())
        query_terms = self._terms(request.change_text)

        candidates: list[dict] = []
        for document, version_id, kb_name in accessible_docs:
            metadata = document.metadata_ or document.doc_metadata or {}
            code = str(metadata.get("code") or metadata.get("document_code") or "")
            title = document.title or ""
            score = 0.0
            reasons: list[str] = []
            if request.selected_document_id and document.id == request.selected_document_id:
                score += 0.85
                reasons.append("Документ указан пользователем")
            if code and code.upper() in code_matches:
                score += 0.95
                reasons.append(f"Точное совпадение обозначения {code}")
            if any(code_match in title.upper() for code_match in code_matches):
                score += 0.85
                reasons.append("Обозначение найдено в названии документа")
            title_hits = sum(1 for term in query_terms if term in title.lower())
            if title_hits:
                score += min(0.20 + title_hits * 0.05, 0.45)
                reasons.append("Совпадение терминов с названием")
            fragments = await self._matching_fragments(document.id, query_terms)
            if fragments:
                score += min(0.18 + len(fragments) * 0.06, 0.45)
                reasons.append("Найдены релевантные фрагменты в базе знаний")
            if score > 0:
                candidates.append(
                    {
                        "document": document,
                        "version_id": version_id,
                        "score": min(score, 0.99),
                        "reason": "; ".join(reasons) + f"; база знаний: {kb_name}",
                        "fragments": fragments,
                    }
                )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:5]

    async def _accessible_documents(self, user: User) -> list[tuple[Document, uuid.UUID | None, str]]:
        access = KnowledgeBaseAccessService(self.db)
        result = await self.db.execute(
            select(KnowledgeBase, KnowledgeBaseSource, Document)
            .join(KnowledgeBaseSource, KnowledgeBaseSource.knowledge_base_id == KnowledgeBase.id)
            .join(Document, Document.id == KnowledgeBaseSource.document_id)
            .where(KnowledgeBase.status == KnowledgeBaseStatus.READY)
        )
        items: list[tuple[Document, uuid.UUID | None, str]] = []
        seen: set[uuid.UUID] = set()
        for kb, source, document in result.all():
            if document.id in seen:
                continue
            effective = await access.can_access_knowledge_base(
                user=user,
                knowledge_base=kb,
                required_access=KnowledgeBaseAccessType.SEARCH,
                allow_non_ready_for_admin=False,
            )
            if effective.allowed:
                seen.add(document.id)
                items.append((document, source.document_version_id, kb.name))
        return items

    async def _ensure_document_access(self, document_id: uuid.UUID, user: User) -> None:
        accessible_ids = {document.id for document, _, _ in await self._accessible_documents(user)}
        if user.is_superuser:
            return
        if document_id not in accessible_ids:
            raise NdChangeServiceError("Документ недоступен пользователю")

    async def _matching_fragments(self, document_id: uuid.UUID, terms: list[str]) -> list[dict]:
        if not terms:
            return []
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(50)
        )
        fragments = []
        for chunk in result.scalars().all():
            text = (chunk.text or chunk.content or "")
            lowered = text.lower()
            score = sum(1 for term in terms if term in lowered)
            if score:
                fragments.append(
                    {
                        "chunk_id": str(chunk.id),
                        "score": score,
                        "page_number": chunk.page_number,
                        "section_title": chunk.section_title,
                        "text": text[:700],
                    }
                )
        fragments.sort(key=lambda item: item["score"], reverse=True)
        return fragments[:3]

    async def _find_related_documents(self, document: Document) -> list[dict]:
        metadata = document.metadata_ or document.doc_metadata or {}
        code = metadata.get("code") or metadata.get("document_code")
        if not code:
            return []
        result = await self.db.execute(
            select(DocumentChunk).where(DocumentChunk.content.ilike(f"%{code}%")).limit(10)
        )
        related = []
        seen = {document.id}
        for chunk in result.scalars().all():
            if not chunk.document_id or chunk.document_id in seen:
                continue
            related_doc = await self.db.get(Document, chunk.document_id)
            if not related_doc:
                continue
            seen.add(related_doc.id)
            related.append(
                {
                    "document_id": str(related_doc.id),
                    "code": (related_doc.metadata_ or {}).get("code"),
                    "title": related_doc.title,
                    "impact_type": "normative_reference",
                    "recommendation": "Проверить необходимость актуализации ссылки",
                }
            )
        return related

    def _validate_required_fields(self, request: NdChangeRequest) -> None:
        if not request.reason or not request.change_text:
            raise NdChangeServiceError("Заполните причину и текст изменения")

    async def _next_number(self) -> str:
        year = datetime.now().year
        count = await self.db.scalar(select(func.count(NdChangeRequest.id)))
        return f"ND-{year}-{int(count or 0) + 1:04d}"

    async def _load_candidates(self, request_id: uuid.UUID) -> list[NdChangeCandidateDocument]:
        result = await self.db.execute(
            select(NdChangeCandidateDocument)
            .where(NdChangeCandidateDocument.change_request_id == request_id)
            .order_by(NdChangeCandidateDocument.rank.asc())
        )
        return list(result.scalars().all())

    async def _load_locations(self, request_id: uuid.UUID) -> list[NdChangeTargetLocation]:
        result = await self.db.execute(
            select(NdChangeTargetLocation)
            .where(NdChangeTargetLocation.change_request_id == request_id)
            .order_by(NdChangeTargetLocation.confidence.desc().nullslast())
        )
        return list(result.scalars().all())

    async def _select_location(self, request_id: uuid.UUID, location_id: uuid.UUID | None) -> NdChangeTargetLocation | None:
        locations = await self._load_locations(request_id)
        if location_id:
            return next((item for item in locations if item.id == location_id), None)
        high = [item for item in locations if (item.confidence or 0) >= 0.8]
        return high[0] if len(high) == 1 else (locations[0] if len(locations) == 1 else None)

    async def _clear_candidates(self, request_id: uuid.UUID) -> None:
        for item in await self._load_candidates(request_id):
            await self.db.delete(item)
        await self.db.flush()

    async def _clear_locations(self, request_id: uuid.UUID) -> None:
        for item in await self._load_locations(request_id):
            await self.db.delete(item)
        await self.db.flush()

    def _add_artifact(self, request: NdChangeRequest, document: Document, artifact, *, file_type: str) -> NdChangeDraftFile:
        draft = NdChangeDraftFile(
            change_request_id=request.id,
            document_id=document.id,
            source_document_version_id=request.selected_document_version_id,
            draft_bucket=artifact.bucket,
            draft_object_name=artifact.object_name,
            original_filename=document.original_filename,
            generated_filename=artifact.filename,
            file_type=file_type,
            status=NdChangeDraftFileStatus.WARNING_SOURCE_NOT_EDITABLE if artifact.warnings else NdChangeDraftFileStatus.GENERATED,
            file_size=artifact.size,
        )
        self.db.add(draft)
        return draft

    def _add_operation(self, request: NdChangeRequest, location: NdChangeTargetLocation | None, edit_result) -> NdChangeOperation:
        diff = [item.__dict__ for item in edit_result.diff]
        first = edit_result.diff[0] if edit_result.diff else None
        operation = NdChangeOperation(
            change_request_id=request.id,
            target_location_id=location.id if location else None,
            old_text=first.old_text if first else None,
            new_text=first.new_text if first else request.change_text,
            diff=diff,
            status=NdChangeOperationStatus.APPLIED_TO_DRAFT,
            requires_manual_review=bool(edit_result.warnings),
        )
        self.db.add(operation)
        return operation

    def _terms(self, text: str) -> list[str]:
        words = re.findall(r"[а-яА-Яa-zA-Z0-9]{5,}", (text or "").lower())
        stop_words = {"изменение", "изложить", "следующей", "редакции", "добавить", "заменить", "исключить"}
        return [word for word in dict.fromkeys(words) if word not in stop_words][:14]


def document_code(document: Document | None) -> str | None:
    if document is None:
        return None
    metadata = document.metadata_ or document.doc_metadata or {}
    if metadata.get("code") or metadata.get("document_code"):
        return str(metadata.get("code") or metadata.get("document_code"))
    filename = document.original_filename or document.title
    match = DOCUMENT_CODE_RE.search(filename or "")
    return match.group(0) if match else None


def generated_download_name(file: NdChangeDraftFile) -> str:
    return PurePath(file.generated_filename).name
