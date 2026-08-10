from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OnecStockBalance(UUIDPrimaryKeyMixin, Base):
    """Остатки товаров на складах из 1С OData (AccumulationRegister_ТоварыНаСкладах/Balance)."""

    __tablename__ = "onec_stock_balances"
    __table_args__ = (
        UniqueConstraint(
            "nomenclature_key",
            "characteristic_key",
            "purpose_key",
            "warehouse_key",
            "room_key",
            "series_key",
            "batch_key",
            name="uq_onec_stock_balance_dims",
        ),
        Index("ix_onec_stock_balances_code", "code"),
        Index("ix_onec_stock_balances_warehouse", "warehouse"),
    )

    code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    warehouse: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    in_stock: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    to_ship: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    available: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    nomenclature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    characteristic_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    purpose_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    warehouse_key: Mapped[str] = mapped_column(String(64), nullable=False)
    room_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    series_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    batch_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OnecStockSyncRun(UUIDPrimaryKeyMixin, Base):
    """Метаданные выгрузки остатков из 1С."""

    __tablename__ = "onec_stock_sync_runs"

    source: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    positive_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    negative_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
