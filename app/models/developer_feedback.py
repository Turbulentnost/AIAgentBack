from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeveloperFeedbackThread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_feedback_threads"
    __table_args__ = (
        UniqueConstraint(
            "agent_slug",
            "participant_user_id",
            name="uq_developer_feedback_threads_agent_participant",
        ),
    )

    agent_slug: Mapped[str] = mapped_column(String(128), default="document_analysis_agent", index=True)
    participant_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    participant_name: Mapped[str] = mapped_column(String(255), index=True)
    participant_email: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    participant_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    developer_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    participant = relationship("User", foreign_keys=[participant_user_id])
    messages: Mapped[list["DeveloperFeedbackMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="DeveloperFeedbackMessage.created_at",
    )


class DeveloperFeedbackMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_feedback_messages"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("developer_feedback_threads.id", ondelete="CASCADE"),
        index=True,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    author_role: Mapped[str] = mapped_column(String(32), index=True)
    author_name: Mapped[str] = mapped_column(String(255))
    author_email: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str] = mapped_column(Text)

    thread: Mapped[DeveloperFeedbackThread] = relationship(back_populates="messages")
    author = relationship("User", foreign_keys=[author_user_id])
    attachments: Mapped[list["DeveloperFeedbackAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class DeveloperFeedbackAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_feedback_attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("developer_feedback_messages.id", ondelete="CASCADE"),
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(128), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)

    message: Mapped[DeveloperFeedbackMessage] = relationship(back_populates="attachments")
