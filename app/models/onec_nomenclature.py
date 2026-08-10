from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OnecNomenclature(UUIDPrimaryKeyMixin, Base):
    """Справочник номенклатуры 1С (атрибуты для агента Авион)."""

    __tablename__ = "onec_nomenclature"
    __table_args__ = (
        UniqueConstraint("ref_key", name="uq_onec_nomenclature_ref_key"),
        Index("ix_onec_nomenclature_code", "code"),
        Index("ix_onec_nomenclature_name", "name"),
    )

    ref_key: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    country_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    country_of_origin: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    unit_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    unit: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
