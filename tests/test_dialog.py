"""Категория «Диалог»: dormant-переписка и активация по словам-действиям."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing.dialog import (
    DialogMode,
    build_dialog_dormant_theme,
    classify_dialog,
    is_dialog_email,
    load_dialog_rules,
    reset_dialog_rules_cache,
)
from agent_pochta.routing.engine import rebuild_decision_xml
from agent_pochta.routing.models import RoutingDecision, ServiceRoute
from agent_pochta.schemas import EmailMessage, ProcessingStatus


@pytest.fixture(autouse=True)
def _clear_dialog_cache():
    reset_dialog_rules_cache()
    yield
    reset_dialog_rules_cache()


def _email(**overrides) -> EmailMessage:
    values = {
        "message_id": "<dialog@test>",
        "mailbox": "info@turbo-don.ru",
        "sender_email": "partner@example.ru",
        "subject": "Re: сроки поставки",
        "body_text": "",
        "received_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return EmailMessage(**values)


def test_classify_dormant_dialog_with_thread_and_exchange():
    body = (
        "Спасибо, принято.\n\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Уточните срок отгрузки\n"
        "> Ждём ответ"
    )
    result = classify_dialog(
        subject="Re: сроки поставки",
        body=body,
        sender_email="partner@example.ru",
    )

    assert result.is_dialog is True
    assert result.mode == DialogMode.DORMANT
    assert result.register_erp is False
    assert result.process_type == "ознакомление"
    assert result.theme_action == "Диалог"


def test_classify_activated_dialog_on_action_words():
    body = (
        "Re: документы\n\n"
        "Просим направить акт сверки до пятницы.\n"
        "08.07.2026, info@turbo-don.ru пишет:\n"
        "> Добрый день"
    )
    result = classify_dialog(
        subject="Re: документы",
        body=body,
        sender_email="partner@example.ru",
    )

    assert result.is_dialog is True
    assert result.mode == DialogMode.ACTIVATED
    assert result.register_erp is True
    assert result.activation_markers
    assert result.process_type in {"рассмотрение", "исполнение"}


def test_classify_skips_without_thread_markers():
    result = classify_dialog(
        subject="Счёт на оплату",
        body="Просим оплатить счёт во вложении.",
        sender_email="partner@example.ru",
    )

    assert result.is_dialog is False


def test_build_dialog_dormant_theme():
    theme = build_dialog_dormant_theme("Re: сроки поставки")
    assert theme.startswith("Диалог:")
    assert "сроки поставки" in theme.lower()


def test_dialog_xml_contains_dialog_mode_tag():
    email = _email(subject="Re: уточнение")
    decision = RoutingDecision(
        organization="НП",
        direction="КС",
        process="ознакомление",
        services=[
            ServiceRoute(
                code="00-000066",
                name="Управление делами",
                process="ознакомление",
            )
        ],
        theme=build_dialog_dormant_theme("Re: уточнение"),
        dialog_mode="dormant",
        match_source="dialog_dormant",
    )
    rebuilt = rebuild_decision_xml(
        email,
        decision,
        recipient="info@turbo-don.ru",
    )

    assert rebuilt.xml_document
    assert "<dialog_mode>dormant</dialog_mode>" in rebuilt.xml_document
    assert "Диалог:" in rebuilt.xml_document


def test_processing_status_dialog_enum():
    assert ProcessingStatus.DIALOG.value == "dialog"


def test_dialog_rules_file_loads():
    rules = load_dialog_rules()
    assert rules.get("enabled") is True
    assert "activation_markers" in rules
    assert "dormant_markers" in rules
    assert "company_thread_signals" in rules
    assert "body_top" in rules


def test_is_dialog_email_strict_criteria():
    body = (
        "Спасибо, принято.\n\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Уточните срок отгрузки\n"
        "С уважением, ООО НПО «Турбулентность-ДОН»"
    )
    subject = "Re: сроки поставки"
    full_body = (
        f"ООО НПО «Турбулентность-ДОН»\n{body}\n"
        "ПАО «Газпром» — партнёр\n"
        "NPO Turbulentnost-DON"
    )
    assert is_dialog_email(subject, full_body) is True


def test_is_dialog_email_false_without_company_repeats():
    body = (
        "Спасибо, принято.\n\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Уточните срок отгрузки"
    )
    assert is_dialog_email("Re: сроки", body) is False


def test_is_dialog_email_false_with_action_at_top():
    body = (
        "Просим направить акт сверки до пятницы.\n\n"
        "ООО НПО «Турбулентность-ДОН»\n"
        "ООО НПО «Турбулентность-ДОН»\n"
        "10.07.2026, info@turbo-don.ru пишет:\n"
        "> Добрый день"
    )
    assert is_dialog_email("Re: документы", body) is False


def test_is_dialog_email_false_without_thread_subject():
    body = "Турбулентность-Дон\nТурбулентность-Дон\nСпасибо"
    assert is_dialog_email("Сроки поставки", body) is False


def test_classify_dormant_via_company_name_repeats():
    body = (
        "Спасибо, принято.\n\n"
        "С уважением, ООО НПО «Турбулентность-ДОН»\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Уточните срок\n"
        "ООО НПО «Турбулентность-ДОН» — ответ"
    )
    result = classify_dialog(
        subject="Re: сроки поставки",
        body=body,
        sender_email="partner@example.ru",
    )
    assert result.is_dialog is True
    assert result.mode == DialogMode.DORMANT
