from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentKpiRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Запись одного запуска ролевого агента ОМТО — реальный источник KPI.

    Каждый вызов агента (через оркестратор / эндпоинт запуска) фиксирует здесь
    измеримые сигналы. KPI-дашборд агрегирует эти строки, поэтому значения на
    дашборде отражают фактическую работу агента, а не демонстрационные числа.
    """

    __tablename__ = "agent_kpi_runs"

    agent_slug: Mapped[str] = mapped_column(String(128), index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), default="default", index=True)
    task_type: Mapped[str | None] = mapped_column(String(128))

    # Итог графа и платформенный статус роли.
    status: Mapped[str] = mapped_column(String(64), index=True)
    role_status: Mapped[str] = mapped_column(String(64), index=True)
    data_confidence: Mapped[str] = mapped_column(String(16), default="medium")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Измеримые сигналы для KPI.
    total_findings: Mapped[int] = mapped_column(Integer, default=0)
    critical_findings: Mapped[int] = mapped_column(Integer, default=0)
    findings_with_source: Mapped[int] = mapped_column(Integer, default=0)
    source_references: Mapped[int] = mapped_column(Integer, default=0)
    coverage_percent: Mapped[float | None] = mapped_column(Float)
    verdict_emitted: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    # HITL: идентификатор потока LangGraph (= correlation_id) и данные точки паузы.
    thread_id: Mapped[str | None] = mapped_column(String(128), index=True)
    hitl_pending: Mapped[dict | None] = mapped_column(JSONB)

    # Кто инициировал запуск (пользователь платформы), для аудита.
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column()

    summary: Mapped[str | None] = mapped_column(Text)
    output_data: Mapped[dict | None] = mapped_column(JSONB)


__all__ = ["AgentKpiRun"]
