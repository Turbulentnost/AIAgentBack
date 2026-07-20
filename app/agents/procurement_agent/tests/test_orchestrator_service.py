from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.agents.procurement_agent.source_discovery import normalize_source_document
from app.agents.procurement_role_agents.config import (
    PRODUCTION_DISPATCHER_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    SOURCE_AGENT_MAP,
)
from app.db.base import Base
from app.models.enums import ProcurementSourceType, TaskStatus
from app.models.procurement import (
    ProcurementCase,
    ProcurementCaseEvent,
    ProcurementCasePosition,
    ProcurementSourceSyncState,
)
from app.models.task import Task
from app.services.procurement_orchestrator_service import (
    ProcurementOrchestratorService,
    _source_date_cutoff,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):  # noqa: ANN001
        # Avoid needing users/tasks FK targets in unit tests.
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    ProcurementCase.__table__,
                    ProcurementCasePosition.__table__,
                    ProcurementCaseEvent.__table__,
                    ProcurementSourceSyncState.__table__,
                    Task.__table__,
                ],
            )
        )
    # Match production AsyncSessionLocal: autoflush=False.
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
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
    source_date: str = "2026-07-16T10:00:00",
    source_type: ProcurementSourceType = ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
):
    return normalize_source_document(
        source_type=source_type,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": ref,
            "DataVersion": version,
            "Number": "НП-1",
            "Date": source_date,
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
    assert cases[0].source_synced_at is not None
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


def test_source_date_cutoff_uses_two_calendar_months():
    assert _source_date_cutoff(datetime(2026, 4, 30, 12, tzinfo=UTC)) == datetime(
        2026, 2, 28, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_upsert_skips_old_document_without_existing_case(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)

    result = await service._upsert_case_from_document(
        _document(ref, "v1", source_date="2026-01-01T10:00:00")
    )

    assert result == "skipped"
    assert (await db_session.execute(select(ProcurementCase))).scalars().all() == []


@pytest.mark.asyncio
async def test_upsert_keeps_tracking_active_case_after_it_ages(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))

    result = await service._upsert_case_from_document(
        _document(ref, "v2", quantity=5, source_date="2026-01-01T10:00:00")
    )

    assert result == "updated"
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    assert case.status == "new"
    assert case.source_date.replace(tzinfo=UTC) == datetime(2026, 1, 1, 7, tzinfo=UTC)


@pytest.mark.asyncio
async def test_upsert_does_not_reactivate_old_archived_case(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))
    await service._upsert_case_from_document(_document(ref, "v2", action="КПроизводству"))

    result = await service._upsert_case_from_document(
        _document(ref, "v3", source_date="2026-01-01T10:00:00")
    )

    assert result == "skipped"
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    assert case.status == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", list(ProcurementSourceType))
async def test_role_agent_is_routed_by_source_type(
    db_session: AsyncSession,
    source_type: ProcurementSourceType,
):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=True)

    result = await service._upsert_case_from_document(
        _document(ref, "v1", source_type=source_type)
    )

    assert result == "enqueued"
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    task = (await db_session.execute(select(Task))).scalar_one()
    expected_agent = SOURCE_AGENT_MAP[source_type.value]
    assert case.current_agent_id == expected_agent
    assert case.assigned_agents == [expected_agent]
    assert task.task_metadata["agent_slug"] == expected_agent
    assert task.task_type == "procurement_role_agent"


@pytest.mark.asyncio
async def test_role_agent_wait_blocks_duplicates_and_completed_resume_releases(
    db_session: AsyncSession,
):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=True)
    await service._upsert_case_from_document(_document(ref, "v1"))
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    task = (await db_session.execute(select(Task))).scalar_one()

    result = await service.execute_case_task(case.id, task.id)
    assert result["role_status"] == "waiting_external"
    assert case.status == "agent_waiting"
    assert task.status is TaskStatus.WAITING_EXTERNAL
    assert await service._enqueue_role_agent(case) is False

    waiting_human = await service.resume_case_agent(
        case.id,
        {
            "role_status": "waiting_human",
            "summary": "Нужно решение инициатора",
            "wait_reason": "Нужно решение инициатора",
            "output_data": {},
        },
    )
    assert waiting_human is not None
    assert task.status is TaskStatus.WAITING_HUMAN
    assert case.current_task_id == task.id

    completed = await service.resume_case_agent(
        case.id,
        {
            "role_status": "completed",
            "summary": "Данные собраны",
            "output_data": {"decision": "continue"},
        },
    )
    assert completed is not None
    assert task.status is TaskStatus.COMPLETED
    assert case.current_task_id is None
    assert case.current_agent_id is None
    assert case.case_metadata["role_agent_output"] == {"decision": "continue"}
    assert await service._enqueue_role_agent(case) is False


@pytest.mark.asyncio
async def test_engineer_dispatch_claims_only_five_cases(db_session: AsyncSession):
    service = ProcurementOrchestratorService(db_session, enqueue_case=True)
    for index in range(7):
        await service._upsert_case_from_document(
            _document(
                str(uuid.uuid4()),
                f"v-{index}",
                source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
            )
        )

    claimed = await service.claim_engineer_dispatches(limit=5)
    tasks = (await db_session.execute(select(Task))).scalars().all()

    assert claimed == 5
    assert len(service.pending_dispatches) == 5
    assert sum(
        bool((task.task_metadata or {}).get("dispatch_claimed"))
        for task in tasks
    ) == 5


@pytest.mark.asyncio
async def test_engineer_purchase_confirmation_hands_off_and_archives_workspace(
    db_session: AsyncSession,
):
    service = ProcurementOrchestratorService(db_session, enqueue_case=True)
    await service._upsert_case_from_document(
        _document(
            str(uuid.uuid4()),
            "v1",
            source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
        )
    )
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    task = (await db_session.execute(select(Task))).scalar_one()
    output = {
        "decision_kind": "purchase_confirmation",
        "positions": [{"net_requirement": "12"}],
    }
    task.status = TaskStatus.WAITING_HUMAN
    task.final_result = {
        "agent_id": PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
        "role_status": "waiting_human",
        "output_data": output,
    }
    case.case_metadata = {
        **(case.case_metadata or {}),
        "engineer_decision_kind": "purchase_confirmation",
        "production_preparation_engineer_output": output,
    }
    await db_session.flush()

    result = await service.confirm_engineer_purchase(case.id, user_id="engineer-1")

    assert result is not None
    assert task.status is TaskStatus.COMPLETED
    assert case.current_task_id is None
    assert case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
    assert case.control_point == "chief_dispatcher"
    assert case.case_metadata["engineer_archived_bucket"] == "attention"
    archive = await service.list_dashboard(
        view="archive",
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
        engineer_workspace=True,
    )
    assert archive["total_cases"] == 1


@pytest.mark.asyncio
async def test_engineer_critical_acknowledgement_keeps_waiting(
    db_session: AsyncSession,
):
    service = ProcurementOrchestratorService(db_session, enqueue_case=True)
    await service._upsert_case_from_document(
        _document(
            str(uuid.uuid4()),
            "v1",
            source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
        )
    )
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    task = (await db_session.execute(select(Task))).scalar_one()
    task.status = TaskStatus.WAITING_HUMAN
    case.case_metadata = {
        **(case.case_metadata or {}),
        "engineer_decision_kind": "critical_acknowledgement",
    }
    await db_session.flush()

    result = await service.acknowledge_engineer_critical(
        case.id,
        user_id="engineer-1",
    )

    assert result is not None
    assert task.status is TaskStatus.WAITING_HUMAN
    assert case.current_task_id == task.id
    assert case.case_metadata["engineer_critical_acknowledged_by"] == "engineer-1"
    active = await service.list_dashboard(
        view="active",
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
        engineer_workspace=True,
    )
    assert active["total_cases"] == 1


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
    assert case.closed_reason == "inactive_supply_action"
    assert case.closed_at is not None


@pytest.mark.asyncio
async def test_upsert_closes_cancelled_document(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))
    cancelled = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": ref,
            "DataVersion": "v-cancelled",
            "Number": "НП-1",
            "Date": "2026-07-16T10:00:00",
            "DeletionMark": False,
            "Отменен": True,
            "Статус": "КВыполнению",
            "Товары": [
                {
                    "LineNumber": 1,
                    "КодСтроки": 1,
                    "Номенклатура_Key": "item-1",
                    "Количество": 2,
                    "Отменено": False,
                    "ВариантОбеспечения": "КОбеспечению",
                }
            ],
        },
    )
    result = await service._upsert_case_from_document(cancelled)
    assert result == "updated"
    case = (await db_session.execute(select(ProcurementCase))).scalar_one()
    assert case.status == "closed"
    assert case.closed_reason == "cancelled"


@pytest.mark.asyncio
async def test_upsert_reactivates_same_case_from_archive(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))
    await service._upsert_case_from_document(_document(ref, "v2", action="КПроизводству"))
    result = await service._upsert_case_from_document(_document(ref, "v3", quantity=4))
    assert result in {"updated", "enqueued"}
    cases = (await db_session.execute(select(ProcurementCase))).scalars().all()
    assert len(cases) == 1
    case = cases[0]
    assert case.status == "new"
    assert case.closed_at is None
    assert case.closed_reason is None
    assert case.reactivated_at is not None
    events = (
        await db_session.execute(
            select(ProcurementCaseEvent).where(
                ProcurementCaseEvent.event_type == "case_reactivated_from_source"
            )
        )
    ).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_list_dashboard_archive_view(db_session: AsyncSession):
    ref = str(uuid.uuid4())
    service = ProcurementOrchestratorService(db_session, enqueue_case=False)
    await service._upsert_case_from_document(_document(ref, "v1"))
    await service._upsert_case_from_document(_document(ref, "v2", action="КПроизводству"))
    archive = await service.list_dashboard(view="archive")
    processing = await service.list_dashboard(view="processing")
    assert archive["counts"]["archive"] == 1
    assert archive["total_cases"] == 1
    assert processing["total_cases"] == 0
    detail = await service.get_case(
        uuid.UUID(archive["groups"][0]["cases"][0]["id"])
    )
    assert detail is not None
    assert detail["closed_reason"] == "inactive_supply_action"
    assert detail["route_stages"]
    assert detail["timeline"]
    assert detail["current_state"]["source_active"] is False

