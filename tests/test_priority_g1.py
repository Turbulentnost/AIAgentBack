"""Таблица G.1: выбор приоритета, очереди и регистрации в 1С."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.graph import build_graph
from agent_pochta.routing.priority import (
    classify_document_kind,
    clear_priority_rules_cache,
    has_response_obligation,
    select_priority,
)
from agent_pochta.schemas import (
    Contractor,
    EmailMessage,
    Priority,
    ProcessingStatus,
    SenderIdentity,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_priority_rules_cache()
    yield
    clear_priority_rules_cache()


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<priority@example>",
        mailbox="info@turbo-don.ru",
        sender_email="partner@example.com",
        subject="Письмо",
        body_text="Текст",
        received_at=datetime.now(timezone.utc),
        routing_recipient="info@turbo-don.ru",
    )
    base.update(kw)
    return EmailMessage(**base)


@pytest.mark.parametrize(
    ("subject", "body", "kind_id", "priority", "queue_tier", "register_erp"),
    [
        (
            "Предписание Ростехнадзора",
            "Просим устранить нарушения.",
            "supervisory_organs",
            Priority.URGENT,
            1,
            True,
        ),
        (
            "Определение арбитражного суда",
            "Направлено исполнительное письмо.",
            "gov_and_courts",
            Priority.URGENT,
            1,
            True,
        ),
        (
            "Претензия по договору",
            "Направляем досудебную претензию о задолженности.",
            "claims_conflict",
            Priority.HIGH,
            1,
            True,
        ),
        (
            "Проект дополнительного соглашения",
            "Просим согласовать доп. соглашение к договору поставки.",
            "customer_contracts",
            Priority.NORMAL,
            1,
            True,
        ),
        (
            "УПД №15 от 01.07.2026",
            "Направляем УПД и закрывающие документы за июнь.",
            "accounting_second_queue",
            Priority.NORMAL,
            2,
            False,
        ),
        (
            "Акт сверки взаимных расчётов",
            "Просьба подписать акт сверки за 1 полугодие.",
            "accounting_second_queue",
            Priority.NORMAL,
            2,
            False,
        ),
        (
            "Рекламация по расходомеру",
            "Техзапрос: неисправность прибора, нужен гарантийный ремонт.",
            "tech_reclamations",
            Priority.NORMAL,
            1,
            True,
        ),
        (
            "Резюме на вакансию менеджера",
            "Отклик на вакансию, прилагаю резюме.",
            "hr_personnel",
            Priority.NORMAL,
            1,
            True,
        ),
        (
            "Прайс-лист поставщика",
            "Наш прайс на ТМЦ, предлагаем поставку материалов.",
            "supplier_offers",
            Priority.NORMAL,
            1,
            False,
        ),
        (
            "Информация о встрече",
            "Добрый день, направляем общие сведения по проекту.",
            "general_correspondence",
            Priority.NORMAL,
            1,
            True,
        ),
    ],
)
def test_g1_base_priority_by_document_kind(
    subject, body, kind_id, priority, queue_tier, register_erp
):
    kind = classify_document_kind(subject, body)
    assert kind.id == kind_id
    decision = select_priority(subject=subject, body=body, claim=False)
    assert decision.document_kind == kind_id
    assert decision.priority == priority
    assert decision.queue_tier == queue_tier
    assert decision.register_erp is register_erp


def test_g1_obligation_elevates_accounting_to_first_queue():
    subject = "УПД №20"
    body = "Направляем УПД. Срок ответа — 3 рабочих дня, требование подтвердить получение."
    assert has_response_obligation(f"{subject} {body}") is True
    decision = select_priority(subject=subject, body=body, claim=False)
    assert decision.document_kind == "accounting_second_queue"
    assert decision.elevated_by_obligation is True
    assert decision.queue_tier == 1
    assert decision.register_erp is True
    assert decision.priority == Priority.HIGH


def test_g1_gov_sender_type_forces_urgent():
    sender = SenderIdentity(
        found=True,
        contractor=Contractor(
            contractor_id="G1",
            name="ИФНС",
            emails=["info@nalog.gov.ru"],
            department_codes=["00-000044"],
            contractor_type="госорган",
        ),
        is_new_contractor=False,
        allowed_departments=["00-000044"],
    )
    decision = select_priority(
        subject="Уведомление",
        body="Информация для сведения.",
        claim=False,
        sender=sender,
    )
    assert decision.priority == Priority.URGENT
    assert decision.register_erp is True
    assert decision.queue_tier == 1


def test_g1_primary_department_hints():
    assert classify_document_kind("Претензия", "претензия").primary_department_codes[0] == (
        "00-000044"
    )
    assert classify_document_kind(
        "Предписание ГИТ", "государственная инспекция труда"
    ).primary_department_codes[0] == "00-000152"
    assert classify_document_kind("УПД", "универсальный передаточный документ").primary_department_codes[
        0
    ] == "00-000002"
    assert classify_document_kind("Резюме", "резюме соискателя").primary_department_codes[0] == (
        "00-000063"
    )
    assert classify_document_kind(
        "КП поставщика", "коммерческое предложение поставщика материалов"
    ).primary_department_codes[0] == "00-000065"


def test_graph_skips_erp_for_second_queue_accounting():
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                message_id="<upd-skip@example>",
                sender_email="buh@romashka.ru",
                subject="УПД и акт сверки за июнь",
                body_text="Направляем УПД и акт сверки взаимных расчётов для ознакомления.",
                routing_recipient="buh@turbo-don.ru",
            )
        }
    )
    assert res["routing"].document_kind == "accounting_second_queue"
    assert res["routing"].register_erp is False
    assert res["routing"].queue_tier == 2
    assert res["routing"].priority == Priority.NORMAL
    assert (res.get("meta") or {}).get("skip_erp") is True
    assert "create_erp_task" not in res["trace"]
    assert res["status"] == ProcessingStatus.DONE


def test_graph_registers_erp_when_accounting_has_obligation():
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                message_id="<upd-oblig@example>",
                sender_email="buh@romashka.ru",
                subject="УПД — срок ответа 5 дней",
                body_text="Направляем УПД. Срок ответа 5 рабочих дней, требование подписать.",
                routing_recipient="buh@turbo-don.ru",
            )
        }
    )
    assert res["routing"].document_kind == "accounting_second_queue"
    assert res["routing"].register_erp is True
    assert res["routing"].queue_tier == 1
    assert res["routing"].priority == Priority.HIGH
    assert "create_erp_task" in res["trace"]
    assert res["erp"].success
    assert res["erp"].erp_document_number != "SKIP-ERP"
