from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OnecResourceSpec(UUIDPrimaryKeyMixin, Base):
    """Ресурсная спецификация 1С (Catalog_РесурсныеСпецификации)."""

    __tablename__ = "onec_resource_specs"
    __table_args__ = (
        UniqueConstraint("ref_key", name="uq_onec_resource_specs_ref_key"),
        Index("ix_onec_resource_specs_code", "code"),
        Index("ix_onec_resource_specs_status", "status"),
        Index("ix_onec_resource_specs_main_product", "main_product_key"),
    )

    ref_key: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    process_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    is_folder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deletion_mark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    main_product_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    main_product_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    main_product_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    main_product_qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    materials_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outputs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OnecResourceSpecMaterial(UUIDPrimaryKeyMixin, Base):
    """Строка ТЧ МатериалыИУслуги ресурсной спецификации."""

    __tablename__ = "onec_resource_spec_materials"
    __table_args__ = (
        UniqueConstraint(
            "spec_ref_key",
            "line_number",
            name="uq_onec_resource_spec_materials_line",
        ),
        Index("ix_onec_resource_spec_materials_spec", "spec_ref_key"),
        Index("ix_onec_resource_spec_materials_nom", "nomenclature_key"),
    )

    spec_ref_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("onec_resource_specs.ref_key", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    nomenclature_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    characteristic_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    packaging_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    unit: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    produced_in_process: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    alternative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class OnecResourceSpecOutput(UUIDPrimaryKeyMixin, Base):
    """Строка ТЧ ВыходныеИзделия ресурсной спецификации."""

    __tablename__ = "onec_resource_spec_outputs"
    __table_args__ = (
        UniqueConstraint(
            "spec_ref_key",
            "line_number",
            name="uq_onec_resource_spec_outputs_line",
        ),
        Index("ix_onec_resource_spec_outputs_spec", "spec_ref_key"),
        Index("ix_onec_resource_spec_outputs_nom", "nomenclature_key"),
    )

    spec_ref_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("onec_resource_specs.ref_key", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    nomenclature_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nomenclature_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    characteristic_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    packaging_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class OnecResourceSpecSyncRun(UUIDPrimaryKeyMixin, Base):
    """Метаданные выгрузки ресурсных спецификаций из 1С."""

    __tablename__ = "onec_resource_spec_sync_runs"

    source: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    specs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    materials_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outputs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_specs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_materials: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_outputs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
