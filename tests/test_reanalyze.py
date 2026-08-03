"""Тесты POST /api/v1/email-messages/{id}/reanalyze и meta reanalyze."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import EmailMessage, ProcessingStatus, SpamResult
from agent_pochta.workers.tasks import reprocess_message_task


def _email_row(*, status: str = ProcessingStatus.DONE.value) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<reanalyze@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="partner@example.com",
        sender_name="Partner",
        subject="Счёт на оплату",
        status=status,
        is_spam=False,
        department_id="00-000010",
        department_name="Бухгалтерия",
        raw_payload_json=json.dumps(
            {
                "message_id": "<reanalyze@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "partner@example.com",
                "subject": "Счёт на оплату",
                "body_text": "Просим оплатить счёт. С уважением, ООО Пример",
                "received_at": received_at.isoformat(),
            },
            ensure_ascii=False,
        ),
    )


@contextmanager
def _mock_repo(row: EmailMessageRow):
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = EmailMessage.model_validate(
        json.loads(row.raw_payload_json)
    )

    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo, session


def test_reanalyze_endpoint_schedules_reprocess_for_done():
    row = _email_row(status=ProcessingStatus.DONE.value)
    client = TestClient(app)
    task = MagicMock(id="task-reanalyze")

    with _mock_repo(row) as (_repo, _session):
        with patch("agent_pochta.api.app.reprocess_message_task") as reprocess_task:
            reprocess_task.delay.return_value = task
            response = client.post(f"/api/v1/email-messages/{row.id}/reanalyze")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reanalyzing"
    assert payload["id"] == str(row.id)
    assert payload["task_id"] == "task-reanalyze"
    assert row.status == ProcessingStatus.PROCESSING.value
    reprocess_task.delay.assert_called_once_with(str(row.id), reanalyze=True)


def test_reanalyze_endpoint_accepts_awaiting_human_and_error():
    client = TestClient(app)
    task = MagicMock(id="task-reanalyze")

    for status in (
        ProcessingStatus.AWAITING_HUMAN.value,
        ProcessingStatus.ERROR.value,
        ProcessingStatus.PROCESSING.value,
    ):
        row = _email_row(status=status)
        with _mock_repo(row):
            with patch("agent_pochta.api.app.reprocess_message_task") as reprocess_task:
                reprocess_task.delay.return_value = task
                response = client.post(f"/api/v1/email-messages/{row.id}/reanalyze")
        assert response.status_code == 200, status
        assert response.json()["status"] == "reanalyzing"


def test_reanalyze_endpoint_rejects_spam():
    row = _email_row(status=ProcessingStatus.SPAM.value)
    client = TestClient(app)

    with _mock_repo(row):
        response = client.post(f"/api/v1/email-messages/{row.id}/reanalyze")

    assert response.status_code == 400
    assert "restore-from-spam" in response.json()["detail"]


def test_reprocess_message_task_reanalyze_invokes_graph_with_meta():
    row = _email_row()
    graph = MagicMock()
    graph.invoke.return_value = {"status": ProcessingStatus.AWAITING_HUMAN}

    with patch("agent_pochta.workers.tasks.get_session_factory") as session_factory:
        session = MagicMock()
        session_factory.return_value.__enter__.return_value = session
        repo = MagicMock()
        repo.get_by_id.return_value = row
        repo.load_email_from_row.return_value = EmailMessage.model_validate(
            json.loads(row.raw_payload_json)
        )
        with patch("agent_pochta.workers.tasks.EmailRepository", return_value=repo):
            with patch("agent_pochta.workers.tasks.learn_from_not_spam") as learn_not_spam:
                with patch("agent_pochta.workers.tasks.get_worker_graph", return_value=graph):
                    result = reprocess_message_task(str(row.id), reanalyze=True)

    learn_not_spam.assert_not_called()
    repo.mark_restored_from_spam.assert_not_called()
    graph.invoke.assert_called_once()
    invoke_args = graph.invoke.call_args[0][0]
    assert invoke_args["meta"]["reanalyze"] is True
    assert "restored_from_spam" not in invoke_args["meta"]
    assert result["ok"] is True
    assert result["status"] == ProcessingStatus.AWAITING_HUMAN.value


def test_spam_filter_skips_on_reanalyze():
    from agent_pochta.nodes.n2_spam_filter import node_spam_filter
    from agent_pochta.services import ServiceContainer

    email = EmailMessage.model_validate(json.loads(_email_row().raw_payload_json))
    container = MagicMock(spec=ServiceContainer)
    result = node_spam_filter(
        {"email": email, "meta": {"reanalyze": True}, "trace": []},
        container=container,
    )
    assert "reanalyze_skip" in result["trace"]
    assert "spam" not in result
    assert "status" not in result


def test_route_department_reanalyze_calls_llm_and_forces_hitl(monkeypatch):
    from agent_pochta.nodes.n5_route_department import node_route_department
    from agent_pochta.routing.models import ConfidenceLevel, RoutingDecision, ServiceRoute
    from agent_pochta.schemas import RoutingResult
    from agent_pochta.services.llm_analyze import IncomingEmailAnalysis

    email = EmailMessage.model_validate(json.loads(_email_row().raw_payload_json))
    analysis = IncomingEmailAnalysis(
        spam=SpamResult(
            is_spam=False,
            confidence=0.05,
            reason="skipped",
            rule_hit="trusted_sender",
        ),
        routing=RoutingResult(
            department_id="00-000020",
            department_name="Юридический",
            confidence=0.9,
            reasoning="LLM",
        ),
        summary_ru="Краткий обзор",
        xml_theme="Оплатить: счёт",
        partner_name='ООО "Пример"',
        process_type="исполнение",
    )
    llm = MagicMock()
    llm.analyze_incoming.return_value = analysis
    container = MagicMock()
    container.llm = llm
    container.rag.search_departments.return_value = []

    decision = RoutingDecision(
        organization="НП",
        direction="КС",
        process="исполнение",
        services=[ServiceRoute(code="00-000010", name="Бухгалтерия", reasoning="rule")],
        confidence_level=ConfidenceLevel.HIGH,
        confidence_score=90,
        match_source="exact_email",
        xml_document="<document/>",
    )

    monkeypatch.setenv("LLM_GATEWAY_URL", "http://llm")
    monkeypatch.setenv("USE_STUBS", "false")
    monkeypatch.setenv("RAG_DEPARTMENT_ENABLED", "true")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.nodes.n5_route_department.route_email",
        return_value=decision,
    ):
        with patch(
            "agent_pochta.nodes.n5_route_department.rebuild_decision_xml",
            side_effect=lambda email_obj, dec, **kwargs: dec,
        ):
            result = node_route_department(
                {
                    "email": email,
                    "combined_text": email.body_text or "",
                    "attachments_text": "",
                    "meta": {"reanalyze": True},
                    "trace": [],
                },
                container=container,
            )

    llm.analyze_incoming.assert_called_once()
    assert llm.analyze_incoming.call_args.kwargs.get("skip_spam_check") is True
    assert result["status"] == ProcessingStatus.AWAITING_HUMAN
    assert result["routing"].department_id == "00-000020"
    assert "reanalyze_hitl" in result["trace"]
