from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NdAcknowledgementStatus


class NdAcknowledgementAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Назначение ознакомления с НД (ТЗ п. 5.9)."""

    __tablename__ = "nd_acknowledgement_assignments"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        index=True,
    )
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nd_change_requests.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[NdAcknowledgementStatus] = mapped_column(
        default=NdAcknowledgementStatus.PENDING,
        index=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_code: Mapped[str | None] = mapped_column(String(128), index=True)
    document_name: Mapped[str | None] = mapped_column(String(512))
    note: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship()


from app.models.user import User  # noqa: E402
