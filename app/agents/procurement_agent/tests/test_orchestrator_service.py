from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.procurement_agent.source_discovery import normalize_source_document
from app.db.base import Base
from app.models.enums import ProcurementSourceType
from app.models.procurement import ProcurementCase, ProcurementCaseEvent, ProcurementCasePosition
from app.services.procurement_orchestrator_service import ProcurementOrchestratorService


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    ProcurementCase.__table__,
                    ProcurementCasePosition.__table__,
                    ProcurementCaseEvent.__table__,
                ],
            )
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _document(
    ref: str,
    version: str,
    quantity: int = 2,
    *,
    action: str = "КОбеспечению",
    cancelled: bool = False,
):
    return normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": ref,
            "DataVersion": version,
            "Number": "НП-1",
            "Date": "2026-07-16T10:00:00",
            "DeletionMark": False,
            "Posted": True,
            "Статус": "КВыполнению",
            "Автор_Key": "author",
            "Подразделение_Key": "dept",
            "Склад_Key": "wh",
            "Организация_Key": "org",
            "Товары": [
                {
                    "LineNumber": 1,
                    "КодСтроки": 1,
                    "Номенклатура_Key": "item-1",
                    "Количество": quantity,
                    "Отменено": cancelled,
                    "ВариантОбеспечения": action,
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_upsert_creates_then_skips_unchanged(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    first = await service._upsert_case_from_document(_document(ref, "v1"))
    assert first in {"created", "enqueued"}
    second = await service._upsert_case_from_document(_document(ref, "v1"))
    assert second == "skipped"
    cases = (await db_session.execute(select(ProcurementCase))).scalars().all()
    assert len(cases) == 1
    positions = (await db_session.execute(select(ProcurementCasePosition))).scalars().all()
    assert len(positions) == 1


@pytest.mark.asyncio
async def test_upsert_updates_on_version_change(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1", quantity=1))
    result = await service._upsert_case_from_document(_document(ref, "v2", quantity=5))
    assert result in {"updated", "enqueued"}
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    assert case.source_data_version == "v2"
    position = (await db_session.execute(select(ProcurementCasePosition))).scalar_one()
    assert str(position.quantity) in {"5", "5.000000"}


@pytest.mark.asyncio
async def test_upsert_closes_case_when_line_is_not_for_supply(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))
    result = await service._upsert_case_from_document(
        _document(ref, "v2", action="КПроизводству")
    )
    assert result == "updated"
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    assert case.status == "closed"
