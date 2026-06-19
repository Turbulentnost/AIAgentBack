from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.tools.Outlook.send_meeting_invite import parse_start


def test_parse_start_accepts_iso_with_timezone_offset() -> None:
    parsed = parse_start("2026-06-22T08:00:00+03:00", "Europe/Moscow")

    assert parsed.tzinfo == ZoneInfo("Europe/Moscow")
    assert parsed.hour == 8
    assert parsed.date().isoformat() == "2026-06-22"


def test_parse_start_accepts_legacy_formats() -> None:
    assert parse_start("2026-06-22 08:00", "Europe/Moscow").hour == 8
    assert parse_start("2026-06-22T08:00", "Europe/Moscow").hour == 8
    assert parse_start("22.06.2026 08:00", "Europe/Moscow").hour == 8


def test_parse_start_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Не удалось разобрать дату"):
        parse_start("not-a-date", "Europe/Moscow")
