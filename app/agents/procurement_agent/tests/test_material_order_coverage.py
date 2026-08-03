from __future__ import annotations

from decimal import Decimal

from app.services.material_order_coverage import (
    build_material_order_coverage,
    select_supplier_orders_for_qty,
)

SOURCE_REF = "11111111-1111-1111-1111-111111111111"
PARENT_REF = "22222222-2222-2222-2222-222222222222"
CHILD_REF = "33333333-3333-3333-3333-333333333333"


def _position(
    line_id: str,
    nomenclature_id: str,
    quantity: str,
    characteristic_id: str | None = None,
) -> dict:
    return {
        "line_id": line_id,
        "nomenclature_id": nomenclature_id,
        "nomenclature_name": nomenclature_id,
        "characteristic_id": characteristic_id,
        "quantity": Decimal(quantity),
        "cancelled": False,
    }


def _supplier(
    ref: str,
    *,
    quantity: str,
    definite: bool,
    basis_ref: str = SOURCE_REF,
    basis_type: str = "Document_ЗаказМатериаловВПроизводство",
    characteristic_id: str | None = None,
) -> dict:
    return {
        "ref": ref,
        "number": ref[:4],
        "partnerRef": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" if definite else None,
        "basisResolution": {
            "status": "resolved",
            "sourceRef": SOURCE_REF,
            "sourceType": "production_material_order",
            "chain": [
                {
                    "supplierOrderRef": ref,
                    "ref": basis_ref,
                    "type": basis_type,
                },
                *(
                    [
                        {
                            "supplierOrderRef": PARENT_REF,
                            "ref": SOURCE_REF,
                            "type": "Document_ЗаказМатериаловВПроизводство",
                        }
                    ]
                    if basis_ref == PARENT_REF
                    else []
                ),
            ],
        },
        "lines": [
            {
                "lineNumber": 1,
                "nomenclatureRef": "nom-1",
                "characteristicRef": characteristic_id,
                "quantity": quantity,
                "cancelled": False,
            }
        ],
    }


def _transfer(quantity: str, characteristic_id: str | None = None) -> dict:
    return {
        "ref": "44444444-4444-4444-4444-444444444444",
        "number": "ПР-1",
        "warehouseFromRef": "warehouse-a",
        "warehouseToRef": "warehouse-b",
        "lines": [
            {
                "lineNumber": 1,
                "nomenclatureRef": "nom-1",
                "characteristicRef": characteristic_id,
                "quantity": quantity,
                "cancelled": False,
            }
        ],
    }


def test_qty_aware_mixed_transfer_and_supplier_coverage() -> None:
    snapshot = build_material_order_coverage(
        positions=[_position("1", "nom-1", "100")],
        transfer_orders=[_transfer("40")],
        supplier_orders=[_supplier(PARENT_REF, quantity="60", definite=False)],
        checked_at="2026-07-30T06:00:00+00:00",
    )

    assert snapshot["coverage_status"] == "full"
    row = snapshot["positions"][0]
    assert row["coverage_source"] == "mixed"
    assert Decimal(row["transfer_ordered_quantity"]) == Decimal("40")
    assert Decimal(row["supplier_ordered_quantity"]) == Decimal("60")
    assert Decimal(row["remaining_quantity"]) == Decimal("0")


def test_quantity_is_consumed_across_duplicate_nomenclature_positions() -> None:
    snapshot = build_material_order_coverage(
        positions=[
            _position("1", "nom-1", "70"),
            _position("2", "nom-1", "70"),
        ],
        transfer_orders=[],
        supplier_orders=[_supplier(PARENT_REF, quantity="100", definite=False)],
        checked_at="2026-07-30T06:00:00+00:00",
    )

    assert snapshot["coverage_status"] == "partial"
    assert Decimal(snapshot["positions"][0]["covered_quantity"]) == Decimal("70")
    assert Decimal(snapshot["positions"][1]["covered_quantity"]) == Decimal("30")
    assert Decimal(snapshot["positions"][1]["remaining_quantity"]) == Decimal("40")


def test_definite_children_replace_conditional_parent_quantity() -> None:
    parent = _supplier(PARENT_REF, quantity="100", definite=False)
    child = _supplier(
        CHILD_REF,
        quantity="60",
        definite=True,
        basis_ref=PARENT_REF,
        basis_type="Document_ЗаказПоставщику",
    )

    selected = select_supplier_orders_for_qty([parent, child])
    assert [order["ref"] for order in selected] == [CHILD_REF]

    snapshot = build_material_order_coverage(
        positions=[_position("1", "nom-1", "100")],
        transfer_orders=[],
        supplier_orders=[parent, child],
        checked_at="2026-07-30T06:00:00+00:00",
    )
    assert snapshot["coverage_status"] == "partial"
    assert Decimal(snapshot["positions"][0]["supplier_ordered_quantity"]) == Decimal("60")
    assert Decimal(snapshot["positions"][0]["remaining_quantity"]) == Decimal("40")


def test_characteristic_mismatch_does_not_cover_position() -> None:
    snapshot = build_material_order_coverage(
        positions=[_position("1", "nom-1", "10", "char-a")],
        transfer_orders=[],
        supplier_orders=[
            _supplier(
                PARENT_REF,
                quantity="10",
                definite=False,
                characteristic_id="char-b",
            )
        ],
        checked_at="2026-07-30T06:00:00+00:00",
    )

    assert snapshot["coverage_status"] == "none"
    assert snapshot["positions"][0]["remaining_quantity"] == "10"


def test_cancelled_or_deleted_documents_do_not_cover_position() -> None:
    supplier = _supplier(PARENT_REF, quantity="10", definite=False)
    supplier["status"] = "Отменен"
    transfer = _transfer("10")
    transfer["DeletionMark"] = True

    snapshot = build_material_order_coverage(
        positions=[_position("1", "nom-1", "10")],
        transfer_orders=[transfer],
        supplier_orders=[supplier],
        checked_at="2026-07-30T06:00:00+00:00",
    )

    assert snapshot["coverage_status"] == "none"
    assert snapshot["positions"][0]["remaining_quantity"] == "10"
