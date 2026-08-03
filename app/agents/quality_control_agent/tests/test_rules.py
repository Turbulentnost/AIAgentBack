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

    # Прил. Б п.5: металлопрокат — 100%
    rule = build_sample_rule(
        "metal",
        lot_qty=100,
        presentation_ref="PRES-42",
        nomenclature_ref="NOM-1",
        supplier_ref="SUP-9",
    )
    assert rule.sample_size == 100
    assert rule.sample_pct == 100.0
    assert rule.sample_basis == "100pct"
    assert rule.lot_qty == 100
    assert rule.presentation_ref == "PRES-42"
    assert "PRES-42" in rule.sample_note


def test_sample_rule_max_rating_one_percent() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule("cable", lot_qty=200, supplier_quality_rating=40)
    assert rule.sample_size == 2
    assert rule.sample_basis == "1pct_rating"


def test_sample_rule_fasteners_tiers_and_each_box() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    # Прил. Б п.7: >100 → 3%; п. 6.6.3 — из каждой коробки
    rule = build_sample_rule("fasteners", lot_qty=500, presentation_ref="BOX-1")
    assert rule.sample_pct == 3.0
    assert rule.sample_size == 15
    assert rule.sample_basis == "3pct"
    assert "коробк" in rule.sample_note.lower()


def test_sample_rule_second_sample() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    rule = build_sample_rule("metal", lot_qty=50, require_second_sample=True)
    assert rule.require_second_sample is True
    assert rule.second_sample_size == rule.sample_size == 50


def test_sample_rule_depends_on_category_pct() -> None:
    from app.agents.quality_control_agent.rules_registry import build_sample_rule

    electronics_small = build_sample_rule("electronics", lot_qty=50)
    electronics_mid = build_sample_rule("electronics", lot_qty=100)
    electronics_large = build_sample_rule("electronics", lot_qty=200)
    drawing = build_sample_rule("drawing_parts", lot_qty=100)
    gaskets = build_sample_rule("gaskets", lot_qty=40)
    pipes = build_sample_rule("pipes", lot_qty=100, supplier_quality_rating=40)
    metal = build_sample_rule("metal", lot_qty=100)
    metal_rated = build_sample_rule("metal", lot_qty=100, supplier_quality_rating=40)

    # Прил. Б п.1: 100 / 50 / 10
    assert electronics_small.sample_pct == 100.0
    assert electronics_small.sample_size == 50
    assert electronics_small.sample_basis == "100pct"
    assert electronics_mid.sample_pct == 50.0
    assert electronics_mid.sample_size == 50
    assert electronics_large.sample_pct == 10.0
    assert electronics_large.sample_size == 20

    # Прил. Б п.3: 100 / 50 / 10 → партия 100 → 50%
    assert drawing.sample_pct == 50.0
    assert drawing.sample_size == 50
    assert drawing.sample_basis == "50pct"

    # Прил. Б п.4 РТИ: 30 / 20 / 10
    assert gaskets.sample_pct == 30.0
    assert gaskets.sample_size == 12

    # трубы: 100%, без скидки 1% по рейтингу (п. 6.7.4)
    assert pipes.sample_pct == 100.0
    assert pipes.sample_size == 100
    assert pipes.sample_basis == "100pct"

    assert metal.sample_pct == 100.0
    assert metal.sample_basis == "100pct"
    assert metal_rated.sample_basis == "1pct_rating"
    assert metal_rated.sample_pct == 1.0
