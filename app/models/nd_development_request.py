from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NdDevelopmentRequestKind, NdDevelopmentRequestStatus, QmsDocumentKind


class NdDevelopmentRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Заявка на разработку нового НД или выпуск новой версии (ТЗ п. 5.3–5.4)."""

    __tablename__ = "nd_development_requests"

    number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[NdDevelopmentRequestKind] = mapped_column(index=True)
    status: Mapped[NdDevelopmentRequestStatus] = mapped_column(
        default=NdDevelopmentRequestStatus.DRAFT,
        index=True,
    )
    document_kind: Mapped[QmsDocumentKind | None] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(512))
    justification: Mapped[str] = mapped_column(Text)
    process_description: Mapped[str | None] = mapped_column(Text)
    process_owner: Mapped[str | None] = mapped_column(String(255))
    developer_department: Mapped[str | None] = mapped_column(String(255))
    interested_departments: Mapped[list | None] = mapped_column(JSONB)
    similar_documents: Mapped[list | None] = mapped_column(JSONB)
    scope: Mapped[str | None] = mapped_column(Text)
    target_effective_date: Mapped[date | None] = mapped_column(Date)
    needs_process_diagram: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_introduction_order: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_implementation_plan: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledgement_targets: Mapped[list | None] = mapped_column(JSONB)
    base_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
    )
    base_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        index=True,
    )
    version_reason: Mapped[str | None] = mapped_column(Text)
    duplicate_check_result: Mapped[dict | None] = mapped_column(JSONB)
    package_completeness: Mapped[dict | None] = mapped_column(JSONB)
    initiator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    initiator: Mapped["User | None"] = relationship()


from app.models.user import User  # noqa: E402
