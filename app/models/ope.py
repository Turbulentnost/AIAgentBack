from __future__ import annotations

import uuid
from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import FindingSeverity, OpeDecision

class OpeCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ope_cards"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    program: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="started")
    decision: Mapped[OpeDecision | None] = mapped_column()
    checklists: Mapped[list["OpeChecklist"]] = relationship(back_populates="card", cascade="all, delete-orphan")
    issues: Mapped[list["OpeIssue"]] = relationship(back_populates="card", cascade="all, delete-orphan")
    reports: Mapped[list["OpeReport"]] = relationship(back_populates="card", cascade="all, delete-orphan")

class OpeChecklist(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ope_checklists"
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ope_cards.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    items: Mapped[list | None] = mapped_column(JSONB)
    card: Mapped[OpeCard] = relationship(back_populates="checklists")

class OpeIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ope_issues"
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ope_cards.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[FindingSeverity] = mapped_column(default=FindingSeverity.MEDIUM)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    card: Mapped[OpeCard] = relationship(back_populates="issues")

class OpeReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ope_reports"
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ope_cards.id", ondelete="CASCADE"), index=True)
    period: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    card: Mapped[OpeCard] = relationship(back_populates="reports")
