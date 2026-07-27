from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EskdUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eskd_users"

    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(64), default="ESKD_OTK", index=True)
    department: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
