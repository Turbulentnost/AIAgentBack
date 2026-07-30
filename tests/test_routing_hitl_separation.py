"""Тесты разделения обучения маршрутизации и spam_learning."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.corrections import extract_correction_keywords, load_corrections
from agent_pochta.routing.hitl import (
    is_routing_escalation_reason,
    parse_recipient_from_message_id,
    resolve_department_from_recipient,
    row_requires_routing_review,
)
from agent_pochta.routing.learning import migrate_misrouted_spam_entries
from agent_pochta.rules.spam_learning import load_spam_learning, save_learning_entry
from agent_pochta.schemas import EmailMessage, ProcessingStatus


def test_is_routing_escalation_reason_detects_route_review():
    assert is_routing_escalation_reason("Низкая уверенность маршрута (НИЗКАЯ, score=45)")
    assert is_routing_escalation_reason("Конфликт нескольких правил маршрутизации")


def test_api_dept_confidence_recovers_score_from_hitl_reason():
    from agent_pochta.api.app import _dept_confidence_for_api

    row = _email_row(
        status=ProcessingStatus.AWAITING_HUMAN.value,
        spam_reason=None,
        payload={"hitl_reason": "Низкая уверенность маршрута (НИЗКАЯ, score=45)"},
    )
    row.dept_confidence = 0.0
    assert _dept_confidence_for_api(
        row, {"hitl_reason": "Низкая уверенность маршрута (НИЗКАЯ, score=45)"}
    ) == 0.45
    assert not is_routing_escalation_reason("Спам в серой зоне (confidence=0.55)")
    assert not is_routing_escalation_reason("Рекламная рассылка")


def test_api_dept_confidence_recovers_score_from_routing_decision():
    from agent_pochta.api.app import _dept_confidence_for_api

    row = _email_row(
        status=ProcessingStatus.AWAITING_HUMAN.value,
        spam_reason=None,
        payload={},
    )
    row.dept_confidence = 0.0
    assert _dept_confidence_for_api(
        row,
        {
            "routing_decision": {
                "confidence_score": 45,
                "confidence_level": "НИЗКАЯ",
            }
        },
    ) == 0.45


def test_api_route_confidence_level_aligns_with_dept_confidence():
    from agent_pochta.api.app import _row_to_list_dict

    row = _email_row(
        status=ProcessingStatus.DONE.value,
        spam_reason=None,
        payload={
            "routing_decision": {
                "confidence_score": 45,
                "confidence_level": "НИЗКАЯ",
            }
        },
    )
    row.dept_confidence = 0.88
    data = _row_to_list_dict(row)
    assert data["dept_confidence"] == 0.88
    assert data["route_confidence_level"] == "СРЕДНЯЯ"
    assert data["route_confidence_score"] == 88


def test_extract_correction_keywords_strips_subject_prefix_and_adds_recipient():
    keywords = extract_correction_keywords(
        "Re: вопрос по оплате",
        "Приветствую. Спасибо большое.",
        recipient="uk_omto11@turbo-don.ru",
    )
    assert keywords[0] == "вопрос по оплате"
    assert "uk_omto11" in keywords
    assert "приветствую." not in keywords


def test_parse_recipient_from_message_id():
    message_id = "<000401dd1292$0c2995c0$247cc140$@rd.technoavia.ru>#uk_omto11@turbo-don.ru"
    assert parse_recipient_from_message_id(message_id) == "uk_omto11@turbo-don.ru"
    assert parse_recipient_from_message_id("<plain@example>") is None


def test_resolve_department_from_recipient_uses_email_keyword_rules():
    dept = resolve_department_from_recipient("uk_omto11@turbo-don.ru")
    assert dept == ("00-000065", "ОМТО")


def test_row_requires_routing_review_uses_hitl_reason_in_payload():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<hitl@example>#uk_omto11@turbo-don.ru",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        status=ProcessingStatus.AWAITING_HUMAN.value,
        raw_payload_json=json.dumps(
            {"hitl_reason": "Низкая уверенность маршрута (НИЗКАЯ, score=45)"},
            ensure_ascii=False,
        ),
    )
    assert row_requires_routing_review(row) is True


def test_migrate_misrouted_spam_entries_moves_routing_reason_to_corrections(
    tmp_path: Path,
    monkeypatch,
):
    spam_path = tmp_path / "spam_learning_patterns.json"
    corrections_path = tmp_path / "routing_corrections.json"
    corrections_path.write_text('{"version": "1.0", "entries": []}\n', encoding="utf-8")

    monkeypatch.setenv("SPAM_LEARNING_PATH", str(spam_path))
    monkeypatch.setenv("ROUTING_CORRECTIONS_PATH", str(corrections_path))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    message_id = "<000401dd1292$0c2995c0$247cc140$@rd.technoavia.ru>#uk_omto11@turbo-don.ru"
    save_learning_entry(
        label="spam",
        message_id=message_id,
        sender_email="gerasimenko@rd.technoavia.ru",
        subject="Re: вопрос по оплате",
        body="Приветствую.",
        reason="Низкая уверенность маршрута (НИЗКАЯ, score=45)",
        path=spam_path,
    )

    result = migrate_misrouted_spam_entries(
        spam_path=spam_path,
        corrections_path=corrections_path,
        since="2026-01-01",
    )

    assert result["removed_count"] == 1
    assert result["migrated_count"] == 1
    assert load_spam_learning(spam_path)["entries"] == []
    corrections = load_corrections(corrections_path)["entries"]
    assert len(corrections) == 1
    assert corrections[0]["department_id"] == "00-000065"
    assert corrections[0]["recipient"] == "uk_omto11@turbo-don.ru"
    assert "uk_omto11" in corrections[0]["keywords"]


def _email_row(*, status: str, spam_reason: str | None = None, payload: dict | None = None) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    base_payload = {
        "message_id": "<resolve@example>",
        "mailbox": "info@turbo-don.ru",
        "sender_email": "vendor@example.com",
        "subject": "Акт сверки",
        "body_text": "Просим подписать акт сверки.",
        "received_at": received_at.isoformat(),
        "routing_recipient": "jurist@turbo-don.ru",
    }
    if payload:
        base_payload.update(payload)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<resolve@example>#jurist@turbo-don.ru",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Акт сверки",
        status=status,
        department_id="00-000044",
        department_name="Юридический отдел",
        is_spam=False,
        spam_reason=spam_reason,
        raw_payload_json=json.dumps(base_payload, ensure_ascii=False),
    )


@contextmanager
def _mock_repo(row: EmailMessageRow):
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = EmailMessage.model_validate(
        json.loads(row.raw_payload_json)
    )
    repo.apply_human_resolution.side_effect = lambda row_id, **kwargs: row
    repo.clear_xml_document = MagicMock()
    repo.rebuild_xml_after_human_correction = MagicMock(return_value="<document></document>")

    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo, session


def test_mark_spam_succeeds_for_routing_hitl():
    row = _email_row(
        status=ProcessingStatus.AWAITING_HUMAN.value,
        spam_reason="Низкая уверенность маршрута (НИЗКАЯ, score=45)",
    )
    client = TestClient(app)

    with _mock_repo(row):
        with patch("agent_pochta.api.app.learn_from_spam_mark") as learn_spam:
            learn_spam.return_value = {
                "spam_pattern_saved": True,
                "spam_pattern_id": "pat-1",
                "qdrant_synced": False,
            }
            response = client.post(
                f"/api/v1/email-messages/{row.id}/resolve-human",
                json={"decision": "mark_spam"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert response.json()["spam_pattern_saved"] is True
    learn_spam.assert_called_once()
    assert learn_spam.call_args.kwargs["spam_reason"] == "Отмечено офис-менеджером"


def test_approve_routing_on_routing_hitl_calls_routing_correction():
    row = _email_row(
        status=ProcessingStatus.AWAITING_HUMAN.value,
        spam_reason="Низкая уверенность маршрута (НИЗКАЯ, score=45)",
    )
    client = TestClient(app)

    with _mock_repo(row):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch("agent_pochta.api.app.learn_from_routing_correction") as learn_routing:
                learn_routing.return_value = {"correction_saved": True}
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "approve_routing",
                        "department_id": "00-000044",
                        "department_name": "Юридический отдел",
                    },
                )

    assert response.status_code == 200
    learn_routing.assert_called_once()
    continue_task.delay.assert_called_once()


def test_mark_not_spam_skips_spam_learning_for_routing_hitl():
    row = _email_row(
        status=ProcessingStatus.AWAITING_HUMAN.value,
        spam_reason="Низкая уверенность маршрута (НИЗКАЯ, score=45)",
    )
    client = TestClient(app)

    with _mock_repo(row):
        with patch("agent_pochta.api.app.reprocess_message_task") as reprocess_task:
            with patch("agent_pochta.api.app.learn_from_not_spam") as learn_not_spam:
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={"decision": "mark_not_spam"},
                )

    assert response.status_code == 200
    learn_not_spam.assert_not_called()
    reprocess_task.delay.assert_called_once()
