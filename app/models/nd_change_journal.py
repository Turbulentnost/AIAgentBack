from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NdChangeJournalEventType, NdChangeJournalSource


class NdChangeJournalEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_change_journal_entries"

    event_type: Mapped[NdChangeJournalEventType] = mapped_column(index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(128), index=True)
    resource_id: Mapped[str] = mapped_column(String(128), index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nd_control_templates.id", ondelete="SET NULL"),
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
    )
    document_code: Mapped[str | None] = mapped_column(String(128), index=True)
    document_name: Mapped[str | None] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[NdChangeJournalSource] = mapped_column(
        default=NdChangeJournalSource.SYSTEM,
        index=True,
    )

    actor: Mapped["User | None"] = relationship()


from app.models.user import User  # noqa: E402
