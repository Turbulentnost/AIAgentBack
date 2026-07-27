"""Stub tests for 1C chain → fulfillment_status mapping and enrich payload."""

from __future__ import annotations

from app.agents.procurement_agent.chain_entities import inventory_snapshot
from app.services.procurement_1c_chain_enricher import (
    build_chain_payload,
    map_chain_to_statuses,
)


def test_chain_entity_inventory_has_enrich_sets() -> None:
    snap = inventory_snapshot()
    enrich = snap["enrich"]
    assert "purchase_order" in enrich
    assert enrich["purchase_order"]["entity_set"] == "Document_ЗаказПоставщику"
    assert "cash_request" in enrich
    assert "otk_presentation" in enrich
    assert "purchase_receipt" in enrich
    assert "goods_receipt_order" in enrich
    assert snap["mcp_aliases"]["read_procurement_chain_purchase_orders"]


def test_map_no_definite_po() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "purchase_order": [{"Ref_Key": "po-1", "Контрагент_Key": ""}],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "no_supplier"
    assert statuses["case_status"] == "purchase_draft"


def test_map_cash_request_unpaid() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "purchase_order": [
                {"Ref_Key": "po-1", "Контрагент_Key": "c-1", "is_definite": True}
            ],
            "cash_request": [{"Ref_Key": "cr-1", "Статус": "Согласована"}],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "payment"
    assert statuses["case_status"] == "payment_pending"


def test_map_paid_to_delivery() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "purchase_order": [{"Ref_Key": "po-1", "Контрагент_Key": "c-1"}],
            "cash_request": [{"Ref_Key": "cr-1", "Статус": "Оплачена", "paid": True}],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "delivery"
    assert statuses["case_status"] == "in_transit"


def test_map_otk_presentation() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "purchase_order": [{"Ref_Key": "po-1", "Контрагент_Key": "c-1"}],
            "cash_request": [{"Ref_Key": "cr-1", "paid": True}],
            "otk_presentation": [
                {"Ref_Key": "otk-1", "РезультатКонтроля": "На контроле"}
            ],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "otk_presentation"
    assert statuses["case_status"] == "receiving"


def test_map_otk_ok_to_posting() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "otk_presentation": [{"Ref_Key": "otk-1", "РезультатКонтроля": "Годен"}],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "posting"
    assert statuses["case_status"] == "posting_required"


def test_map_receipt_completed() -> None:
    chain = build_chain_payload(
        source_ref="need-1",
        documents_by_stage={
            "goods_receipt_order": [{"Ref_Key": "gr-1", "Posted": True}],
        },
    )
    statuses = map_chain_to_statuses(chain)
    assert statuses["fulfillment_status"] == "completed"
    assert statuses["case_status"] == "posted"
    assert chain["mapped_fulfillment_status"] == "completed"
