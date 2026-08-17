"""Классификация изделий и расчёт полуфабрикатов на дашборде обеспеченности."""

from __future__ import annotations

from app.agents.document_analysis_agent.coverage_dashboard import (
    _product_assemblable_from_material_lines,
    _product_has_any_material_supply,
    _product_status_for_monthly,
)


def test_assemblable_limited_by_bottleneck_material():
    materials = [
        {"name": "Болт", "plan": 1000.0, "stock": 500.0, "expected": 0.0, "shortage": 500.0},
        {"name": "Гайка", "plan": 1000.0, "stock": 0.0, "expected": 0.0, "shortage": 1000.0},
    ]
    assert _product_assemblable_from_material_lines(materials, plan=1000.0) == 0.0


def test_assemblable_counts_buildable_units():
    materials = [
        {"name": "Болт", "plan": 1000.0, "stock": 500.0, "expected": 0.0, "shortage": 500.0},
        {"name": "Гайка", "plan": 2000.0, "stock": 900.0, "expected": 0.0, "shortage": 1100.0},
    ]
    assert _product_assemblable_from_material_lines(materials, plan=1000.0) == 450.0


def test_optional_materials_still_limit_assemblable():
    materials = [
        {"name": "Болт", "plan": 100.0, "stock": 100.0, "expected": 0.0, "shortage": 0.0},
        {
            "name": "Антифлюс",
            "plan": 25.0,
            "stock": 0.0,
            "expected": 0.0,
            "shortage": 25.0,
            "optional": True,
        },
    ]
    assert _product_assemblable_from_material_lines(materials, plan=100.0) == 0.0


def test_product_status_yellow_when_any_material_has_supply():
    materials = [
        {"name": "Болт", "plan": 100.0, "stock": 0.0, "expected": 0.0, "shortage": 100.0},
        {"name": "Антифлюс", "plan": 25.0, "stock": 12.39, "expected": 0.0, "shortage": 12.61},
    ]
    assert _product_has_any_material_supply(materials) is True
    status = _product_status_for_monthly(
        plan=1000.0,
        strict_covered=0.0,
        materials=materials,
        assemblable_qty=0.0,
    )
    assert status == "yellow"


def test_product_status_green_when_assemblable_covers_plan():
    status = _product_status_for_monthly(
        plan=100.0,
        strict_covered=0.0,
        materials=[],
        assemblable_qty=100.0,
    )
    assert status == "green"
