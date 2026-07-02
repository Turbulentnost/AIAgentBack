from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.schemas.meeting import MeetingMemoApproveRequest
from app.services.meeting_offline_cache import (
    build_offline_approve_result,
    is_offline_cache_detail,
)
from app.services.meeting_service import MeetingService
from app.tools.onec.service_memo_shared import APPROVED_STATUS, UNAPPROVED_STATUS


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)


def test_is_offline_cache_detail_by_history() -> None:
    detail = {
        "history": [{"message": "СЗ загружена из Excel (offline cache)"}],
    }
    assert is_offline_cache_detail(detail) is True


def test_is_offline_cache_detail_by_source() -> None:
    assert is_offline_cache_detail({"cache_source": "excel"}) is True
    assert is_offline_cache_detail({"status": UNAPPROVED_STATUS}) is False


def test_build_offline_approve_result_changes_status() -> None:
    result = build_offline_approve_result(
        {"number": "000010703", "status": UNAPPROVED_STATUS, "sto_ready": True},
        ref_key="abc",
        approver_fio="Иванов И. И.",
    )
    assert result["changed"] is True
    assert result["status"] == APPROVED_STATUS
    assert "offline cache" in result["message"]


@pytest.mark.asyncio
async def test_approve_memo_offline_cache_skips_onec(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()
    service._apply_memo_status_to_cache = AsyncMock()

    offline_detail = {
        "number": "000010703",
        "status": UNAPPROVED_STATUS,
        "sto_ready": True,
        "history": [{"message": "СЗ загружена из Excel (offline cache)"}],
    }

    cache_service = AsyncMock()
    cache_service.read_cached = AsyncMock(
        return_value={"payload": offline_detail, "fetched_at": None}
    )
    cache_service.patch_status = AsyncMock(return_value=True)

    with patch("app.services.meeting_service.MeetingMemoCacheService", return_value=cache_service):
        with patch("app.services.meeting_service.approve_service_memo") as approve_onec:
            result = await service.approve_memo(
                "65050fe0-0e1a-53fc-88f4-dc0772a0c021",
                MeetingMemoApproveRequest(),
                current_user=user,
            )

    approve_onec.assert_not_called()
    cache_service.patch_status.assert_awaited_once()
    service._apply_memo_status_to_cache.assert_awaited_once_with(
        "65050fe0-0e1a-53fc-88f4-dc0772a0c021",
        APPROVED_STATUS,
    )
    assert result.changed is True
    assert result.status == APPROVED_STATUS


@pytest.mark.asyncio
async def test_approve_memo_without_offline_cache_uses_onec(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()
    service._apply_memo_status_to_cache = AsyncMock()
    service._schedule_memo_cache_refresh = lambda _ref: None

    cache_service = AsyncMock()
    cache_service.read_cached = AsyncMock(return_value=None)

    with patch("app.services.meeting_service.MeetingMemoCacheService", return_value=cache_service):
        with patch(
            "app.services.meeting_service.approve_service_memo",
            return_value={
                "ref_key": "real-ref",
                "number": "000010430",
                "status": APPROVED_STATUS,
                "changed": True,
                "sto_ready": True,
                "sto_issues": [],
            },
        ) as approve_onec:
            result = await service.approve_memo(
                "real-ref",
                MeetingMemoApproveRequest(),
                current_user=user,
            )

    approve_onec.assert_called_once()
    assert result.changed is True


@pytest.mark.asyncio
async def test_approve_agent_slot_syncs_offline_cache_status(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()
    service._apply_memo_status_to_cache = AsyncMock()
    service._sync_offline_cache_after_invite = AsyncMock()

    offline_detail = {
        "number": "000010703",
        "status": UNAPPROVED_STATUS,
        "history": [{"message": "СЗ загружена из Excel (offline cache)"}],
        "application": {"initiator": {"full_name": "A"}},
    }

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
        AsyncMock(return_value=(offline_detail, None, True)),
    ):
        with patch(
            "app.services.meeting_service.dispatch_meeting_invite",
            return_value={"status": "sent", "attendees": ["a@turbo-don.ru"]},
        ):
            with patch(
                "app.services.meeting_service.MeetingRegistryService.upsert_from_invite",
                AsyncMock(),
            ) as upsert_registry:
                from app.schemas.meeting import MeetingAgentSlotApproveRequest, MeetingAttendeeRead

                result = await service.approve_agent_slot(
                    "65050fe0-0e1a-53fc-88f4-dc0772a0c021",
                    MeetingAgentSlotApproveRequest(
                        slot_start="2026-07-08T12:00:00",
                        slot_end="2026-07-08T13:00:00",
                        subject="Совещание",
                        attendees=[
                            MeetingAttendeeRead(
                                fio="A",
                                email="a@turbo-don.ru",
                                role="participant",
                                role_label="Участник",
                                found=True,
                            )
                        ],
                    ),
                    current_user=user,
                )

    assert result.sent is True
    upsert_registry.assert_awaited_once()
    service._sync_offline_cache_after_invite.assert_awaited_once()
