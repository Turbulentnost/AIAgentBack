"""Тесты фильтров списка писем и пагинации API."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from agent_pochta.api.app import app
from agent_pochta.db.message_filters import (
    INFO_MAILBOX,
    INFO_RECIPIENT_Q,
    TEST_II_MAILBOX,
    compute_is_info_recipient,
    info_to_test_ii_sql_filter,
    is_info_to_test_ii_routing,
    is_only_info_to,
    email_eligible_for_erp,
    is_dialog_message,
    is_turbo_don_routing_row,
    matches_info_recipient_only,
    matches_recipient_q,
    only_info_to_sql_filter,
    recipient_display_value,
    recipient_q_sql_filter,
    sanitize_json_text_for_pg,
    msk_day_end_exclusive_utc,
    msk_day_start_utc,
    parse_optional_date,
)
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.api.list_table_fields import operator_review_state
from agent_pochta.db.repository import EmailRepository
from agent_pochta.db.session import get_session_factory

TEST_II_MAILBOX = "test_ii@turbo-don.ru"
FROM_II_MAILBOX = "from_ii@turbo-don.ru"
PEREADRES_MAILBOX = "pereadres@turbo-don.ru"
TENDER_MAILBOX = "tender@turbo-don.ru"





def test_parse_optional_date():

    assert parse_optional_date(None) is None

    assert parse_optional_date("  ") is None

    assert parse_optional_date("2026-07-02") == date(2026, 7, 2)





def test_msk_day_bounds():

    start = msk_day_start_utc(date(2026, 7, 2))

    end = msk_day_end_exclusive_utc(date(2026, 7, 2))

    assert start == datetime(2026, 7, 1, 21, 0, 0)

    assert end == datetime(2026, 7, 2, 21, 0, 0)





def _email_row(*, status: str = "done") -> EmailMessageRow:

    received_at = datetime.now(timezone.utc).replace(tzinfo=None)

    return EmailMessageRow(

        id=uuid.uuid4(),

        message_id="<list@example>",

        received_at=received_at,

        mailbox="info@turbo-don.ru",

        sender_email="vendor@example.com",

        sender_name="Vendor",

        subject="Акт сверки",

        status=status,

    )





@contextmanager

def _mock_repo(
    *,
    rows: list[EmailMessageRow],
    total: int,
    by_status: dict[str, int] | None = None,
    operator_review_counts: dict[str, int] | None = None,
):

    repo = MagicMock()

    repo.list_messages.return_value = rows
    repo.list_messages_paginated.return_value = (rows, total)
    repo.count_messages.return_value = total
    status_counts = by_status or {row.status: 1 for row in rows}
    review_counts = operator_review_counts or {
        "all": total,
        "verified": 0,
        "corrected": 0,
        "pending": total,
    }
    repo.count_by_status.return_value = status_counts
    repo.count_operator_review_states.return_value = review_counts
    repo.message_stats_bundle.return_value = (status_counts, review_counts)

    session = MagicMock()

    session.__enter__.return_value = session

    session.__exit__.return_value = False



    with patch("agent_pochta.api.app.get_session_factory", return_value=lambda: session):

        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):

            yield repo





def test_list_email_messages_returns_page():

    row = _email_row()

    client = TestClient(app)

    with _mock_repo(rows=[row], total=1):

        response = client.get("/api/v1/email-messages", params={"limit": 50, "offset": 0})

    assert response.status_code == 200

    payload = response.json()

    assert payload["total"] == 1

    assert payload["limit"] == 50

    assert payload["offset"] == 0

    assert len(payload["items"]) == 1

    assert payload["items"][0]["id"] == str(row.id)

    assert "body_text" not in payload["items"][0]





def test_list_email_messages_passes_processing_status_filter():
    row = _email_row(status="processing")
    client = TestClient(app)
    with _mock_repo(rows=[row], total=1) as mock_repo:
        response = client.get("/api/v1/email-messages", params={"status": "processing"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["status"] == "processing"
    mock_repo.list_messages_paginated.assert_called_once()
    assert mock_repo.list_messages_paginated.call_args.kwargs["status"] == "processing"





def test_email_messages_stats_endpoint():

    client = TestClient(app)

    with _mock_repo(
        rows=[],
        total=3,
        by_status={"done": 2, "spam": 1},
        operator_review_counts={"all": 3, "verified": 1, "corrected": 1, "pending": 1},
    ):
        with patch(
            "agent_pochta.api.app.collect_operator_approvals_dashboard",
            return_value=(
                {"saved": 4, "changed": 1, "rate": 0.8},
                {"saved": 2, "changed": 3, "rate": 0.4},
            ),
        ):
            response = client.get("/api/v1/email-messages/stats")

    assert response.status_code == 200

    payload = response.json()

    assert payload["total"] == 3

    assert payload["by_status"]["done"] == 2

    assert payload["by_status"]["spam"] == 1

    assert payload["operator_review_counts"] == {
        "all": 3,
        "verified": 1,
        "corrected": 1,
        "pending": 1,
    }

    assert payload["operator_approvals"] == {"saved": 4, "changed": 1, "rate": 0.8}
    assert payload["operator_approvals_last_actions"] == {"saved": 2, "changed": 3, "rate": 0.4}
    assert payload["operator_approvals_last_24h"] == {"saved": 2, "changed": 3, "rate": 0.4}
    assert payload["operator_accuracy_window"] == 200


def test_email_messages_stats_passes_status_filter():
    client = TestClient(app)
    with _mock_repo(rows=[], total=2, by_status={"done": 2}) as mock_repo:
        with patch(
            "agent_pochta.api.app.collect_operator_approvals_dashboard",
            return_value=(
                {"saved": 0, "changed": 0, "rate": None},
                {"saved": 0, "changed": 0, "rate": None},
            ),
        ):
            response = client.get("/api/v1/email-messages/stats", params={"status": "done"})
    assert response.status_code == 200
    mock_repo.message_stats_bundle.assert_called_once()
    assert mock_repo.message_stats_bundle.call_args.kwargs["status"] == "done"





def test_list_email_messages_rejects_invalid_date_range():

    client = TestClient(app)

    response = client.get(

        "/api/v1/email-messages",

        params={"date_from": "2026-07-10", "date_to": "2026-07-01"},

    )

    assert response.status_code == 400





def test_is_info_to_test_ii_accepts_forwarded_empty_to():
    payload = {"to": [], "cc": [], "routing_recipient": TEST_II_MAILBOX}
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is True


def test_is_info_to_test_ii_accepts_info_in_to():
    payload = {
        "to": [INFO_MAILBOX],
        "cc": [],
        "routing_recipient": TEST_II_MAILBOX,
    }
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is True


def test_is_info_to_test_ii_accepts_info_in_cc():
    payload = {
        "to": [],
        "cc": [INFO_MAILBOX],
        "routing_recipient": TEST_II_MAILBOX,
    }
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is True


def test_is_info_to_test_ii_rejects_direct_test_ii_to():
    payload = {"to": [TEST_II_MAILBOX], "cc": [], "routing_recipient": TEST_II_MAILBOX}
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is False


def test_is_info_to_test_ii_rejects_info_only_routing():
    payload = {"to": [INFO_MAILBOX], "cc": [], "routing_recipient": INFO_MAILBOX}
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is False


def test_is_info_to_test_ii_rejects_pereadres_routing():
    payload = {
        "to": [INFO_MAILBOX],
        "cc": [],
        "routing_recipient": PEREADRES_MAILBOX,
    }
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is False


def test_is_info_to_test_ii_rejects_tender_routing():
    payload = {
        "to": [TENDER_MAILBOX],
        "cc": [],
        "routing_recipient": TENDER_MAILBOX,
    }
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is False


def test_is_turbo_don_routing_row_accepts_turbo_recipient():
    assert is_turbo_don_routing_row(
        mailbox=INFO_MAILBOX,
        payload={"routing_recipient": INFO_MAILBOX},
    )
    assert is_turbo_don_routing_row(mailbox=INFO_MAILBOX, payload={})


def test_is_turbo_don_routing_row_rejects_external_recipient():
    assert not is_turbo_don_routing_row(
        mailbox=INFO_MAILBOX,
        payload={"routing_recipient": "partner@example.ru"},
    )


def test_split_routing_recipients_skips_non_turbo_addresses():
    from datetime import datetime, timezone

    from agent_pochta.routing.recipients import split_routing_recipients
    from agent_pochta.schemas import EmailMessage

    email = EmailMessage(
        message_id="<dl@test>",
        mailbox=INFO_MAILBOX,
        sender_email="sender@client.ru",
        subject="Рассылка",
        body_text="Текст",
        received_at=datetime.now(timezone.utc),
        to=["partner@example.ru", INFO_MAILBOX, "user@gmail.com"],
    )
    assert split_routing_recipients(email) == [INFO_MAILBOX]


def test_is_info_to_test_ii_rejects_empty_to_wrong_mailbox():
    payload = {"to": [], "cc": [], "routing_recipient": TEST_II_MAILBOX}
    assert is_info_to_test_ii_routing(mailbox=INFO_MAILBOX, payload=payload) is False


def test_is_info_to_test_ii_rejects_test_ii_with_info_and_direct_to():
    payload = {
        "to": [TEST_II_MAILBOX, INFO_MAILBOX],
        "cc": [],
        "routing_recipient": TEST_II_MAILBOX,
    }
    assert is_info_to_test_ii_routing(mailbox=TEST_II_MAILBOX, payload=payload) is True


def test_is_only_info_to_accepts_info_routing_without_cc():
    payload = {"to": [INFO_MAILBOX], "cc": [], "routing_recipient": INFO_MAILBOX}
    assert is_only_info_to(mailbox=INFO_MAILBOX, payload=payload) is True


def test_is_only_info_to_accepts_empty_to_in_info_mailbox():
    payload = {"to": [], "cc": [], "routing_recipient": INFO_MAILBOX}
    assert is_only_info_to(mailbox=INFO_MAILBOX, payload=payload) is True


def test_is_only_info_to_rejects_cc():
    payload = {"to": [INFO_MAILBOX], "cc": [TEST_II_MAILBOX], "routing_recipient": INFO_MAILBOX}
    assert is_only_info_to(mailbox=INFO_MAILBOX, payload=payload) is False


def test_is_only_info_to_rejects_other_to():
    payload = {"to": [INFO_MAILBOX, TENDER_MAILBOX], "cc": [], "routing_recipient": INFO_MAILBOX}
    assert is_only_info_to(mailbox=INFO_MAILBOX, payload=payload) is False


def test_is_only_info_to_rejects_test_ii_routing():
    payload = {"to": [INFO_MAILBOX], "cc": [], "routing_recipient": TEST_II_MAILBOX}
    assert is_only_info_to(mailbox=TEST_II_MAILBOX, payload=payload) is False


def test_email_eligible_for_erp_accepts_info_only():
    assert email_eligible_for_erp(
        mailbox=INFO_MAILBOX,
        to=[INFO_MAILBOX],
        cc=[],
        routing_recipient=INFO_MAILBOX,
    ) is True


def test_email_eligible_for_erp_rejects_td_sales():
    assert email_eligible_for_erp(
        mailbox="td_sales2.8@turbo-don.ru",
        to=["td_sales2.8@turbo-don.ru"],
        cc=[],
        routing_recipient="td_sales2.8@turbo-don.ru",
    ) is False


def test_email_eligible_for_erp_accepts_info_routing_in_multi_to():
    assert email_eligible_for_erp(
        mailbox=TEST_II_MAILBOX,
        to=[INFO_MAILBOX, TENDER_MAILBOX],
        cc=[],
        routing_recipient=INFO_MAILBOX,
    ) is True


def test_email_eligible_for_erp_accepts_info_to_test_ii_chain():
    assert email_eligible_for_erp(
        mailbox=TEST_II_MAILBOX,
        to=[INFO_MAILBOX],
        cc=[],
        routing_recipient=TEST_II_MAILBOX,
    ) is True


def test_is_dialog_message_by_status():
    assert is_dialog_message(status="dialog", payload=None) is True


def test_is_dialog_message_by_payload_dialog_block():
    payload = {"dialog": {"mode": "dormant", "document_kind": "dialog"}}
    assert is_dialog_message(status="done", payload=payload) is True


def test_is_dialog_message_by_routing_decision_kind():
    payload = {"routing_decision": {"document_kind": "dialog"}}
    assert is_dialog_message(status="done", payload=payload) is True


def test_email_eligible_for_erp_rejects_dialog():
    payload = {"dialog": {"mode": "activated", "document_kind": "dialog"}}
    assert email_eligible_for_erp(
        mailbox=INFO_MAILBOX,
        to=[INFO_MAILBOX],
        cc=[],
        routing_recipient=INFO_MAILBOX,
        payload=payload,
        status="done",
    ) is False


def test_recipient_display_value_prefers_routing_recipient():
    payload = {
        "routing_recipient": "tender@turbo-don.ru",
        "to": [INFO_MAILBOX],
    }
    assert recipient_display_value(mailbox=INFO_MAILBOX, payload=payload) == "tender@turbo-don.ru"


def test_matches_recipient_q_on_routing_recipient():
    payload = {"routing_recipient": INFO_MAILBOX, "to": []}
    assert matches_recipient_q(mailbox=INFO_MAILBOX, payload=payload, query="info@turbo") is True
    assert matches_recipient_q(mailbox=INFO_MAILBOX, payload=payload, query="tender") is False


def test_matches_recipient_q_on_to_when_routing_empty():
    payload = {"to": [INFO_MAILBOX, "tender@turbo-don.ru"], "cc": []}
    assert matches_recipient_q(mailbox=INFO_MAILBOX, payload=payload, query="info@") is True
    assert matches_recipient_q(mailbox=INFO_MAILBOX, payload=payload, query="tender") is True


def test_matches_recipient_q_falls_back_to_mailbox_when_recipient_empty():
    payload = {"to": [], "cc": []}
    assert matches_recipient_q(mailbox=INFO_MAILBOX, payload=payload, query="info") is True
    assert matches_recipient_q(mailbox=TENDER_MAILBOX, payload=payload, query="info") is False


def test_matches_info_recipient_only_on_routing_and_to():
    info_payload = {"routing_recipient": INFO_MAILBOX, "to": []}
    tender_payload = {"routing_recipient": "tender@turbo-don.ru", "to": []}
    to_info_payload = {"to": [INFO_MAILBOX], "cc": []}

    assert matches_info_recipient_only(mailbox=INFO_MAILBOX, payload=info_payload) is True
    assert matches_info_recipient_only(mailbox=INFO_MAILBOX, payload=to_info_payload) is True
    assert matches_info_recipient_only(mailbox=INFO_MAILBOX, payload=tender_payload) is False
    assert matches_info_recipient_only(mailbox=INFO_MAILBOX, payload=None) is False


def test_matches_info_recipient_only_equivalent_to_recipient_q_info():
    cases = [
        (INFO_MAILBOX, {"routing_recipient": INFO_MAILBOX, "to": []}),
        (INFO_MAILBOX, {"to": [INFO_MAILBOX], "cc": []}),
        (INFO_MAILBOX, {"routing_recipient": "tender@turbo-don.ru", "to": []}),
        (TEST_II_MAILBOX, {"to": [TEST_II_MAILBOX], "routing_recipient": TEST_II_MAILBOX}),
    ]
    for mailbox, payload in cases:
        assert matches_info_recipient_only(mailbox=mailbox, payload=payload) == matches_recipient_q(
            mailbox=mailbox,
            payload=payload,
            query=INFO_RECIPIENT_Q,
        )


def test_list_email_messages_passes_recipient_q_filter():
    client = TestClient(app)
    with _mock_repo(rows=[], total=0) as mock_repo:
        response = client.get(
            "/api/v1/email-messages",
            params={"recipient_q": "info@turbo-don.ru"},
        )
        assert response.status_code == 200
        mock_repo.list_messages_paginated.assert_called_once()
        assert mock_repo.list_messages_paginated.call_args.kwargs["recipient_q"] == "info@turbo-don.ru"


def test_list_email_messages_passes_only_info_to_filter():
    client = TestClient(app)
    with _mock_repo(rows=[], total=0) as mock_repo:
        response = client.get(
            "/api/v1/email-messages",
            params={"only_info_to": "true"},
        )
        assert response.status_code == 200
        mock_repo.list_messages_paginated.assert_called_once()
        assert mock_repo.list_messages_paginated.call_args.kwargs["only_info_to"] is True


def test_list_email_messages_passes_only_info_to_test_ii_filter():
    client = TestClient(app)
    with _mock_repo(rows=[], total=0) as mock_repo:
        response = client.get(
            "/api/v1/email-messages",
            params={"only_info_to_test_ii": "true"},
        )
        assert response.status_code == 200
        mock_repo.list_messages_paginated.assert_called_once()
        assert mock_repo.list_messages_paginated.call_args.kwargs["only_info_to_test_ii"] is True


def test_list_email_messages_passes_info_recipient_only_filter():
    client = TestClient(app)
    with _mock_repo(rows=[], total=0) as mock_repo:
        response = client.get(
            "/api/v1/email-messages",
            params={"info_recipient_only": "true"},
        )
        assert response.status_code == 200
        mock_repo.list_messages_paginated.assert_called_once()
        assert mock_repo.list_messages_paginated.call_args.kwargs["info_recipient_only"] is True


def _payload_json(*, to: list[str], cc: list[str] | None = None, routing: str | None = None) -> str:
    payload: dict[str, object] = {"to": to, "cc": cc or []}
    if routing is not None:
        payload["routing_recipient"] = routing
    return json.dumps(payload, ensure_ascii=False)


def _insert_filter_probe_row(
    session,
    *,
    suffix: str,
    mailbox: str,
    payload_json: str,
    is_info_recipient: bool | None = None,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    if is_info_recipient is None:
        is_info_recipient = compute_is_info_recipient(
            mailbox=mailbox,
            raw_payload_json=payload_json,
        )
    session.add(
        EmailMessageRow(
            id=row_id,
            message_id=f"<only-info-filter-{suffix}@pytest>",
            received_at=datetime.now(timezone.utc).replace(tzinfo=None),
            mailbox=mailbox,
            sender_email="filter-probe@example.com",
            sender_name="Filter Probe",
            subject=f"only_info filter probe {suffix}",
            status="done",
            raw_payload_json=payload_json,
            is_info_recipient=is_info_recipient,
        )
    )
    return row_id


def test_compute_is_info_recipient_matches_python():
    cases = [
        (INFO_MAILBOX, _payload_json(to=[], routing=INFO_MAILBOX), True),
        (INFO_MAILBOX, _payload_json(to=["tender@turbo-don.ru"]), False),
        (INFO_MAILBOX, None, False),
    ]
    for mailbox, payload_json, expected in cases:
        assert (
            compute_is_info_recipient(mailbox=mailbox, raw_payload_json=payload_json) is expected
        )


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_recipient_q_sql_filter_matches_python_on_postgres():
    suffix = uuid.uuid4().hex[:8]
    row_ids: list[uuid.UUID] = []
    cases = [
        (
            "routing-info",
            INFO_MAILBOX,
            _payload_json(to=[], routing=INFO_MAILBOX),
            "info@turbo",
            True,
        ),
        (
            "routing-tender",
            INFO_MAILBOX,
            _payload_json(to=[], routing="tender@turbo-don.ru"),
            "info@turbo",
            False,
        ),
        (
            "to-info",
            INFO_MAILBOX,
            _payload_json(to=[INFO_MAILBOX]),
            "info@turbo-don",
            True,
        ),
        (
            "to-tender",
            INFO_MAILBOX,
            _payload_json(to=["tender@turbo-don.ru"]),
            "info@",
            False,
        ),
    ]

    factory = get_session_factory()
    with factory() as session:
        for name, mailbox, payload_json, _query, _expected in cases:
            row_ids.append(
                _insert_filter_probe_row(
                    session,
                    suffix=f"{suffix}-{name}",
                    mailbox=mailbox,
                    payload_json=payload_json,
                )
            )
        session.commit()

        for (name, mailbox, payload_json, query, expected), row_id in zip(cases, row_ids, strict=True):
            payload = json.loads(payload_json)
            assert (
                matches_recipient_q(mailbox=mailbox, payload=payload, query=query) is expected
            ), name
            sql_hit = (
                session.query(EmailMessageRow)
                .filter(
                    EmailMessageRow.id == row_id,
                    recipient_q_sql_filter(
                        EmailMessageRow.mailbox,
                        EmailMessageRow.raw_payload_json,
                        query,
                    ),
                )
                .count()
                == 1
            )
            assert sql_hit is expected, name

        repo = EmailRepository(session)
        filtered = repo.list_messages(recipient_q="info@turbo-don", limit=500)
        filtered_ids = {row.id for row in filtered}
        for (name, _mailbox, _payload, _query, expected), row_id in zip(cases, row_ids, strict=True):
            if name in {"routing-info", "to-info"}:
                assert (row_id in filtered_ids) is expected, name
            elif name in {"routing-tender", "to-tender"}:
                assert row_id not in filtered_ids, name

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_info_recipient_only_filter_on_postgres():
    suffix = uuid.uuid4().hex[:8]
    row_ids: list[uuid.UUID] = []
    cases = [
        ("routing-info", INFO_MAILBOX, _payload_json(to=[], routing=INFO_MAILBOX), True),
        ("routing-tender", INFO_MAILBOX, _payload_json(to=[], routing="tender@turbo-don.ru"), False),
        ("to-info", INFO_MAILBOX, _payload_json(to=[INFO_MAILBOX]), True),
        ("to-tender", INFO_MAILBOX, _payload_json(to=["tender@turbo-don.ru"]), False),
    ]

    factory = get_session_factory()
    with factory() as session:
        for name, mailbox, payload_json, _expected in cases:
            row_ids.append(
                _insert_filter_probe_row(
                    session,
                    suffix=f"{suffix}-{name}",
                    mailbox=mailbox,
                    payload_json=payload_json,
                )
            )
        session.commit()

        repo = EmailRepository(session)
        filtered = repo.list_messages(info_recipient_only=True, limit=500)
        filtered_ids = {row.id for row in filtered}
        for (name, _mailbox, _payload, expected), row_id in zip(cases, row_ids, strict=True):
            assert (row_id in filtered_ids) is expected, name

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_info_recipient_only_uses_denormalized_column():
    """Filter reads is_info_recipient column, not raw_payload_json on each row."""
    suffix = uuid.uuid4().hex[:8]
    info_payload = _payload_json(to=[INFO_MAILBOX], routing=INFO_MAILBOX)
    tender_payload = _payload_json(to=["tender@turbo-don.ru"], routing="tender@turbo-don.ru")
    row_ids: list[uuid.UUID] = []

    factory = get_session_factory()
    with factory() as session:
        row_ids.append(
            _insert_filter_probe_row(
                session,
                suffix=f"{suffix}-column-true",
                mailbox=INFO_MAILBOX,
                payload_json=tender_payload,
                is_info_recipient=True,
            )
        )
        row_ids.append(
            _insert_filter_probe_row(
                session,
                suffix=f"{suffix}-column-false",
                mailbox=INFO_MAILBOX,
                payload_json=info_payload,
                is_info_recipient=False,
            )
        )
        session.commit()

        repo = EmailRepository(session)
        filtered_ids = {row.id for row in repo.list_messages(info_recipient_only=True, limit=500)}
        assert row_ids[0] in filtered_ids
        assert row_ids[1] not in filtered_ids

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


def test_sanitize_json_text_for_pg_strips_nul_escape_and_byte():
    payload = {"to": [INFO_MAILBOX], "body_text": f"Probe\x00 with NUL"}
    raw = json.dumps(payload, ensure_ascii=False)
    assert "\x00" not in raw
    assert "\\u0000" in raw

    cleaned = sanitize_json_text_for_pg(raw)
    assert "\\u0000" not in cleaned
    assert json.loads(cleaned)["body_text"] == "Probe with NUL"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_info_recipient_only_filter_survives_nul_bytes_in_payload():
    """json.dumps NUL as \\u0000 must not crash JSONB cast during list/stats filters."""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "to": [INFO_MAILBOX],
        "cc": [],
        "routing_recipient": INFO_MAILBOX,
        "body_text": f"Probe {suffix}\x00 with NUL",
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    assert "\\u0000" in payload_json
    row_id = None
    factory = get_session_factory()
    with factory() as session:
        row_id = _insert_filter_probe_row(
            session,
            suffix=suffix,
            mailbox=INFO_MAILBOX,
            payload_json=payload_json,
        )
        session.commit()

        repo = EmailRepository(session)
        filtered = repo.list_messages(info_recipient_only=True, limit=500)
        assert row_id in {row.id for row in filtered}
        assert repo.count_messages(info_recipient_only=True) >= 1
        assert sum(repo.count_by_status(info_recipient_only=True).values()) >= 1

        session.query(EmailMessageRow).filter(EmailMessageRow.id == row_id).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_only_info_to_sql_filter_matches_python_on_postgres():
    suffix = uuid.uuid4().hex[:8]
    row_ids: list[uuid.UUID] = []
    cases = [
        ("info-only", INFO_MAILBOX, _payload_json(to=[INFO_MAILBOX], routing=INFO_MAILBOX), True),
        ("info-empty-to", INFO_MAILBOX, _payload_json(to=[], routing=INFO_MAILBOX), True),
        ("info-with-cc", INFO_MAILBOX, _payload_json(to=[INFO_MAILBOX], cc=[TEST_II_MAILBOX], routing=INFO_MAILBOX), False),
        ("multi-to", INFO_MAILBOX, _payload_json(to=[INFO_MAILBOX, TENDER_MAILBOX], routing=INFO_MAILBOX), False),
        ("test-ii-routing", TEST_II_MAILBOX, _payload_json(to=[INFO_MAILBOX], routing=TEST_II_MAILBOX), False),
    ]

    factory = get_session_factory()
    with factory() as session:
        for name, mailbox, payload_json, _expected in cases:
            row_ids.append(
                _insert_filter_probe_row(
                    session,
                    suffix=f"{suffix}-{name}",
                    mailbox=mailbox,
                    payload_json=payload_json,
                )
            )
        session.commit()

        repo = EmailRepository(session)
        filtered = repo.list_messages(only_info_to=True, limit=500)
        filtered_ids = {row.id for row in filtered}

        for (name, _mailbox, _payload, expected), row_id in zip(cases, row_ids, strict=True):
            assert (row_id in filtered_ids) is expected, name

        sql_count = session.query(EmailMessageRow).filter(
            only_info_to_sql_filter(EmailMessageRow.mailbox, EmailMessageRow.raw_payload_json)
        ).count()
        assert sql_count >= sum(1 for *_, expected in cases if expected)

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_info_to_test_ii_sql_filter_matches_python_on_postgres():
    """Регрессия: SQL-фильтр info→test_ii выполняется в PostgreSQL без ошибки колонки."""
    suffix = uuid.uuid4().hex[:8]
    row_ids: list[uuid.UUID] = []
    cases = [
        (
            "forwarded-empty-to",
            TEST_II_MAILBOX,
            _payload_json(to=[], routing=TEST_II_MAILBOX),
            True,
        ),
        (
            "info-in-to",
            TEST_II_MAILBOX,
            _payload_json(to=[INFO_MAILBOX], routing=TEST_II_MAILBOX),
            True,
        ),
        (
            "direct-test-ii",
            TEST_II_MAILBOX,
            _payload_json(to=[TEST_II_MAILBOX], routing=TEST_II_MAILBOX),
            False,
        ),
        (
            "info-only-routing",
            TEST_II_MAILBOX,
            _payload_json(to=[INFO_MAILBOX], routing=INFO_MAILBOX),
            False,
        ),
        (
            "pereadres-routing",
            TEST_II_MAILBOX,
            _payload_json(to=[INFO_MAILBOX], routing=PEREADRES_MAILBOX),
            False,
        ),
        (
            "tender-routing",
            TEST_II_MAILBOX,
            _payload_json(to=[TENDER_MAILBOX], routing=TENDER_MAILBOX),
            False,
        ),
    ]

    factory = get_session_factory()
    with factory() as session:
        for name, mailbox, payload_json, _expected in cases:
            row_ids.append(
                _insert_filter_probe_row(
                    session,
                    suffix=f"{suffix}-{name}",
                    mailbox=mailbox,
                    payload_json=payload_json,
                )
            )
        session.commit()

        repo = EmailRepository(session)
        filtered = repo.list_messages(only_info_to_test_ii=True, limit=500)
        filtered_ids = {row.id for row in filtered}

        for (name, _mailbox, payload_json, expected), row_id in zip(cases, row_ids, strict=True):
            assert (row_id in filtered_ids) is expected, name

        sql_count = session.query(EmailMessageRow).filter(
            info_to_test_ii_sql_filter(EmailMessageRow.mailbox, EmailMessageRow.raw_payload_json)
        ).count()
        assert sql_count >= sum(1 for *_, expected in cases if expected)

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_list_email_messages_only_info_to_test_ii_endpoint_on_postgres():
    suffix = uuid.uuid4().hex[:8]
    include_id = _payload_json(to=[], routing=TEST_II_MAILBOX)
    exclude_id_payload = _payload_json(to=[TEST_II_MAILBOX], routing=TEST_II_MAILBOX)
    row_ids: list[uuid.UUID] = []

    factory = get_session_factory()
    with factory() as session:
        row_ids.append(
            _insert_filter_probe_row(
                session,
                suffix=f"{suffix}-include",
                mailbox=TEST_II_MAILBOX,
                payload_json=include_id,
            )
        )
        row_ids.append(
            _insert_filter_probe_row(
                session,
                suffix=f"{suffix}-exclude",
                mailbox=TEST_II_MAILBOX,
                payload_json=exclude_id_payload,
            )
        )
        session.commit()

    client = TestClient(app)
    try:
        response = client.get(
            "/api/v1/email-messages",
            params={"only_info_to_test_ii": "true", "q": f"only_info filter probe {suffix}"},
        )
        assert response.status_code == 200
        payload = response.json()
        returned_ids = {item["id"] for item in payload["items"]}
        assert str(row_ids[0]) in returned_ids
        assert str(row_ids[1]) not in returned_ids
        assert payload["total"] == len(returned_ids) == 1
    finally:
        with factory() as session:
            session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
                synchronize_session=False
            )
            session.commit()


def _payload_with_operator_flags(
    *,
    to: list[str] | None = None,
    operator_verified: bool | None = None,
    operator_corrected: bool | None = None,
) -> str:
    payload: dict[str, object] = {"to": to or [INFO_MAILBOX], "cc": []}
    if operator_verified is not None:
        payload["operator_verified"] = operator_verified
    if operator_corrected is not None:
        payload["operator_corrected"] = operator_corrected
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_count_operator_review_states_on_postgres():
    suffix = uuid.uuid4().hex[:8]
    row_ids: list[uuid.UUID] = []
    cases = [
        ("pending", _payload_with_operator_flags(), "pending"),
        ("verified", _payload_with_operator_flags(operator_verified=True), "verified"),
        ("corrected", _payload_with_operator_flags(operator_corrected=True), "corrected"),
        (
            "verified-and-corrected",
            _payload_with_operator_flags(operator_verified=True, operator_corrected=True),
            "corrected",
        ),
    ]

    factory = get_session_factory()
    with factory() as session:
        for name, payload_json, _expected_state in cases:
            row_ids.append(
                _insert_filter_probe_row(
                    session,
                    suffix=f"{suffix}-{name}",
                    mailbox=INFO_MAILBOX,
                    payload_json=payload_json,
                )
            )
        session.commit()

        repo = EmailRepository(session)
        counts = repo.count_operator_review_states(
            search=f"only_info filter probe {suffix}",
        )
        assert counts["all"] == len(cases)
        assert counts["pending"] == 1
        assert counts["verified"] == 1
        assert counts["corrected"] == 2

        rows = repo.list_messages(search=f"only_info filter probe {suffix}", limit=500)
        event_hints = repo.batch_operator_review_event_hints([row.id for row in rows])
        python_counts = {"pending": 0, "verified": 0, "corrected": 0}
        for row in rows:
            payload = json.loads(row.raw_payload_json or "{}")
            hints = event_hints.get(row.id) or {}
            state = operator_review_state(
                payload,
                has_operator_approve=hints.get("has_operator_approve", False),
                has_operator_change=hints.get("has_operator_change", False),
            )
            python_counts[state] += 1
        assert python_counts["pending"] == counts["pending"]
        assert python_counts["verified"] == counts["verified"]
        assert python_counts["corrected"] == counts["corrected"]

        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not configured")
def test_operator_review_state_infers_from_classification_events_on_postgres():
    from datetime import datetime, timezone

    from agent_pochta.db.models import ClassificationEventRow

    suffix = uuid.uuid4().hex[:8]
    factory = get_session_factory()
    row_ids: list[uuid.UUID] = []

    with factory() as session:
        approve_id = _insert_filter_probe_row(
            session,
            suffix=f"{suffix}-approve",
            mailbox=INFO_MAILBOX,
            payload_json=_payload_with_operator_flags(),
        )
        change_id = _insert_filter_probe_row(
            session,
            suffix=f"{suffix}-change",
            mailbox=INFO_MAILBOX,
            payload_json=_payload_with_operator_flags(),
        )
        row_ids.extend([approve_id, change_id])
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            ClassificationEventRow(
                created_at=now,
                message_id=f"<only-info-filter-{suffix}-approve@pytest>",
                email_id=approve_id,
                category="department",
                event_type="operator_approve",
                actor="operator",
                source="pytest",
            )
        )
        session.add(
            ClassificationEventRow(
                created_at=now,
                message_id=f"<only-info-filter-{suffix}-change@pytest>",
                email_id=change_id,
                category="department",
                event_type="operator_change",
                actor="operator",
                source="pytest",
            )
        )
        session.commit()

        repo = EmailRepository(session)
        counts = repo.count_operator_review_states(search=f"only_info filter probe {suffix}")
        assert counts["verified"] == 1
        assert counts["corrected"] == 1
        assert counts["pending"] == 0

        hints = repo.batch_operator_review_event_hints(row_ids)
        assert hints[approve_id]["has_operator_approve"] is True
        assert hints[change_id]["has_operator_change"] is True

        client = TestClient(app)
        response = client.get(
            "/api/v1/email-messages",
            params={"q": f"only_info filter probe {suffix}", "limit": 10},
        )
        assert response.status_code == 200
        by_id = {item["id"]: item for item in response.json()["items"]}
        assert by_id[str(approve_id)]["operator_review_state"] == "verified"
        assert by_id[str(change_id)]["operator_review_state"] == "corrected"

        session.query(ClassificationEventRow).filter(
            ClassificationEventRow.email_id.in_(row_ids)
        ).delete(synchronize_session=False)
        session.query(EmailMessageRow).filter(EmailMessageRow.id.in_(row_ids)).delete(
            synchronize_session=False
        )
        session.commit()
