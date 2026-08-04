"""Tests for 1C routing corpus utilities."""

from __future__ import annotations

from agent_pochta.services.onec_routing_corpus import (
    build_agent_docs_filter,
    resolve_dept_from_1c_doc,
)


def test_build_agent_docs_filter_contains_date_and_responsible_key() -> None:
    flt = build_agent_docs_filter(since="2026-07-20")
    assert "2026-07-20" in flt
    assert "Ответственный_Key eq guid'" in flt
    assert "Date ge datetime'" in flt


def test_resolve_dept_from_komu_code() -> None:
    doc = {"Кому": "00-000065", "ПодразделениеИсполнитель_Key": "00000000-0000-0000-0000-000000000000"}
    result = resolve_dept_from_1c_doc(doc, code_by_guid={}, name_by_code={"00-000065": "МТО"})
    assert result["department_code"] == "00-000065"
    assert result["department_name"] == "МТО"
    assert result["destination_source"] == "Кому"


def test_resolve_dept_from_executor_guid() -> None:
    guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    doc = {
        "Кому": "",
        "ПодразделениеИсполнитель_Key": guid,
    }
    result = resolve_dept_from_1c_doc(
        doc,
        code_by_guid={guid.lower(): "00-000128"},
        name_by_code={"00-000128": "Продажи"},
    )
    assert result["department_code"] == "00-000128"
    assert result["destination_source"] == "ПодразделениеИсполнитель_Key"


def test_resolve_dept_missing_returns_empty_code() -> None:
    doc = {"Кому": "", "ПодразделениеИсполнитель_Key": "00000000-0000-0000-0000-000000000000"}
    result = resolve_dept_from_1c_doc(doc, code_by_guid={}, name_by_code={})
    assert result["department_code"] == ""
