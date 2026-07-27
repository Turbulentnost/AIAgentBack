from __future__ import annotations

from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import (
    clear_service_account_cache,
    connect_account,
    parse_start,
)


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


def _outlook_config() -> OutlookConfig:
    return OutlookConfig(
        email="Postagent@turbo-don.ru",
        password="secret",
        mailbox="postagent@turbo-don.ru",
        server="mail.turbo-don.ru",
        web_app_url="",
        timezone="Europe/Moscow",
        smtp_host="mail.turbo-don.ru",
        smtp_port=587,
        smtp_use_tls=True,
        smtp_from="Postagent@turbo-don.ru",
        company_calendar="calendar@turbo-don.ru",
    )


def test_connect_account_reuses_cached_service_account() -> None:
    clear_service_account_cache()
    config = _outlook_config()
    account = MagicMock(name="ews-account")

    with patch("app.tools.Outlook.send_meeting_invite.Account", return_value=account):
        first = connect_account(config, verify_mailbox=False)
        second = connect_account(config, verify_mailbox=False)

    assert first is second
    clear_service_account_cache()
