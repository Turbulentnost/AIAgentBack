from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OnecProductionPlanHeader(UUIDPrimaryKeyMixin, Base):
    """Актуальный проведённый документ 1С Document_ПланПроизводства."""

    __tablename__ = "onec_production_plan_headers"
    __table_args__ = (
        UniqueConstraint("ref_key", name="uq_onec_production_plan_headers_ref_key"),
        Index("ix_onec_production_plan_headers_date", "plan_date"),
    )

    ref_key: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    plan_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deletion_mark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_entity: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OnecProductionPlanItem(UUIDPrimaryKeyMixin, Base):
    """Строка табличной части актуального плана производства."""

    __tablename__ = "onec_production_plan_items"
    __table_args__ = (
        UniqueConstraint(
            "plan_ref_key",
            "line_number",
            "nomenclature_key",
            "product_date",
            name="uq_onec_prod_plan_line",
        ),
        Index("ix_onec_production_plan_items_plan", "plan_ref_key"),
        Index("ix_onec_production_plan_items_nom", "nomenclature_key"),
        Index("ix_onec_production_plan_items_month", "month_key"),
    )

    plan_ref_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("onec_production_plan_headers.ref_key", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    product_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    month_key: Mapped[str] = mapped_column(String(7), default="", nullable=False)
    nomenclature_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    specification_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    specification_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    department: Mapped[str] = mapped_column(Text, default="", nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, default="", nullable=False)


class OnecProductionPlanSyncRun(UUIDPrimaryKeyMixin, Base):
    """Метаданные выгрузки плана производства из 1С."""

    __tablename__ = "onec_production_plan_sync_runs"

    source: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    plan_ref_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    plan_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    plan_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
