"""Smoke-тесты графа на заглушках (без внешней инфраструктуры)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.graph import build_graph
from agent_pochta.schemas import EmailMessage, ProcessingStatus


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<t@example>",
        mailbox="info@turbo-don.ru",
        sender_email="zakaz@romashka.ru",
        subject="Заказ",
        body_text="Просьба выставить счёт на поставку.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return EmailMessage(**base)


@pytest.fixture(scope="module")
def app():
    return build_graph()


def test_happy_path_creates_erp_task(app):
    res = app.invoke({"email": _email()})
    assert res["status"] == ProcessingStatus.DONE
    assert res["erp"].success
    assert res["routing"].department_id == "SALES"
    assert res["trace"][-1] == "finalize"


def test_spam_is_stopped_before_routing(app):
    res = app.invoke({"email": _email(
        message_id="<s@example>",
        sender_email="promo@spam.example",
        subject="Реклама и распродажа",
        body_text="Только сегодня выгодное предложение!",
    )})
    assert res["status"] == ProcessingStatus.SPAM
    assert "route_department" not in res["trace"]


def test_gov_sender_gets_urgent_priority(app):
    res = app.invoke({"email": _email(
        message_id="<g@example>",
        sender_email="info@nalog.gov.ru",
        subject="Требование",
        body_text="Претензия по срокам.",
    )})
    assert res["routing"].priority.value == "urgent"
