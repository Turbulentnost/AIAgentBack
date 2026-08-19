from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WechatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """TEMP: история сообщений WeChat-утилиты. Удалить вместе с кнопками test/история."""

    __tablename__ = "wechat_messages"
    __table_args__ = (UniqueConstraint("external_id", name="uq_wechat_messages_external_id"),)

    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    message_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(255), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(255))
    group_name: Mapped[str | None] = mapped_column(String(512), index=True)
    group_id: Mapped[str | None] = mapped_column(String(255), index=True)
    message_type: Mapped[str | None] = mapped_column(String(64), index=True)
    has_file: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    file_name: Mapped[str | None] = mapped_column(String(512))
    file_mime: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_kind: Mapped[str | None] = mapped_column(String(32), index=True)
    file_storage: Mapped[str | None] = mapped_column(String(32))
    file_storage_path: Mapped[str | None] = mapped_column(String(1024))
    file_error: Mapped[str | None] = mapped_column(String(512))
