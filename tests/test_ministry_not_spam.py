"""Министерства не могут быть спамом — обход фильтра и маршрут к ОД."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.nodes.n2_spam_filter import node_spam_filter
from agent_pochta.rules.ministry_not_spam import (
    check_ministry_not_spam,
    load_ministry_content_patterns,
)
from agent_pochta.rules.spam_learning import save_spam_pattern
from agent_pochta.rules.spam_rules import check_rule_spam
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.spam_tz import check_tz_spam
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.services import build_container


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<ministry-not-spam@example>",
        mailbox="info@turbo-don.ru",
        sender_email="office@minstroy-region.gov.ru",
        subject="Запрос",
        body_text="Обычное деловое письмо.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


@pytest.fixture
def engine():
    return RouteEngine.load()


def test_ministry_content_patterns_loaded():
    patterns = load_ministry_content_patterns()
    assert "министерство" in patterns
    assert "минпром" in patterns
    assert "минпромторг" in patterns


def test_ministry_with_spam_markers_not_spam():
    """Письмо министерства с маркерами Приложения А не должно быть спамом."""
    email = _email(
        subject="Приглашение на семинар",
        body_text="Министерство промышленности приглашает на рабочую встречу.",
    )
    result = check_ministry_not_spam(email)
    assert result is not None
    assert result.is_spam is False
    assert result.rule_hit == "ministry_not_spam"
    assert check_rule_spam(email) is not None  # без bypass было бы спамом


def test_ministry_spam_filter_node_bypasses_rules():
    email = _email(
        subject="Вебинар",
        body_text="Минпромэнерго РО направляет материалы по вебинару для отрасли.",
        list_unsubscribe="mailto:unsub@example.com",
    )
    result = node_spam_filter({"email": email, "trace": []}, build_container())
    assert result.get("status") != ProcessingStatus.SPAM
    spam = result.get("spam")
    assert spam is not None
    assert spam.is_spam is False
    assert spam.rule_hit == "ministry_not_spam"
    assert "ministry_not_spam" in (result.get("trace") or [])


def test_ministry_bypasses_learned_spam(tmp_path, monkeypatch):
    learning_file = tmp_path / "spam_learning_patterns.json"
    learning_file.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    email = _email(
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body_text="Министерство промышленности: приглашаем на вебинар.",
    )
    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Министерство промышленности: приглашаем на вебинар.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )

    result = node_spam_filter({"email": email, "trace": []}, build_container())
    assert result.get("status") != ProcessingStatus.SPAM
    assert result["spam"].rule_hit == "ministry_not_spam"


def test_ministry_bypasses_tz_spam():
    email = _email(
        sender_email="noreply@minprom.gov.ru",
        subject="Уведомление",
        body_text="Министерство промышленности направляет документы.",
    )
    assert check_tz_spam(email) is not None
    assert check_ministry_not_spam(email) is not None
    assert check_ministry_not_spam(email).is_spam is False


def test_ministry_not_routed_to_operational_director_off_info_mailbox(engine):
    decision = route_email(
        _email(
            mailbox="sales@turbo-don.ru",
            routing_recipient="sales@turbo-don.ru",
            subject="Исх. №123",
            body_text="Министерство строительства направляет запрос.",
        ),
        combined_text="Министерство строительства направляет запрос.",
        recipient="sales@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code != "00-000152"
    assert decision.match_source != "institution_operational_director"


def test_non_ministry_spam_still_works():
    email = _email(
        sender_email="promo@spam-offers.xyz",
        subject="Приглашение на семинар",
        body_text="Приглашаем на бесплатный вебинар только сегодня.",
    )
    assert check_ministry_not_spam(email) is None
    result = check_rule_spam(email)
    assert result is not None
    assert result.is_spam is True

    node_result = node_spam_filter({"email": email, "trace": []}, build_container())
    assert node_result.get("status") == ProcessingStatus.SPAM
