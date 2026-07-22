"""Тесты POST /api/v1/email-messages/{id}/resolve-human."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import EmailMessage, ProcessingStatus


def _email_row(*, status: str) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<resolve@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Акт сверки",
        status=status,
        department_id="00-000044",
        department_name="Юридический отдел",
        is_spam=status == ProcessingStatus.SPAM.value,
        raw_payload_json=json.dumps(
            {
                "message_id": "<resolve@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "vendor@example.com",
                "subject": "Акт сверки",
                "body_text": "Просим подписать акт сверки.",
                "received_at": received_at.isoformat(),
                "routing_recipient": "jurist@turbo-don.ru",
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

    def _apply_human_resolution(
        row_id: uuid.UUID,
        *,
        status: str,
        department_id: str | None = None,
        department_name: str | None = None,
        is_spam: bool | None = None,
        **kwargs: object,
    ) -> EmailMessageRow | None:
        row.status = status
        row.human_review = False
        if department_id is not None:
            row.department_id = department_id
        if department_name is not None:
            row.department_name = department_name
        if is_spam is not None:
            row.is_spam = is_spam
        return row

    repo.apply_human_resolution.side_effect = _apply_human_resolution
    repo.clear_xml_document = MagicMock()
    repo.rebuild_xml_after_human_correction = MagicMock(return_value="<document></document>")

    def _set_operator_verified(email_row: EmailMessageRow, verified: bool = True) -> None:
        payload = json.loads(email_row.raw_payload_json or "{}")
        if verified:
            payload["operator_verified"] = True
            payload["operator_verified_at"] = "2026-07-15T00:00:00"
        else:
            payload.pop("operator_verified", None)
            payload.pop("operator_verified_at", None)
        email_row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    repo.set_operator_verified.side_effect = _set_operator_verified

    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo, session


def test_approve_routing_on_error_sets_done_and_skips_pipeline():
    row = _email_row(status=ProcessingStatus.ERROR.value)
    client = TestClient(app)

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={
                    "correction_saved": True,
                    "correction_id": "c1",
                    "keywords_added": 0,
                    "qdrant_updated": False,
                    "learning_keywords": [],
                },
            ):
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "approve_routing",
                        "department_id": "00-000002",
                        "department_name": "Бухгалтерия",
                    },
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "correction_saved"
    assert row.status == ProcessingStatus.DONE.value
    assert row.department_id == "00-000002"
    continue_task.delay.assert_not_called()
    repo.rebuild_xml_after_human_correction.assert_called_once()


def test_mark_verified_on_done_keeps_status_and_sets_flag():
    row = _email_row(status=ProcessingStatus.DONE.value)
    client = TestClient(app)

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={
                    "correction_saved": False,
                    "correction_id": None,
                    "keywords_added": 0,
                    "qdrant_updated": False,
                    "learning_keywords": [],
                },
            ):
                with patch("agent_pochta.api.app.log_department_resolution") as log_dept:
                    response = client.post(
                        f"/api/v1/email-messages/{row.id}/resolve-human",
                        json={
                            "decision": "mark_verified",
                            "department_id": "00-000044",
                            "department_name": "Юридический отдел",
                        },
                    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "verified"
    assert payload["operator_verified"] is True
    assert row.status == ProcessingStatus.DONE.value
    stored = json.loads(row.raw_payload_json)
    assert stored["operator_verified"] is True
    continue_task.delay.assert_not_called()
    repo.set_operator_verified.assert_called_once_with(row, True)
    log_dept.assert_called_once()


def test_mark_verified_on_error_keeps_error_status():
    row = _email_row(status=ProcessingStatus.ERROR.value)
    client = TestClient(app)

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={
                    "correction_saved": False,
                    "correction_id": None,
                    "keywords_added": 0,
                    "qdrant_updated": False,
                    "learning_keywords": [],
                },
            ):
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "mark_verified",
                        "department_id": "00-000044",
                        "department_name": "Юридический отдел",
                    },
                )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert row.status == ProcessingStatus.ERROR.value
    continue_task.delay.assert_not_called()
    repo.set_operator_verified.assert_called_once_with(row, True)


def test_mark_verified_rejected_for_awaiting_human():
    row = _email_row(status=ProcessingStatus.AWAITING_HUMAN.value)
    client = TestClient(app)

    with _mock_repo(row) as (_repo, _session):
        response = client.post(
            f"/api/v1/email-messages/{row.id}/resolve-human",
            json={
                "decision": "mark_verified",
                "department_id": "00-000044",
                "department_name": "Юридический отдел",
            },
        )

    assert response.status_code == 400
    assert "done or error" in response.json()["detail"]


def test_approve_routing_on_done_schedules_erp_sync_when_document_exists():
    row = _email_row(status=ProcessingStatus.DONE.value)
    row.erp_task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    row.erp_document_number = "ВК-000001"
    client = TestClient(app)
    sync_task = MagicMock(id="sync-task-1")

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch("agent_pochta.api.app.sync_erp_correction_task") as sync_erp_task:
                sync_erp_task.delay.return_value = sync_task
                with patch(
                    "agent_pochta.api.app.learn_from_routing_correction",
                    return_value={
                        "correction_saved": True,
                        "correction_id": "c1",
                        "keywords_added": 0,
                        "qdrant_updated": False,
                        "learning_keywords": [],
                    },
                ):
                    response = client.post(
                        f"/api/v1/email-messages/{row.id}/resolve-human",
                        json={
                            "decision": "approve_routing",
                            "department_id": "00-000002",
                            "department_name": "Бухгалтерия",
                        },
                    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "correction_saved"
    assert payload["erp_sync_scheduled"] is True
    assert payload["erp_sync_task_id"] == "sync-task-1"
    continue_task.delay.assert_not_called()
    sync_erp_task.delay.assert_called_once_with(row.message_id)


def test_approve_routing_on_done_keeps_status_and_skips_pipeline():
    row = _email_row(status=ProcessingStatus.DONE.value)
    client = TestClient(app)

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={
                    "correction_saved": True,
                    "correction_id": "c1",
                    "keywords_added": 0,
                    "qdrant_updated": False,
                    "learning_keywords": [],
                },
            ):
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "approve_routing",
                        "department_id": "00-000002",
                        "department_name": "Бухгалтерия",
                    },
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "correction_saved"
    assert payload["correction_saved"] is True
    assert row.status == ProcessingStatus.DONE.value
    assert row.department_id == "00-000002"
    assert row.department_name == "Бухгалтерия"
    continue_task.delay.assert_not_called()
    repo.rebuild_xml_after_human_correction.assert_called_once()


def test_approve_routing_on_awaiting_human_schedules_pipeline():
    row = _email_row(status=ProcessingStatus.AWAITING_HUMAN.value)
    client = TestClient(app)
    task = MagicMock(id="task-1")

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            continue_task.delay.return_value = task
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={
                    "correction_saved": True,
                    "correction_id": "c1",
                    "keywords_added": 0,
                    "qdrant_updated": False,
                    "learning_keywords": [],
                },
            ):
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "approve_routing",
                        "department_id": "00-000002",
                        "department_name": "Бухгалтерия",
                    },
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "continuing"
    assert payload["task_id"] == "task-1"
    assert row.status == ProcessingStatus.PROCESSING.value
    continue_task.delay.assert_called_once_with(str(row.id))
    repo.rebuild_xml_after_human_correction.assert_called_once()


def test_approve_routing_on_processing_reschedules_pipeline():
    row = _email_row(status=ProcessingStatus.PROCESSING.value)
    client = TestClient(app)
    task = MagicMock(id="task-retry")

    with _mock_repo(row) as (_repo, _session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            continue_task.delay.return_value = task
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={"correction_saved": True},
            ):
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={
                        "decision": "approve_routing",
                        "department_id": "00-000002",
                        "department_name": "Бухгалтерия",
                    },
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "continuing"
    assert payload["task_id"] == "task-retry"
    continue_task.delay.assert_called_once_with(str(row.id))


def test_mark_spam_on_done_sets_spam_status():
    row = _email_row(status=ProcessingStatus.DONE.value)
    row.is_spam = False
    client = TestClient(app)

    with _mock_repo(row) as (repo, _session):
        with patch(
            "agent_pochta.api.app.learn_from_spam_mark",
            return_value={
                "spam_pattern_saved": True,
                "spam_pattern_id": "p1",
                "qdrant_synced": False,
            },
        ):
            response = client.post(
                f"/api/v1/email-messages/{row.id}/resolve-human",
                json={"decision": "mark_spam"},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["spam_pattern_saved"] is True
    assert row.status == ProcessingStatus.SPAM.value
    assert row.is_spam is True
    repo.clear_xml_document.assert_called_once_with(row)


def test_mark_not_spam_on_awaiting_human_reprocesses():
    row = _email_row(status=ProcessingStatus.AWAITING_HUMAN.value)
    row.is_spam = False
    client = TestClient(app)
    task = MagicMock(id="task-reprocess")

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.reprocess_message_task") as reprocess_task:
            with patch(
                "agent_pochta.api.app.learn_from_not_spam",
                return_value={
                    "spam_pattern_removed": False,
                    "removed_count": 0,
                    "antipattern_saved": True,
                    "antipattern_id": "ap1",
                    "antipattern_qdrant_synced": False,
                },
            ) as learn_not_spam:
                reprocess_task.delay.return_value = task
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={"decision": "mark_not_spam"},
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reprocessing"
    assert payload["task_id"] == "task-reprocess"
    assert payload["antipattern_saved"] is True
    assert row.status == ProcessingStatus.PROCESSING.value
    assert row.is_spam is False
    learn_not_spam.assert_called_once()
    reprocess_task.delay.assert_called_once_with(str(row.id))
    repo.clear_xml_document.assert_called_once_with(row)


def test_approve_routing_rejected_for_spam():
    row = _email_row(status=ProcessingStatus.SPAM.value)
    client = TestClient(app)

    with _mock_repo(row):
        response = client.post(
            f"/api/v1/email-messages/{row.id}/resolve-human",
            json={
                "decision": "approve_routing",
                "department_id": "00-000002",
                "department_name": "Бухгалтерия",
            },
        )

    assert response.status_code == 400
    assert "spam" in response.json()["detail"].lower()


def test_list_organizations_endpoint():
    client = TestClient(app)
    response = client.get("/api/v1/organizations")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 6
    assert payload[0]["id"] == "НП"
    assert "Турбулентность" in payload[0]["name"]


def test_approve_routing_rejects_unknown_organization():
    row = _email_row(status=ProcessingStatus.DONE.value)
    client = TestClient(app)

    with _mock_repo(row):
        response = client.post(
            f"/api/v1/email-messages/{row.id}/resolve-human",
            json={
                "decision": "approve_routing",
                "department_id": "00-000002",
                "department_name": "Бухгалтерия",
                "organization": "XX",
            },
        )

    assert response.status_code == 400
    assert "organization" in response.json()["detail"].lower()


def test_approve_routing_accepts_manual_partner_name():
    row = _email_row(status=ProcessingStatus.AWAITING_HUMAN.value)
    client = TestClient(app)
    task = MagicMock(id="task-partner")

    with _mock_repo(row) as (repo, session):
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch("agent_pochta.api.app.CatalogRepository") as catalog_cls:
                with patch(
                    "agent_pochta.api.app.learn_from_routing_correction",
                    return_value={"correction_saved": True},
                ):
                    continue_task.delay.return_value = task
                    catalog_cls.return_value.upsert_manual_contractor.return_value = MagicMock()
                    response = client.post(
                        f"/api/v1/email-messages/{row.id}/resolve-human",
                        json={
                            "decision": "approve_routing",
                            "department_id": "00-000002",
                            "department_name": "Бухгалтерия",
                            "partner_name": "ООО «Ромашка»",
                        },
                    )

    assert response.status_code == 200
    repo.apply_human_resolution.assert_called_once()
    kwargs = repo.apply_human_resolution.call_args.kwargs
    assert kwargs["partner_name"] == "ООО «Ромашка»"
    assert kwargs.get("contractor_id") is None
    repo.rebuild_xml_after_human_correction.assert_called_once()
    rebuild_kwargs = repo.rebuild_xml_after_human_correction.call_args.kwargs
    assert rebuild_kwargs["partner_override"] == "ООО «Ромашка»"
    catalog_cls.return_value.upsert_manual_contractor.assert_called_once()
