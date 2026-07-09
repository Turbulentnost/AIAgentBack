"""Тесты импорта RAG-каталога."""

from __future__ import annotations

from pathlib import Path

from agent_pochta.services.rag_import import (
    load_catalog_from_json,
    load_department_keywords,
    merge_department_keywords,
    parse_contractor,
    parse_department,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "rag_catalog.example.json"


def test_parse_odata_style_contractor():
    row = {
        "Ref_Key": "uuid-1",
        "Description": "ООО Тест",
        "Email": "test@client.ru",
        "DepartmentCodes": "SALES,FINANCE",
    }
    c = parse_contractor(row)
    assert c is not None
    assert c.contractor_id == "uuid-1"
    assert c.emails == ["test@client.ru"]
    assert c.department_codes == ["SALES", "FINANCE"]


def test_load_example_catalog():
    contractors, departments = load_catalog_from_json(CATALOG)
    assert len(contractors) >= 3
    assert len(departments) >= 4
    assert any(c.name.startswith("ООО «Промтехснаб»") for c in contractors)


def test_merge_keywords():
    dept = parse_department(
        {"department_id": "SALES", "department_name": "Отдел продаж", "keywords": ["заказ"]}
    )
    assert dept is not None
    extra = load_department_keywords(ROOT / "data" / "rag_department_keywords.json")
    merged = merge_department_keywords([dept], extra)[0]
    assert "заказ" in merged.keywords
    assert "счёт" in merged.keywords or "счет" in merged.keywords
