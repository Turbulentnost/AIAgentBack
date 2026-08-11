"""Tests for IMAP subject search."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from agent_pochta.imap.client import ImapCredentials, ImapMailboxClient


def test_find_uid_by_subject_with_sender() -> None:
    client = MagicMock()
    client.search.side_effect = [[101], [101]]

    imap = ImapMailboxClient(
        "sales@turbo-don.ru",
        ImapCredentials(username="sales@turbo-don.ru", password="secret"),
    )
    uid = imap._find_uid_by_subject(
        client,
        "Запрос на поставку",
        folder="INBOX",
        sender_email="client@example.com",
    )
    assert uid == 101
    client.search.assert_called()


@patch("agent_pochta.imap.client.parse_raw_email")
def test_fetch_by_subject_returns_parsed_email(mock_parse) -> None:
    mock_client = MagicMock()
    mock_client.fetch.return_value = {55: {b"RFC822": b"raw-bytes"}}

    imap = ImapMailboxClient(
        "sales@turbo-don.ru",
        ImapCredentials(username="sales@turbo-don.ru", password="secret"),
    )

    @contextmanager
    def _session(**_kwargs):
        yield mock_client

    with patch.object(imap, "_open_session", side_effect=lambda **kw: _session()):
        with patch.object(imap, "_find_uid_by_subject", return_value=55):
            email_obj = MagicMock()
            mock_parse.return_value = email_obj
            result = imap.fetch_by_subject("Тема письма", sender_email="a@b.ru")
    assert result is email_obj
    mock_parse.assert_called_once()
