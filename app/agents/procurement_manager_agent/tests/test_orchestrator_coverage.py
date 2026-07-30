from __future__ import annotations

from app.agents.procurement_manager_agent.fulfillment import derive_fulfillment_status
from app.agents.procurement_manager_agent.orchestrator_coverage import (
    definite_supplier_orders_from_snapshot,
    merge_order_coverage_with_orchestrator,
    search_fields_from_coverage,
)


def test_merge_supplier_order_overrides_bank_none() -> None:
    bank = {
        "tone": "uncovered",
        "label": "Полностью необеспечен",
        "covered_count": 0,
        "positions_count": 1,
        "uncovered_positions_count": 1,
        "has_suppliers": False,
        "lines": [
            {
                "line_id": "1",
                "nomenclature_id": "nom-1",
                "nomenclature_name": "Полукожух",
                "needed_quantity": "4",
                "covered_quantity": "0",
                "deficit_quantity": "4",
                "from_warehouse": "0",
                "from_supplier": "0",
                "coverage_source": "none",
                "coverage_source_label": "нет",
                "tone": "uncovered",
            }
        ],
    }
    metadata = {
        "material_order_coverage": {
            "coverage_status": "full",
            "positions": [
                {
                    "line_id": "1",
                    "nomenclature_id": "nom-1",
                    "nomenclature_name": "Полукожух",
                    "requested_quantity": "4",
                    "covered_quantity": "4",
                    "supplier_ordered_quantity": "4",
                    "transfer_ordered_quantity": "0",
                    "coverage_source": "supplier_order",
                    "purchasing": True,
                    "supplier_orders": [
                        {
                            "supplier_order_1c_ref": "aaa",
                            "supplier_order_number": "НП00-005278",
                            "is_definite": True,
                            "Контрагент_Key": "bbbb",
                        }
                    ],
                }
            ],
            "supplier_orders": [
                {
                    "supplier_order_1c_ref": "aaa",
                    "supplier_order_number": "НП00-005278",
                    "is_definite": True,
                    "Контрагент_Key": "bbbb",
                }
            ],
        }
    }
    merged = merge_order_coverage_with_orchestrator(bank, metadata=metadata)
    assert merged["lines"][0]["coverage_source"] == "supplier_order"
    assert merged["lines"][0]["coverage_source_label"] == "заказ поставщику"
    assert merged["has_suppliers"] is True
    assert merged["orchestrator_coverage_status"] == "full"
    assert merged["supplier_orders"][0]["supplier_order_number"] == "НП00-005278"


def test_conditional_parent_excluded_when_definite_child_exists() -> None:
    snapshot = {
        "supplier_orders": [
            {
                "supplier_order_1c_ref": "parent-cond",
                "supplier_order_number": "ЗП-УСЛ",
                "is_definite": False,
            },
            {
                "supplier_order_1c_ref": "child-def",
                "supplier_order_number": "ЗП-DEF",
                "is_definite": True,
                "Контрагент_Key": "cp-1",
                "basisResolution": {
                    "chain": [{"supplierOrderRef": "parent-cond"}]
                },
            },
        ]
    }
    orders = definite_supplier_orders_from_snapshot(snapshot)
    numbers = {item["supplier_order_number"] for item in orders}
    assert "ЗП-DEF" in numbers
    assert "ЗП-УСЛ" not in numbers


def test_fulfillment_delivery_from_1c_orders_without_po_drafts() -> None:
    status = derive_fulfillment_status(
        case_status="agent_waiting",
        workspace={
            "supplier_orders": [
                {
                    "supplier_order_1c_ref": "aaa",
                    "supplier_order_number": "НП00-005278",
                    "is_definite": True,
                }
            ]
        },
    )
    assert status == "delivery"

    ordered = derive_fulfillment_status(
        case_status="ordered",
        workspace={"purchase_order_drafts": []},
    )
    assert ordered == "delivery"


def test_search_fields_include_nomenclature_and_po_numbers() -> None:
    fields = search_fields_from_coverage(
        source_number="НП00-001119",
        order_coverage={
            "lines": [
                {
                    "nomenclature_id": "nom-1",
                    "nomenclature_name": "Полукожух UFL",
                }
            ],
            "supplier_orders": [
                {"supplier_order_number": "НП00-005278"},
            ],
        },
    )
    assert "нп00-001119" in fields["search_text"]
    assert "полукожух ufl" in fields["search_text"]
    assert "нп00-005278" in fields["search_text"]
    assert fields["supplier_order_numbers"] == ["НП00-005278"]


def test_transfer_only_does_not_require_supplier_offerings() -> None:
    bank = {
        "tone": "uncovered",
        "label": "Полностью необеспечен",
        "lines": [
            {
                "line_id": "2",
                "nomenclature_id": "nom-t",
                "needed_quantity": "2",
                "covered_quantity": "0",
                "from_warehouse": "0",
                "from_supplier": "0",
                "coverage_source": "none",
                "tone": "uncovered",
            }
        ],
    }
    metadata = {
        "material_order_coverage": {
            "coverage_status": "full",
            "positions": [
                {
                    "line_id": "2",
                    "nomenclature_id": "nom-t",
                    "requested_quantity": "2",
                    "covered_quantity": "2",
                    "transfer_ordered_quantity": "2",
                    "supplier_ordered_quantity": "0",
                    "coverage_source": "transfer_order",
                    "transferring": True,
                }
            ],
            "supplier_orders": [],
        }
    }
    merged = merge_order_coverage_with_orchestrator(bank, metadata=metadata)
    assert merged["lines"][0]["coverage_source"] == "transfer_order"
    assert merged["lines"][0]["tone"] == "ready"
