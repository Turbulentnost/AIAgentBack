from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_card import QmsDocumentCard
from app.models.enums import NdDevelopmentRequestKind, NdDevelopmentRequestStatus, QmsDocumentKind
from app.models.nd_development_request import NdDevelopmentRequest
from app.models.user import User
from app.schemas.nd_development_request import NdDevelopmentRequestCreate


class NdDevelopmentRequestServiceError(Exception):
    pass


_REQUIRED_PACKAGE: dict[QmsDocumentKind, list[str]] = {
    QmsDocumentKind.STO: [
        "project_word",
        "process_diagram",
        "introduction_order",
        "implementation_plan",
        "acknowledgement_targets",
    ],
    QmsDocumentKind.PROVISION: [
        "project_word",
        "process_diagram",
        "introduction_order",
        "implementation_plan",
        "acknowledgement_targets",
    ],
}


class NdDevelopmentRequestService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        payload: NdDevelopmentRequestCreate,
        *,
        current_user: User,
    ) -> NdDevelopmentRequest:
        if payload.kind == NdDevelopmentRequestKind.NEW_VERSION and payload.base_document_id is None:
            raise NdDevelopmentRequestServiceError("Для новой версии нужен base_document_id")
        item = NdDevelopmentRequest(
            number=await self._next_number(payload.kind),
            kind=payload.kind,
            document_kind=payload.document_kind,
            title=payload.title.strip(),
            justification=payload.justification.strip(),
            process_description=payload.process_description,
            process_owner=payload.process_owner,
            developer_department=payload.developer_department,
            interested_departments=payload.interested_departments,
            similar_documents=payload.similar_documents,
            scope=payload.scope,
            target_effective_date=payload.target_effective_date,
            needs_process_diagram=payload.needs_process_diagram,
            needs_introduction_order=payload.needs_introduction_order,
            needs_implementation_plan=payload.needs_implementation_plan,
            acknowledgement_targets=payload.acknowledgement_targets,
            base_document_id=payload.base_document_id,
            base_document_version_id=payload.base_document_version_id,
            version_reason=payload.version_reason,
            initiator_user_id=current_user.id,
        )
        if item.document_kind is None:
            item.document_kind = self._suggest_document_kind(item.justification, item.process_description)
        self.db.add(item)
        await self.db.flush()
        return item

    async def list(
        self,
        *,
        kind: NdDevelopmentRequestKind | None = None,
        status: NdDevelopmentRequestStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[NdDevelopmentRequest], int]:
        query = select(NdDevelopmentRequest)
        if kind is not None:
            query = query.where(NdDevelopmentRequest.kind == kind)
        if status is not None:
            query = query.where(NdDevelopmentRequest.status == status)
        total = await self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        result = await self.db.execute(
            query.order_by(NdDevelopmentRequest.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(result.scalars().all()), int(total)

    async def get_or_raise(self, request_id: uuid.UUID) -> NdDevelopmentRequest:
        item = await self.db.get(NdDevelopmentRequest, request_id)
        if item is None:
            raise NdDevelopmentRequestServiceError("Заявка не найдена")
        return item

    async def submit(self, request_id: uuid.UUID) -> NdDevelopmentRequest:
        item = await self.get_or_raise(request_id)
        if item.status != NdDevelopmentRequestStatus.DRAFT:
            raise NdDevelopmentRequestServiceError("Заявку можно отправить только из статуса draft")
        item.status = NdDevelopmentRequestStatus.SUBMITTED
        item.submitted_at = datetime.now(UTC)
        await self.db.flush()
        return item

    async def run_duplicate_check(self, request_id: uuid.UUID) -> dict:
        item = await self.get_or_raise(request_id)
        terms = [item.title, *(item.similar_documents or [])]
        query_text = " ".join(term for term in terms if term).strip()
        matches: list[dict] = []
        if query_text:
            pattern = f"%{query_text[:120]}%"
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
                matches.append(
                    {
                        "document_code": card.document_code,
                        "document_name": card.document_name,
                        "status": card.status.value,
                        "score": 0.7,
                    }
                )
        recommendation = "develop_new"
        if matches:
            recommendation = "change_existing" if item.kind == NdDevelopmentRequestKind.NEW_DOCUMENT else "new_version"
        payload = {"matches": matches, "recommendation": recommendation}
        item.duplicate_check_result = payload
        item.status = NdDevelopmentRequestStatus.DUPLICATE_CHECK
        await self.db.flush()
        return payload

    async def check_package(self, request_id: uuid.UUID) -> dict:
        item = await self.get_or_raise(request_id)
        required = list(_REQUIRED_PACKAGE.get(item.document_kind or QmsDocumentKind.INSTRUCTION, ["project_word"]))
        if item.needs_process_diagram and "process_diagram" not in required:
            required.append("process_diagram")
        if item.needs_introduction_order and "introduction_order" not in required:
            required.append("introduction_order")
        if item.needs_implementation_plan and "implementation_plan" not in required:
            required.append("implementation_plan")
        if item.acknowledgement_targets:
            required.append("acknowledgement_targets")
        metadata = item.metadata_ or {}
        attachments = set(metadata.get("attachments") or [])
        missing = [name for name in required if name not in attachments]
        warnings: list[str] = []
        if item.kind == NdDevelopmentRequestKind.NEW_VERSION and not item.base_document_id:
            warnings.append("missing_base_document")
        result = {
            "is_complete": not missing,
            "missing_items": missing,
            "warnings": warnings,
        }
        item.package_completeness = result
        item.status = NdDevelopmentRequestStatus.PACKAGE_REVIEW
        await self.db.flush()
        return result

    def _suggest_document_kind(self, justification: str, process_description: str | None) -> QmsDocumentKind:
        text = f"{justification} {process_description or ''}".lower()
        if any(token in text for token in ("политик", "стратег")):
            return QmsDocumentKind.POLICY
        if any(token in text for token in ("сто", "стандарт организации", "контрол")):
            return QmsDocumentKind.STO
        if any(token in text for token in ("положен", "организац")):
            return QmsDocumentKind.PROVISION
        if any(token in text for token in ("регламент", "последователь")):
            return QmsDocumentKind.REGULATION
        return QmsDocumentKind.INSTRUCTION

    async def _next_number(self, kind: NdDevelopmentRequestKind) -> str:
        prefix = "NDN" if kind == NdDevelopmentRequestKind.NEW_DOCUMENT else "NDV"
        count = await self.db.scalar(
            select(func.count()).select_from(NdDevelopmentRequest).where(NdDevelopmentRequest.kind == kind)
        )
        return f"{prefix}-{int(count or 0) + 1:06d}"
