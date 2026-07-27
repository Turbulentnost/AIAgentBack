from __future__ import annotations

import uuid
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import ScheduledMeetingWeekday
from app.models.app_notification import AppNotification
from app.schemas.app_notification import (
    TurboProjectRgParticipantProposal,
    TurboProjectRgSeriesProposal,
    TurboProjectRgWeeklySlotProposal,
)
from app.services.scheduled_meeting_person import ResolvedPerson, ScheduledMeetingPersonError
from app.services.turbo_project_series_sync_service import (
    RG_CATEGORY_NAME,
    TurboProjectSeriesSyncService,
    WeeklySlotChoice,
    clear_daily_candidates_memory_cache,
    turbo_project_entity_key,
)


@pytest.fixture(autouse=True)
def _clear_daily_cache():
    clear_daily_candidates_memory_cache()
    yield
    clear_daily_candidates_memory_cache()


def _person(fio: str, email: str | None = None) -> ResolvedPerson:
    slug = fio.split()[0].lower()
    return ResolvedPerson(
        user_id=uuid.uuid4(),
        fio=fio,
        email=email or f"{slug}@turbo-don.ru",
        position_id=uuid.uuid4(),
        position_name="Менеджер",
    )


def _listing(*projects: dict) -> dict:
    return {
        "total_projects": len(projects),
        "projects_with_1c_count": len(projects),
        "matched_count": len(projects),
        "projects": list(projects),
    }


def _project_details(
    *,
    file_id: int = 153,
    project_name: str = "Demo",
    status_proekta: str = "ВРаботе",
) -> dict:
    return {
        "file_id": file_id,
        "project_name": project_name,
        "uploaded_at": "2026-07-24T05:31:18",
        "dates": {
            "start_date": "2026-02-20T08:00:00",
            "finish_date": "2026-12-04T09:00:00",
        },
        "data_1c": {
            "nomer_proekta": "P-001",
            "one_c_ref_key": "ref-1",
            "status_proekta": status_proekta,
        },
        "resources": [],
    }


def _working_group(*, file_id: int = 153, project_name: str = "Demo") -> dict:
    details = _project_details(file_id=file_id, project_name=project_name)
    return {
        "file_id": file_id,
        "project_name": project_name,
        "one_c_ref_key": "ref-1",
        "members": [
            {"fio": "Елеева Анна Васильевна", "role": "Руководитель проекта", "source": "1c"},
            {"fio": "Донцова Анна Егоровна", "role": "Куратор", "source": "1c"},
            {"fio": "Сулейманов Руслан Андреевич", "role": "Ресурс проекта", "source": "msp"},
        ],
        "project": {
            "file_id": file_id,
            "project_name": project_name,
            "dates": details["dates"],
            "data_1c": details["data_1c"],
        },
    }


@pytest.mark.asyncio
async def test_discover_skips_below_watermark_and_includes_min_file_id() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    service = TurboProjectSeriesSyncService(db)
    recipient = SimpleNamespace(id=uuid.uuid4())

    with (
        patch.object(TurboProjectSeriesSyncService, "_local_today", return_value=date(2026, 7, 24)),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_setex",
            AsyncMock(),
        ) as setex,
        patch(
            "app.services.turbo_project_series_sync_service.list_turbo_projects",
            return_value=_listing(
                {
                    "file_id": 432,
                    "project_name": "Old",
                    "uploaded_at": "2026-07-24T10:00:00",
                },
                {
                    "file_id": 433,
                    "project_name": "Тестирование создания РГ для совещаний",
                    "uploaded_at": "2026-07-20T05:31:18",
                },
                {
                    "file_id": 434,
                    "project_name": "Новее",
                    "uploaded_at": "2026-07-24T08:00:00",
                },
            ),
        ) as list_projects,
        patch(
            "app.services.turbo_project_series_sync_service.list_meeting_agent_users",
            AsyncMock(return_value=[recipient]),
        ),
        patch.object(service, "_find_existing_series", AsyncMock(return_value=None)),
        patch.object(service, "_entity_key_exists", AsyncMock(return_value=False)),
    ):
        result = await service.discover_and_notify(min_file_id=433, uploaded_within_days=0)

    assert result.cache_hit is False
    assert result.skipped_below_watermark == 1
    assert result.skipped_stale_upload == 0
    assert result.candidates == 2
    assert result.notified == 2
    list_projects.assert_called_once()
    assert list_projects.call_args.kwargs.get("only_with_1c") is False
    setex.assert_awaited_once()
    assert db.add.call_count == 2
    created_keys = {call.args[0].entity_key for call in db.add.call_args_list}
    assert created_keys == {
        turbo_project_entity_key(433),
        turbo_project_entity_key(434),
    }


@pytest.mark.asyncio
async def test_discover_uses_daily_cache_without_turbo_project() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    service = TurboProjectSeriesSyncService(db)
    recipient = SimpleNamespace(id=uuid.uuid4())

    with (
        patch.object(TurboProjectSeriesSyncService, "_local_today", return_value=date(2026, 7, 24)),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_setex",
            AsyncMock(),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.list_turbo_projects",
            return_value=_listing(
                {
                    "file_id": 433,
                    "project_name": "Cached",
                    "uploaded_at": "2026-07-24T08:00:00",
                }
            ),
        ) as list_projects,
        patch(
            "app.services.turbo_project_series_sync_service.list_meeting_agent_users",
            AsyncMock(return_value=[recipient]),
        ),
        patch.object(service, "_find_existing_series", AsyncMock(return_value=None)),
        patch.object(service, "_entity_key_exists", AsyncMock(return_value=False)),
    ):
        first = await service.discover_and_notify(min_file_id=433, uploaded_within_days=0)
        second = await service.discover_and_notify(min_file_id=433, uploaded_within_days=0)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.candidates == 1
    assert list_projects.call_count == 1


@pytest.mark.asyncio
async def test_discover_dedupes_entity_key() -> None:
    db = AsyncMock()
    service = TurboProjectSeriesSyncService(db)

    with (
        patch.object(TurboProjectSeriesSyncService, "_local_today", return_value=date(2026, 7, 24)),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.meeting_redis_setex",
            AsyncMock(),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.list_turbo_projects",
            return_value=_listing(
                {
                    "file_id": 433,
                    "project_name": "Demo",
                    "uploaded_at": "2026-07-24T08:00:00",
                }
            ),
        ) as list_projects,
        patch(
            "app.services.turbo_project_series_sync_service.list_meeting_agent_users",
            AsyncMock(return_value=[SimpleNamespace(id=uuid.uuid4())]),
        ),
        patch.object(service, "_find_existing_series", AsyncMock(return_value=None)),
        patch.object(service, "_entity_key_exists", AsyncMock(return_value=True)),
    ):
        result = await service.discover_and_notify(min_file_id=433, uploaded_within_days=0)

    assert result.skipped_already_notified == 1
    list_projects.assert_called_once()


@pytest.mark.asyncio
async def test_build_series_proposal_uses_outlook_and_slot() -> None:
    db = AsyncMock()
    service = TurboProjectSeriesSyncService(db)
    manager = _person("Елеева Анна Васильевна")
    responsible = _person("Донцова Анна Егоровна")
    participant = _person("Сулейманов Руслан Андреевич")

    async def resolve_side_effect(_db, fio: str):
        mapping = {
            manager.fio: manager,
            responsible.fio: responsible,
            participant.fio: participant,
        }
        person = mapping.get(fio)
        if person is None:
            raise ScheduledMeetingPersonError("missing", status_code=404)
        return person

    with (
        patch(
            "app.services.turbo_project_series_sync_service.get_turbo_project_working_group",
            return_value=_working_group(),
        ),
        patch(
            "app.services.turbo_project_series_sync_service.resolve_person_by_fio",
            side_effect=resolve_side_effect,
        ),
        patch.object(
            service,
            "_pick_weekly_slot",
            AsyncMock(
                return_value=WeeklySlotChoice(
                    weekday=ScheduledMeetingWeekday.WEDNESDAY,
                    time_local=time(11, 0),
                    duration_minutes=60,
                    slot_start="2026-07-29T11:00:00+03:00",
                    coverage_ratio=0.8,
                    fallback=False,
                )
            ),
        ),
    ):
        proposal = await service.build_series_proposal(153)

    assert proposal.file_id == 153
    assert proposal.series_start_date == date(2026, 2, 20)
    assert proposal.series_end_date == date(2026, 12, 4)
    assert proposal.weekly_slot.weekday == ScheduledMeetingWeekday.WEDNESDAY
    assert proposal.manager.fio == manager.fio
    assert len(proposal.participants) == 1


@pytest.mark.asyncio
async def test_create_series_from_proposal() -> None:
    db = AsyncMock()
    service = TurboProjectSeriesSyncService(db)
    category = SimpleNamespace(id=uuid.uuid4(), name=RG_CATEGORY_NAME)
    manager = TurboProjectRgParticipantProposal(
        user_id=uuid.uuid4(),
        fio="РП",
        email="rp@turbo-don.ru",
    )
    responsible = TurboProjectRgParticipantProposal(
        user_id=uuid.uuid4(),
        fio="Куратор",
        email="kur@turbo-don.ru",
    )
    proposal = TurboProjectRgSeriesProposal(
        file_id=153,
        project_name="Demo",
        one_c_ref_key="ref-1",
        nomer_proekta="P-001",
        status_proekta="ВРаботе",
        title="РГ: Demo",
        meeting_category_name=RG_CATEGORY_NAME,
        series_start_date=date(2026, 2, 20),
        series_end_date=date(2026, 12, 4),
        recurrence_label="еженедельно, среда 11:00",
        weekly_slot=TurboProjectRgWeeklySlotProposal(
            weekday=ScheduledMeetingWeekday.WEDNESDAY,
            time_local=time(11, 0),
            duration_minutes=60,
            fallback=False,
        ),
        manager=manager,
        responsible=responsible,
        participants=[],
    )
    created = SimpleNamespace(id=uuid.uuid4(), title="РГ: Demo")

    with (
        patch.object(service, "_find_existing_series", AsyncMock(return_value=None)),
        patch.object(service, "_resolve_rg_category", AsyncMock(return_value=category)),
        patch(
            "app.services.turbo_project_series_sync_service.ScheduledMeetingService"
        ) as scheduled_cls,
    ):
        scheduled_cls.return_value.create = AsyncMock(return_value=created)
        result = await service.create_series_from_proposal(proposal)

    assert result is created
    payload = scheduled_cls.return_value.create.await_args.args[0]
    assert payload.status.value == "created"
    assert payload.payload["turbo_project_file_id"] == 153


def test_is_in_work_status() -> None:
    assert TurboProjectSeriesSyncService._is_in_work_status("ВРаботе")
    assert not TurboProjectSeriesSyncService._is_in_work_status("Завершен")


@pytest.mark.asyncio
async def test_pick_weekly_slot_maps_quorum_candidate() -> None:
    db = AsyncMock()
    service = TurboProjectSeriesSyncService(db)
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 29, 11, 0, tzinfo=tz)

    with patch(
        "app.tools.Outlook.slot_search.api.dispatch_find_quorum_meeting_slots",
        return_value={
            "candidates": [
                {"slot_start": slot_start.isoformat(), "coverage_ratio": 0.75}
            ]
        },
    ):
        choice = await service._pick_weekly_slot(
            attendee_emails=["a@turbo-don.ru", "b@turbo-don.ru"],
            required_emails=["a@turbo-don.ru"],
            series_start=date(2026, 7, 24),
            series_end=date(2026, 12, 4),
            duration_minutes=60,
        )

    assert choice.fallback is False
    assert choice.weekday == ScheduledMeetingWeekday.WEDNESDAY
    assert choice.time_local == time(11, 0)
