"""Тесты коррекций полей 1С (партнёр / организация) в routing_rules.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.engine import RouteEngine, reset_route_engine
from agent_pochta.routing.onec_corrections import (
    empty_onec_corrections,
    ensure_onec_corrections_section,
    find_onec_correction_match,
    load_onec_corrections,
    save_onec_correction,
)
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.services.llm_analyze import resolve_partner_name
from agent_pochta.services.routing_departments import load_routing_rules


def _minimal_rules() -> dict:
    return {
        "version": "1.0",
        "reserve_code": "00-000066",
        "reserve_name": "Управление делами",
        "spam_code": "00-999999",
        "email_aliases": {},
        "email_keyword_rules": [
            {
                "keyword": "jurist",
                "code": "00-000044",
                "name": "Юридический отдел",
                "direction": "ПР",
            }
        ],
        "exact_email_rules": [],
        "content_rules": [],
        "organization_keywords": {
            "АЛ": ["алмаз"],
            "МГ": ["метрогаз"],
        },
        "department_names": {
            "00-000044": "Юридический отдел",
            "00-000066": "Управление делами",
        },
        "onec_corrections": empty_onec_corrections(),
    }


@pytest.fixture
def rules_file(tmp_path: Path) -> Path:
    path = tmp_path / "routing_rules.json"
    path.write_text(json.dumps(_minimal_rules(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_ensure_onec_section_on_legacy_rules(tmp_path: Path):
    legacy = _minimal_rules()
    del legacy["onec_corrections"]
    path = tmp_path / "legacy_rules.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    rules = load_routing_rules(path)
    ensure_onec_corrections_section(rules)
    assert "onec_corrections" in rules
    assert rules["onec_corrections"]["entries"] == []


def test_route_engine_ignores_onec_corrections_key(rules_file: Path, monkeypatch: pytest.MonkeyPatch):
    def _empty_deterministic_rules(path: str = "") -> dict:
        return {}

    _empty_deterministic_rules.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "agent_pochta.routing.deterministic_sales.load_deterministic_sales_rules",
        _empty_deterministic_rules,
    )
    engine = RouteEngine.load(rules_file)
    email = EmailMessage(
        message_id="<onec-ignore@example>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Общий запрос",
        body_text="Здравствуйте",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    decision = engine.route(email, combined_text=email.body_text)
    assert decision.services[0].code == "00-000044"
    assert "onec_corrections" in engine.rules


def test_save_and_match_onec_correction(rules_file: Path):
    entry = save_onec_correction(
        partner="ООО «Ромашка»",
        organization="АЛ",
        sender_email="vendor@example.com",
        recipient="jurist@turbo-don.ru",
        subject="Договор поставки",
        body="Просим согласовать договор поставки с Алмаз.",
        department_id="00-000044",
        department_name="Юридический отдел",
        path=rules_file,
    )
    assert entry is not None
    assert entry["partner"] == "ООО «Ромашка»"
    assert entry["organization"] == "АЛ"

    store = load_onec_corrections(rules_file)
    assert len(store["entries"]) == 1

    matched = find_onec_correction_match(
        recipient="jurist@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Договор поставки",
        body="Просим согласовать договор поставки.",
        path=rules_file,
    )
    assert matched is not None
    assert matched["partner"] == "ООО «Ромашка»"
    assert matched["organization"] == "АЛ"


def test_save_onec_skips_empty_fields(rules_file: Path):
    assert (
        save_onec_correction(
            partner=None,
            organization=None,
            sender_email="a@b.ru",
            recipient="jurist@turbo-don.ru",
            subject="x",
            body="y",
            path=rules_file,
        )
        is None
    )
    assert load_onec_corrections(rules_file)["entries"] == []


def test_engine_applies_onec_organization_and_partner(rules_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTING_RULES_PATH", str(rules_file))
    from agent_pochta.config import reset_settings

    reset_settings()
    reset_route_engine()

    save_onec_correction(
        partner="ООО «Метрогаз Партнёр»",
        organization="МГ",
        sender_email="partner@metro.ru",
        recipient="jurist@turbo-don.ru",
        subject="Счёт на оплату",
        body="Направляем счёт.",
        department_id="00-000044",
        department_name="Юридический отдел",
        path=rules_file,
    )
    reset_route_engine()

    engine = RouteEngine.load(rules_file)
    email = EmailMessage(
        message_id="<onec-apply@example>",
        mailbox="info@turbo-don.ru",
        sender_email="partner@metro.ru",
        subject="Счёт на оплату",
        body_text="Направляем счёт.",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    decision = engine.route(email, combined_text=email.body_text)
    assert decision.organization == "МГ"
    assert decision.partner == "ООО «Метрогаз Партнёр»"


def test_resolve_partner_prefers_learned_partner():
    email = EmailMessage(
        message_id="<learned@example>",
        mailbox="info@turbo-don.ru",
        sender_email="sales@lan-service.ru",
        subject="КП",
        body_text="С уважением, Ирина Петрова",
        received_at=datetime.now(timezone.utc),
    )
    assert (
        resolve_partner_name(
            llm_partner="Ирина Петрова",
            rag_partner=None,
            email=email,
            body_text=email.body_text,
            learned_partner="ООО «ЛАН-Сервис»",
        )
        == "ООО «ЛАН-Сервис»"
    )


def test_learn_from_routing_correction_saves_onec(rules_file: Path, tmp_path: Path):
    from agent_pochta.routing.learning import learn_from_routing_correction

    corrections = tmp_path / "routing_corrections.json"
    corrections.write_text('{"version": "1.0", "entries": []}\n', encoding="utf-8")

    result = learn_from_routing_correction(
        message_id="<learn-onec@example>",
        sender_email="vendor@example.com",
        recipient="jurist@turbo-don.ru",
        subject="Акт",
        body="Акт сверки",
        department_id="00-000044",
        department_name="Юридический отдел",
        partner="ООО «Ромашка»",
        organization="АЛ",
        path=corrections,
        routing_rules_path=rules_file,
        session=MagicMock(),
    )
    assert result["correction_saved"] is True
    assert result["onec_correction_saved"] is True
    assert result["onec_partner"] == "ООО «Ромашка»"
    assert result["onec_organization"] == "АЛ"
    assert result["onec_qdrant_synced"] is False
    assert len(load_onec_corrections(rules_file)["entries"]) == 1


def test_save_onec_correction_upserts_qdrant(
    rules_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.services.onec_corrections_rag_qdrant.upsert_onec_correction_entry"
    ) as upsert_mock:
        entry = save_onec_correction(
            partner="ООО «Ромашка»",
            organization="АЛ",
            sender_email="vendor@example.com",
            recipient="jurist@turbo-don.ru",
            subject="Договор",
            body="Текст договора",
            department_id="00-000044",
            department_name="Юридический отдел",
            path=rules_file,
        )

    assert entry is not None
    assert entry["qdrant_synced"] is True
    upsert_mock.assert_called_once()
    assert upsert_mock.call_args.args[0] == "http://qdrant:6333"
    assert upsert_mock.call_args.args[1]["partner"] == "ООО «Ромашка»"


def test_learn_from_routing_correction_reports_onec_qdrant(
    rules_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from agent_pochta.routing.learning import learn_from_routing_correction

    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    corrections = tmp_path / "routing_corrections.json"
    corrections.write_text('{"version": "1.0", "entries": []}\n', encoding="utf-8")

    with patch(
        "agent_pochta.services.rag_qdrant.append_department_keywords",
        return_value={"updated": True, "keywords_added": 1, "added_keywords": ["акт"]},
    ):
        with patch(
            "agent_pochta.services.onec_corrections_rag_qdrant.upsert_onec_correction_entry"
        ) as upsert_mock:
            result = learn_from_routing_correction(
                message_id="<learn-onec-qd@example>",
                sender_email="vendor@example.com",
                recipient="jurist@turbo-don.ru",
                subject="Акт",
                body="Акт сверки",
                department_id="00-000044",
                department_name="Юридический отдел",
                partner="ООО «Ромашка»",
                organization="АЛ",
                path=corrections,
                routing_rules_path=rules_file,
                session=MagicMock(),
            )

    assert result["onec_qdrant_synced"] is True
    upsert_mock.assert_called_once()


def test_enrich_hitl_contractor_in_qdrant(monkeypatch: pytest.MonkeyPatch):
    from agent_pochta.routing.learning import enrich_hitl_contractor_in_qdrant

    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.services.rag_qdrant.upsert_contractors_merge",
        return_value=1,
    ) as upsert_mock:
        result = enrich_hitl_contractor_in_qdrant(
            contractor_id="email:vendor@example.com",
            name="ООО «Ромашка»",
            email="vendor@example.com",
            department_code="00-000044",
        )

    assert result["upserted"] == 1
    upsert_mock.assert_called_once()
    contractors = upsert_mock.call_args.args[1]
    assert contractors[0].name == "ООО «Ромашка»"
    assert contractors[0].emails == ["vendor@example.com"]


def test_approve_routing_passes_partner_and_organization_to_learning():
    from contextlib import contextmanager
    import uuid

    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<approve-onec@example>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Договор",
        status=ProcessingStatus.AWAITING_HUMAN.value,
        received_at=received_at,
        raw_payload_json=json.dumps(
            {
                "message_id": "<approve-onec@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "vendor@example.com",
                "subject": "Договор",
                "body_text": "Текст",
                "received_at": received_at.isoformat(),
                "routing_recipient": "jurist@turbo-don.ru",
            },
            ensure_ascii=False,
        ),
    )
    client = TestClient(app)

    @contextmanager
    def _mock_repo():
        repo = MagicMock()
        repo.get_by_id.return_value = row
        repo.load_email_from_row.return_value = EmailMessage.model_validate(
            json.loads(row.raw_payload_json)
        )
        repo.learning_text_from_row.return_value = "Текст"
        repo.apply_human_resolution.return_value = row
        repo.rebuild_xml_after_human_correction.return_value = "<document></document>"
        session = MagicMock()
        session_factory = MagicMock()
        session_factory.return_value.__enter__.return_value = session
        with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
            with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
                yield repo

    with _mock_repo():
        with patch("agent_pochta.api.app.continue_after_human_task") as continue_task:
            with patch(
                "agent_pochta.api.app.learn_from_routing_correction",
                return_value={"correction_saved": True, "onec_correction_saved": True},
            ) as learn:
                with patch("agent_pochta.api.app.CatalogRepository"):
                    continue_task.delay.return_value = MagicMock(id="t1")
                    response = client.post(
                        f"/api/v1/email-messages/{row.id}/resolve-human",
                        json={
                            "decision": "approve_routing",
                            "department_id": "00-000044",
                            "department_name": "Юридический отдел",
                            "partner_name": "ООО «Ромашка»",
                            "organization": "АЛ",
                        },
                    )

    assert response.status_code == 200
    learn.assert_called_once()
    kwargs = learn.call_args.kwargs
    assert kwargs["partner"] == "ООО «Ромашка»"
    assert kwargs["organization"] == "АЛ"
