from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Position(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_positions_canonical_key"),
        UniqueConstraint("slug", name="uq_positions_slug"),
    )

    name: Mapped[str] = mapped_column(String(512), index=True)
    normalized_name: Mapped[str] = mapped_column(String(512), index=True)
    canonical_key: Mapped[str] = mapped_column(String(512), index=True)
    slug: Mapped[str] = mapped_column(String(256), index=True)
    departments_count: Mapped[int] = mapped_column(Integer, default=0)
    assignments_count: Mapped[int] = mapped_column(Integer, default=0)
    source_system: Mapped[str | None] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    department_links: Mapped[list["DepartmentPosition"]] = relationship(
        back_populates="position",
        cascade="all, delete-orphan",
    )


class DepartmentPosition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "department_positions"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "position_id",
            name="uq_department_positions_department_id_position_id",
        ),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        index=True,
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"),
        index=True,
    )

    department: Mapped["Department"] = relationship(back_populates="position_links")
    position: Mapped[Position] = relationship(back_populates="department_links")
