"""Тесты LLM-извлечения keywords для routing_corrections."""

from __future__ import annotations

import json

from agent_pochta.routing.corrections_llm import (
    KEYWORD_TARGET_MAX,
    KEYWORD_TARGET_MIN,
    build_keyword_extraction_user_payload,
    finalize_llm_keywords,
    routing_rules_context,
)


def test_build_keyword_extraction_user_payload_json():
    payload = build_keyword_extraction_user_payload(
        subject="Re: Заказ 12345 на расходомеры",
        body="Просим согласовать спецификацию расходомера turbo-f.",
        sender_email="client@example.ru",
        recipient="uk_omto4@turbo-don.ru",
        department_id="00-000001",
        department_name="ОМТО",
        original_department_id="00-000044",
        original_department_name="Юридический отдел",
        current_keywords=["заказ 12345"],
    )
    data = json.loads(payload)
    assert data["target_department"]["department_name"] == "ОМТО"
    assert data["original_department"]["changed"] is True
    assert "body_excerpt" in data
    assert "few_shot_examples" in data


def test_finalize_llm_keywords_merges_subject_and_local_part():
    keywords = finalize_llm_keywords(
        [
            "расходомер turbo-f",
            "согласовать спецификацию",
            "заказ 12345",
            "turbo",
            "добрый день",
            "uk_omto4",
        ],
        subject="Re: Заказ 12345 на расходомеры",
        recipient="uk_omto4@turbo-don.ru",
    )
    assert "uk_omto4" in keywords
    assert any("заказ" in k for k in keywords)
    assert "turbo" not in keywords
    assert "добрый день" not in keywords
    assert len(keywords) >= 4
    assert len(keywords) <= KEYWORD_TARGET_MAX


def test_finalize_llm_keywords_dedupes_substrings():
    keywords = finalize_llm_keywords(
        [
            "акт сверки за квартал",
            "акт сверки",
            "подписать акт сверки",
            "бухгалтерия расчеты",
            "сверка взаиморасчетов",
            "квартальный отчет",
        ],
        subject="Акт сверки за квартал",
        recipient="buh@turbo-don.ru",
    )
    lowered = [k.lower() for k in keywords]
    assert not any(
        lowered[i] != lowered[j] and lowered[i] in lowered[j]
        for i in range(len(lowered))
        for j in range(len(lowered))
    )


def test_finalize_llm_keywords_fallback_when_too_few():
    keywords = finalize_llm_keywords(
        ["одно"],
        subject="Счёт на оплату оборудования",
        recipient="sales@turbo-don.ru",
        department_id="00-000155",
    )
    assert len(keywords) >= 3
    assert "sales" in keywords


def test_routing_rules_context_returns_list():
    ctx = routing_rules_context(
        department_id="00-000155",
        recipient="sales@turbo-don.ru",
        limit=3,
    )
    assert isinstance(ctx, list)
    assert len(ctx) <= 3


def test_extract_correction_keywords_llm_fallback_without_gateway(monkeypatch):
    from agent_pochta.routing.corrections_llm import extract_correction_keywords_llm

    monkeypatch.setattr(
        "agent_pochta.services.build_container",
        lambda settings: type("C", (), {"llm": object()})(),
    )
    keywords, source = extract_correction_keywords_llm(
        "Re: Заказ 12345 на расходомеры",
        "Просим согласовать спецификацию расходомера.",
        sender_email="client@example.ru",
        recipient="uk_omto4@turbo-don.ru",
        department_id="00-000001",
        department_name="ОМТО",
    )
    assert source == "deterministic_fallback"
    assert len(keywords) >= 2
    assert "uk_omto4" in keywords
