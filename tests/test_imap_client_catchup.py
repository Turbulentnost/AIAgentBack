"""Тесты лимитов catch-up IMAP (без реального сервера)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

from agent_pochta.config import Settings
from agent_pochta.imap.client import ImapCredentials, ImapMailboxClient


def _client(*, max_uids: int = 50) -> ImapMailboxClient:
    settings = Settings(IMAP_CATCHUP_MAX_UIDS=max_uids, IMAP_FETCH_BATCH_SIZE=20)
    return ImapMailboxClient(
        "info@turbo-don.ru",
        ImapCredentials(username="info@turbo-don.ru", password="secret"),
        settings=settings,
    )


def _patch_session(client: ImapMailboxClient, mock_imap: MagicMock):
    @contextmanager
    def _session(**_kwargs):
        yield mock_imap

    return patch.object(client, "_open_session", side_effect=lambda **kw: _session())


def test_fetch_since_caps_scan_and_fetch_when_no_known_ids():
    client = _client(max_uids=3)
    mock_imap = MagicMock()
    mock_imap.search.return_value = [1, 2, 3, 4, 5]
    mock_imap.fetch.side_effect = [
        {
            5: {b"RFC822": b"raw-5"},
            4: {b"RFC822": b"raw-4"},
            3: {b"RFC822": b"raw-3"},
        },
    ]

    with _patch_session(client, mock_imap):
        with patch("agent_pochta.imap.client.parse_raw_email") as parse_raw:
            parse_raw.side_effect = lambda raw, mailbox: MagicMock(message_id=f"<{raw.decode()}>")
            emails = client.fetch_since(date(2026, 7, 20), exclude_message_id_bases=set())

    assert mock_imap.fetch.call_count == 1
    fetched_uids = mock_imap.fetch.call_args[0][0]
    assert fetched_uids == [5, 4, 3]
    assert len(emails) == 3


def test_fetch_since_stops_after_max_unknown_targets():
    client = _client(max_uids=2)
    mock_imap = MagicMock()
    mock_imap.search.return_value = [10, 11, 12, 13]
    mock_imap.fetch.side_effect = [
        {
            13: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <known>\r\n"},
            12: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <new-1>\r\n"},
            11: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <new-2>\r\n"},
            10: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <new-3>\r\n"},
        },
        {
            12: {b"RFC822": b"raw-12"},
            11: {b"RFC822": b"raw-11"},
        },
    ]

    with _patch_session(client, mock_imap):
        with patch("agent_pochta.imap.client.parse_raw_email") as parse_raw:
            parse_raw.side_effect = lambda raw, mailbox: MagicMock(message_id=f"<{raw.decode()}>")
            emails = client.fetch_since(date(2026, 7, 20), exclude_message_id_bases={"<known>"})

    body_fetch_uids = mock_imap.fetch.call_args_list[-1][0][0]
    assert body_fetch_uids == [12, 11]
    assert len(emails) == 2
