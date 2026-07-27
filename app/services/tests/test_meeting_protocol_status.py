from __future__ import annotations

import pytest

from app.models.enums import MeetingRegistryStage
from app.services.meeting_protocol_status import (
    normalize_protocol_status,
    protocol_status_is_terminal,
    registry_cancel_allowed,
    should_fetch_protocol_status_from_onec,
    stage_for_protocol_status,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("На исполнении", "На исполнении"),
        ("  Закрыт  ", "Закрыт"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_protocol_status(raw: object, expected: str | None) -> None:
    assert normalize_protocol_status(raw) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("На исполнении", MeetingRegistryStage.MEETING_COMPLETED),
        ("НаИсполнении", MeetingRegistryStage.MEETING_COMPLETED),
        ("Закрыт", MeetingRegistryStage.MEETING_COMPLETED),
        ("Закрыто", MeetingRegistryStage.MEETING_COMPLETED),
        ("Подготовлен", None),
        (None, None),
    ],
)
def test_stage_for_protocol_status(status: str | None, expected: MeetingRegistryStage | None) -> None:
    assert stage_for_protocol_status(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("На исполнении", True),
        ("НаИсполнении", True),
        ("Закрыт", True),
        ("Закрыто", True),
        ("Подготовлен", False),
        (None, False),
    ],
)
def test_protocol_status_is_terminal(status: str | None, expected: bool) -> None:
    assert protocol_status_is_terminal(status) == expected


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (MeetingRegistryStage.PROTOCOL_CREATED, True),
        (MeetingRegistryStage.PROTOCOL_CONDUCTED, True),
        (MeetingRegistryStage.MEETING_COMPLETED, False),
        (MeetingRegistryStage.CANCELLED, False),
    ],
)
def test_should_fetch_protocol_status_from_onec(
    stage: MeetingRegistryStage,
    expected: bool,
) -> None:
    assert should_fetch_protocol_status_from_onec(stage) is expected


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (MeetingRegistryStage.INVITATIONS_SENT, True),
        (MeetingRegistryStage.PROTOCOL_CREATED, True),
        (MeetingRegistryStage.PROTOCOL_CONDUCTED, False),
        (MeetingRegistryStage.MEETING_COMPLETED, False),
        (MeetingRegistryStage.CANCELLED, False),
    ],
)
def test_registry_cancel_allowed(stage: MeetingRegistryStage, expected: bool) -> None:
    assert registry_cancel_allowed(stage) is expected
