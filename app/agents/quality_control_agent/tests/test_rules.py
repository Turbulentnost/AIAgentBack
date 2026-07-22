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


def test_sample_rule_for_delivery_lot() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule(
        "metal",
        lot_qty=100,
        presentation_ref="PRES-42",
        nomenclature_ref="NOM-1",
        supplier_ref="SUP-9",
    )
    assert rule.sample_size == 10
    assert rule.sample_pct == 10.0
    assert rule.sample_basis == "10pct"
    assert rule.lot_qty == 100
    assert rule.presentation_ref == "PRES-42"
    assert "PRES-42" in rule.sample_note


def test_sample_rule_max_rating_one_percent() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule("cable", lot_qty=200, supplier_quality_rating=40)
    assert rule.sample_size == 2
    assert rule.sample_basis == "1pct_rating"


def test_sample_rule_fasteners_per_package() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule("fasteners", lot_qty=500, presentation_ref="BOX-1")
    assert rule.sample_size is None
    assert rule.sample_basis == "per_package"
    assert "тары" in rule.sample_note.lower() or "коробки" in rule.sample_note.lower()


def test_sample_rule_second_sample() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule("electronics", lot_qty=50, require_second_sample=True)
    assert rule.require_second_sample is True
    assert rule.second_sample_size == rule.sample_size == 5

