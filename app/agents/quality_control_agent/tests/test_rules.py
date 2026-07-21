"""Tests for quality control rules registry and scrap decisions."""

from __future__ import annotations

from app.agents.quality_control_agent.rules_registry import (
    SCRAP_THRESHOLD_PCT,
    build_mandatory_documents,
    evaluate_document_completeness,
    evaluate_scrap_decision,
    normalize_category,
)


def test_normalize_category_aliases() -> None:
    assert normalize_category("Электроника") == "electronics"
    assert normalize_category("кабель силовой") == "cable"
    assert normalize_category(None) == "other"


def test_mandatory_docs_electronics() -> None:
    docs = build_mandatory_documents("electronics", ["certificate"])
    assert len(docs) == 3
    assert any(d.doc_type == "certificate" and d.present for d in docs)
    assert any(d.doc_type == "origin_confirmation" and not d.present for d in docs)


def test_document_findings() -> None:
    findings = evaluate_document_completeness("metal", [], "case-1")
    assert findings
    assert findings[0].severity == "critical"


def test_scrap_ge15() -> None:
    result = evaluate_scrap_decision(SCRAP_THRESHOLD_PCT)
    assert result["disposition"] == "forbid"
    assert result["require_zdk"] is True
    assert result["require_second_sample"] is False


def test_scrap_lt15() -> None:
    result = evaluate_scrap_decision(10.0)
    assert result["require_second_sample"] is True
    assert result["require_zdk"] is True


def test_scrap_no_analog() -> None:
    result = evaluate_scrap_decision(0.0, analog_in_nomenclature=False)
    assert result["disposition"] == "forbid"
    assert result["rule_id"] == "QC.SCRAP.NO_ANALOG"
