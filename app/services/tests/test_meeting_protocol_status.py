from __future__ import annotations

import pytest

from app.models.enums import MeetingRegistryStage
from app.services.meeting_protocol_status import (
    normalize_protocol_status,
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
        ("На исполнении", MeetingRegistryStage.PROTOCOL_CONDUCTED),
        ("Закрыт", MeetingRegistryStage.MEETING_COMPLETED),
        ("Подготовлен", None),
        (None, None),
    ],
)
def test_stage_for_protocol_status(status: str | None, expected: MeetingRegistryStage | None) -> None:
    assert stage_for_protocol_status(status) == expected
