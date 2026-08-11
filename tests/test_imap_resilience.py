"""Тесты IMAP retry / transient detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.config import Settings
from agent_pochta.imap.resilience import (
    call_with_imap_retries,
    is_transient_imap_error,
)


def test_is_transient_imap_error_detects_server_unavailable():
    assert is_transient_imap_error(RuntimeError("select failed: Server Unavailable. 15"))
    assert is_transient_imap_error(TimeoutError("timed out"))
    assert not is_transient_imap_error(ValueError("bad credentials"))


def test_call_with_imap_retries_recovers_after_transient():
    settings = Settings(
        IMAP_OPERATION_RETRIES=3,
        IMAP_OPERATION_RETRY_DELAY_SEC=0.01,
    )
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("select failed: Server Unavailable. 15")
        return "ok"

    with patch("agent_pochta.imap.resilience.time.sleep") as sleep:
        assert call_with_imap_retries(flaky, settings=settings, what="test") == "ok"
        assert calls["n"] == 3
        assert sleep.call_count == 2


def test_call_with_imap_retries_raises_non_transient():
    settings = Settings(IMAP_OPERATION_RETRIES=3, IMAP_OPERATION_RETRY_DELAY_SEC=0.01)

    def boom() -> None:
        raise ValueError("authentication failed")

    with pytest.raises(ValueError, match="authentication"):
        call_with_imap_retries(boom, settings=settings, what="test")


def test_open_session_retries_select_unavailable():
    from agent_pochta.imap.client import ImapCredentials, ImapMailboxClient

    settings = Settings(
        IMAP_OPERATION_RETRIES=3,
        IMAP_OPERATION_RETRY_DELAY_SEC=0.01,
        IMAP_MAX_CONCURRENT=2,
    )
    client_wrapper = ImapMailboxClient(
        "info@turbo-don.ru",
        ImapCredentials(username="u", password="p"),
        settings=settings,
    )

    good = MagicMock()
    bad = MagicMock()
    bad.select_folder.side_effect = RuntimeError("select failed: Server Unavailable. 15")
    connects = {"n": 0}

    def connect_once(*, timeout_sec=None):
        connects["n"] += 1
        if connects["n"] == 1:
            return bad
        return good

    with (
        patch.object(client_wrapper, "_connect_once", side_effect=connect_once),
        patch("agent_pochta.imap.resilience.time.sleep"),
    ):
        with client_wrapper._open_session(folder="INBOX", readonly=True) as session:
            assert session is good

    assert connects["n"] == 2
    good.select_folder.assert_called_once_with("INBOX", readonly=True)
    bad.logout.assert_called()
