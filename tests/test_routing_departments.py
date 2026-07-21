"""Тесты сборки RAG-отделов из routing_rules.json."""

from __future__ import annotations

from agent_pochta.schemas import Department
from agent_pochta.services.rag import score_department_keywords
from agent_pochta.services.routing_departments import (
    build_departments_from_rules,
    build_departments_from_structure,
    is_liquidated_department,
    list_active_departments_for_ui,
    load_routing_rules,
    load_tz_emails_by_code,
)


def _search_top(departments: list[Department], text: str, top_k: int = 3) -> list[Department]:
    text_l = text.lower()
    scored: list[tuple[int, Department]] = []
    for department in departments:
        score = sum(1 for keyword in department.keywords if keyword in text_l)
        scored.append((score, department))
    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [dept for score, dept in scored if score > 0] or [dept for _, dept in scored]
    return ranked[:top_k]


def test_build_departments_excludes_spam():
    rules = load_routing_rules()
    departments = build_departments_from_rules(rules)
    codes = {d.department_id for d in departments}
    spam_code = str(rules.get("spam_code", "00-999999"))
    assert spam_code not in codes
    assert "00-000001" in codes
    expected = {str(c) for c in rules.get("department_names", {}) if str(c) != spam_code}
    assert codes == expected


def test_search_act_sverki_returns_buhgalteriya():
    departments = build_departments_from_rules(load_routing_rules())
    top = _search_top(departments, "акт сверки")
    assert top[0].department_id == "00-000002"
    assert top[0].department_name == "Бухгалтерия"


def test_department_ids_are_1c_codes():
    departments = build_departments_from_rules(load_routing_rules())
    for dept in departments:
        assert dept.department_id.startswith("00-")
        assert "акт сверки" in dept.keywords or dept.department_id != "00-000002"


def test_build_departments_from_structure_expands_catalog():
    departments = build_departments_from_structure()
    codes = {d.department_id for d in departments}
    assert len(departments) >= 130
    assert "00-000032" not in codes
    assert "00-999999" not in codes
    assert "00-000002" in codes
    assert "00-000163" in codes
    assert all(d.department_id.startswith("00-") for d in departments)


def test_structure_departments_merge_routing_keywords_and_tz_emails():
    departments = build_departments_from_structure()
    by_id = {d.department_id: d for d in departments}
    buh = by_id["00-000002"]
    assert buh.department_name == "Бухгалтерия"
    assert "акт сверки" in buh.keywords
    assert "almaz_glavbuh@turbo-don.ru" in buh.keywords
    assert "almaz_glavbuh" in buh.keywords


def test_structure_skips_liquidated_and_special_codes():
    departments = build_departments_from_structure()
    codes = {d.department_id for d in departments}
    assert "00-999997" not in codes
    assert "00-999998" not in codes
    assert "00-999999" not in codes
    for dept in departments:
        assert not is_liquidated_department(dept.department_name)


def test_recipient_email_boosts_structure_department():
    departments = build_departments_from_structure()
    buh = next(d for d in departments if d.department_id == "00-000002")
    score = score_department_keywords(
        buh,
        "общий вопрос",
        recipient="almaz_glavbuh@turbo-don.ru",
    )
    assert score >= 3


def test_list_active_departments_for_ui_uses_onec_names():
    departments = list_active_departments_for_ui()
    by_id = {item["id"]: item["name"] for item in departments}
    assert len(departments) == 31
    assert by_id["00-000001"] == "Председатель Совета Директоров"
    assert by_id["00-000065"] == "Отдел МТО"
    assert by_id["00-000002"] == "Бухгалтерия"
    assert by_id["00-000066"] == "Управление делами"
    assert by_id["00-000152"] == "ОПЕРАЦИОННЫЙ ДИРЕКТОР"
    assert by_id["00-000182"] == "Помощник зам. операционного директора"
    assert "ФИНАНСОВЫЙ" in by_id["00-000049"] and "ДИРЕКТОР" in by_id["00-000049"]
    assert "00-999999" not in by_id
    assert "00-000007" not in by_id
    assert "00-000149" not in by_id
    assert "00-000013" not in by_id


def test_list_active_departments_for_ui_explicit_directors_allowed():
    from agent_pochta.services.routing_departments import load_ui_department_allowlist

    allowlist = load_ui_department_allowlist()
    assert "00-000001" in allowlist
    assert "00-000152" in allowlist
    assert "00-000172" in allowlist
    assert "00-000007" not in allowlist
    assert "00-000149" not in allowlist
    for item in list_active_departments_for_ui():
        if item["id"] in {
            "00-000001",
            "00-000152",
            "00-000049",
            "00-000058",
            "00-000080",
            "00-000163",
            "00-000172",
            "00-000040",
            "00-000182",
        }:
            continue
        name_l = item["name"].lower().replace("ё", "е")
        assert "директор" not in name_l, item


def test_schet_routes_to_buh_not_service():
    from agent_pochta.routing import route_email
    from agent_pochta.schemas import EmailMessage
    from datetime import datetime, timezone

    email = EmailMessage(
        message_id="<schet@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="Счёт",
        body_text="Просьба выставить счёт на поставку оборудования.",
        received_at=datetime.now(timezone.utc),
        routing_recipient="info@turbo-don.ru",
    )
    decision = route_email(
        email,
        combined_text="Просьба выставить счёт на поставку оборудования.",
        recipient="info@turbo-don.ru",
    )
    assert decision.services[0].code == "00-000002"
    assert decision.services[0].code != "00-000163"


def test_zamena_alone_does_not_route_to_service():
    from agent_pochta.routing import route_email
    from agent_pochta.schemas import EmailMessage
    from datetime import datetime, timezone

    email = EmailMessage(
        message_id="<zamena@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="Замена",
        body_text="Требуется замена детали в комплекте поставки.",
        received_at=datetime.now(timezone.utc),
        routing_recipient="info@turbo-don.ru",
    )
    decision = route_email(
        email,
        combined_text="Требуется замена детали в комплекте поставки.",
        recipient="info@turbo-don.ru",
    )
    assert decision.services[0].code != "00-000163"


def test_rag_schet_prefers_buh_over_reserve():
    from agent_pochta.routing.recipients import build_routing_search_text
    from agent_pochta.services.rag import score_department_keywords

    departments = build_departments_from_structure()
    buh = next(d for d in departments if d.department_id == "00-000002")
    reserve = next(d for d in departments if d.department_id == "00-000066")
    search = build_routing_search_text(
        recipient="info@turbo-don.ru",
        subject="Счёт",
        body="Просьба выставить счёт на поставку.",
    )
    assert score_department_keywords(buh, search, recipient="info@turbo-don.ru") > score_department_keywords(
        reserve, search, recipient="info@turbo-don.ru"
    )


def test_load_tz_emails_from_enterprise_fixture():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    enterprise = json.loads((root / "data" / "enterprise_positions.json").read_text(encoding="utf-8"))
    emails = load_tz_emails_by_code(enterprise)
    assert "00-000002" in emails
    assert "almaz_glavbuh@turbo-don.ru" in emails["00-000002"]
