from __future__ import annotations

import asyncio

from app.tools import procurement_onec_tools as tools
from app.tools.schemas import (
    ProcurementProductionSupplyInput,
    ProcurementResourceSpecificationsInput,
    ProcurementSupplyReadInput,
)


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def call_capability(self, capability, arguments):
        self.calls.append((capability, arguments))
        return self.response


def test_resource_specification_tool_maps_batch_arguments(monkeypatch) -> None:
    client = _FakeClient({"status": "success", "items": [], "specifications": []})
    monkeypatch.setattr(tools, "MCP_CLIENT_FACTORY", lambda: client)
    payload = ProcurementResourceSpecificationsInput(
        correlation_id="corr-1",
        specification_ids=["00000000-0000-0000-0000-000000000001"],
        product_ids=["00000000-0000-0000-0000-000000000002"],
        database="erp",
    )

    result = asyncio.run(
        tools.GetActiveResourceSpecificationsTool().execute(payload, None)  # type: ignore[arg-type]
    )

    assert result.status == "success"
    assert client.calls[0][0] == "onec_get_active_resource_specifications"
    assert client.calls[0][1]["specificationRefs"] == payload.specification_ids
    assert client.calls[0][1]["productRefs"] == payload.product_ids


def test_production_supply_rejects_unconfirmed_items(monkeypatch) -> None:
    client = _FakeClient(
        {
            "status": "success",
            "items": [
                {
                    "supply_id": "row-1",
                    "source_type": "semifinished",
                    "nomenclature_id": "item-1",
                    "unit": "шт",
                    "quantity": 4,
                    "confirmed": False,
                    "suitable": True,
                    "evidence_id": "register:row-1",
                }
            ],
        }
    )
    monkeypatch.setattr(tools, "MCP_CLIENT_FACTORY", lambda: client)
    payload = ProcurementProductionSupplyInput(
        correlation_id="corr-2",
        nomenclature_ids=["00000000-0000-0000-0000-000000000001"],
        entity_set="AccumulationRegister_МатериалыВПроизводстве",
        source_type="semifinished",
    )

    result = asyncio.run(
        tools.GetProductionSupplyEvidenceTool().execute(payload, None)  # type: ignore[arg-type]
    )

    assert result.status == "success"
    assert result.data["items"] == []
    assert result.data["python_excluded_count"] == 1


def test_accounting_inventory_uses_items_and_excludes_it_from_coverage(monkeypatch) -> None:
    client = _FakeClient(
        {
            "items": [
                {
                    "ref": "item-1",
                    "item": "Материал",
                    "quantity": 5,
                }
            ],
            "paginationComplete": True,
        }
    )
    monkeypatch.setattr(tools, "MCP_CLIENT_FACTORY", lambda: client)
    payload = ProcurementSupplyReadInput(
        correlation_id="corr-3",
        nomenclature_ids=["item-1"],
    )

    result = asyncio.run(tools.GetFreeStockTool().execute(payload, None))  # type: ignore[arg-type]

    assert result.status == "success"
    assert result.data["items"] == []
    assert len(result.data["excluded_items"]) == 1
    assert "normalized_items" not in result.data
