from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.procurement_agent.mcp_client import MCPCallError, MCPUnavailableError, OneCMCPClient
from app.agents.procurement_agent.mcp_normalizers import normalize_inventory_response
from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import (
    ProcurementNeedLinesInput,
    ProcurementOneCReadOutput,
    ProcurementSupplyReadInput,
    ToolContext,
)


MCP_CLIENT_FACTORY = OneCMCPClient
_SENSITIVE_KEYS = {"password", "token", "secret", "authorization", "connection_string"}


class _ProcurementOneCReadTool(Tool):
    action_class = "R"
    required_permissions = ["agents.procurement_logistics_agent.run"]
    preview_safe = False
    object_type = "onec_business_data"

    async def execute(
        self,
        payload: BaseModel,
        context: ToolContext,
    ) -> ProcurementOneCReadOutput:
        correlation_id = str(getattr(payload, "correlation_id"))
        arguments = payload.model_dump(mode="json", exclude={"correlation_id"})
        retrieved_at = datetime.now(UTC)
        try:
            raw = await MCP_CLIENT_FACTORY().call_capability(self.name, arguments)
        except MCPUnavailableError:
            return ProcurementOneCReadOutput(
                status="capability_unavailable",
                tool_name=self.name,
                object_type=self.object_type,
                retrieved_at=retrieved_at,
                freshness_status="unknown",
                correlation_id=correlation_id,
                error_code="capability_unavailable",
                error_message=(
                    "Необходимая read-only возможность отсутствует "
                    "в подключённом MCP 1С."
                ),
            )
        except MCPCallError:
            return ProcurementOneCReadOutput(
                status="failed",
                tool_name=self.name,
                object_type=self.object_type,
                retrieved_at=retrieved_at,
                freshness_status="unknown",
                correlation_id=correlation_id,
                error_code="mcp_call_failed",
                error_message="Read-only инструмент 1С завершился ошибкой.",
            )

        if not isinstance(raw, (dict, list)):
            return ProcurementOneCReadOutput(
                status="failed",
                tool_name=self.name,
                object_type=self.object_type,
                retrieved_at=retrieved_at,
                freshness_status="unknown",
                correlation_id=correlation_id,
                error_code="invalid_mcp_response",
                error_message="Инструмент 1С вернул неподдерживаемый формат.",
            )
        envelope = raw if isinstance(raw, dict) else {"items": raw}
        business_effective_at = _parse_datetime(envelope.get("business_effective_at"))
        freshness_status = envelope.get("freshness_status")
        if freshness_status not in {"fresh", "stale", "unknown"}:
            freshness_status = "unknown"
        return ProcurementOneCReadOutput(
            status="success",
            tool_name=self.name,
            object_type=str(envelope.get("object_type") or self.object_type),
            object_id=_optional_string(envelope.get("object_id")),
            row_ids=[str(value) for value in envelope.get("row_ids") or []],
            retrieved_at=retrieved_at,
            business_effective_at=business_effective_at,
            data=_redact(envelope.get("data", envelope)),
            freshness_status=freshness_status,
            correlation_id=correlation_id,
        )


class GetProcurementNeedLinesTool(_ProcurementOneCReadTool):
    name = "onec_get_procurement_need_lines"
    description = "Получить строки исходной потребности из 1С (только чтение)."
    agent_description = "Возвращает типизированные строки исходного документа потребности."
    input_model = ProcurementNeedLinesInput
    output_model = ProcurementOneCReadOutput
    object_type = "procurement_need"


class GetFreeStockTool(_ProcurementOneCReadTool):
    name = "onec_get_free_stock"
    description = (
        "Проверить доступный в MCP бухгалтерский остаток. "
        "Без резервов, склада, единицы и контроля качества свободным остатком не считается."
    )
    agent_description = description
    input_model = ProcurementSupplyReadInput
    output_model = ProcurementOneCReadOutput
    object_type = "accounting_inventory_snapshot"

    async def execute(
        self,
        payload: BaseModel,
        context: ToolContext,
    ) -> ProcurementOneCReadOutput:
        request = ProcurementSupplyReadInput.model_validate(payload)
        retrieved_at = datetime.now(UTC)
        arguments: dict[str, Any] = {
            "limit": min(1000, max(request.limit, len(request.nomenclature_ids))),
            "nomenclatureRefs": list(request.nomenclature_ids),
        }
        if request.database:
            arguments["database"] = request.database
        # MCP accepts organization name or Ref_Key GUID.
        if request.organization_id:
            arguments["organization"] = request.organization_id
        try:
            raw = await MCP_CLIENT_FACTORY().call_capability(self.name, arguments)
        except MCPUnavailableError:
            return ProcurementOneCReadOutput(
                status="capability_unavailable",
                tool_name=self.name,
                object_type=self.object_type,
                retrieved_at=retrieved_at,
                freshness_status="unknown",
                correlation_id=request.correlation_id,
                error_code="capability_unavailable",
                error_message="Бухгалтерский остаток MCP недоступен.",
            )
        except MCPCallError as exc:
            return ProcurementOneCReadOutput(
                status="failed",
                tool_name=self.name,
                object_type=self.object_type,
                retrieved_at=retrieved_at,
                freshness_status="unknown",
                correlation_id=request.correlation_id,
                error_code="mcp_call_failed",
                error_message=str(exc)[:1000],
            )

        normalized = normalize_inventory_response(
            raw,
            correlation_id=request.correlation_id,
            retrieved_at=retrieved_at,
            requested_nomenclature_ids=request.nomenclature_ids,
            requested_warehouse_ids=request.warehouse_ids,
            requested_organization_id=request.organization_id,
            limit=request.limit,
        )
        warnings = list(normalized.warnings)
        warnings.append(
            "accounting_balance_only: резервы/качество/свободный остаток "
            "не подтверждены MCP; позиции исключаются из покрытия"
        )
        return ProcurementOneCReadOutput(
            status="success",
            tool_name=self.name,
            object_type=self.object_type,
            retrieved_at=retrieved_at,
            business_effective_at=retrieved_at,
            data={
                "normalized_items": [
                    item.model_dump(mode="json")
                    for item in normalized.records
                ],
                "normalization_errors": normalized.errors,
                "normalization_warnings": warnings,
                "pagination_complete": normalized.pagination_complete,
                "semantic_limitations": [
                    "reservations_not_applied",
                    "quality_status_unavailable",
                    "free_stock_not_confirmed",
                ],
            },
            freshness_status="fresh",
            correlation_id=request.correlation_id,
        )


def _supply_tool(
    *,
    name: str,
    description: str,
    object_type: str,
) -> Tool:
    return _ProcurementOneCReadTool(
        name=name,
        description=description,
        agent_description=description,
        input_model=ProcurementSupplyReadInput,
        output_model=ProcurementOneCReadOutput,
        object_type=object_type,
    )


register_tool(GetProcurementNeedLinesTool())
register_tool(GetFreeStockTool())
register_tool(
    _supply_tool(
        name="onec_get_reservations",
        description="Получить резервы по потребностям из 1С (только чтение).",
        object_type="reservation",
    )
)
register_tool(
    _supply_tool(
        name="onec_get_store_room_stock",
        description="Получить свободные остатки в кладовых из 1С (только чтение).",
        object_type="store_room_stock",
    )
)
register_tool(
    _supply_tool(
        name="onec_get_open_supplier_orders",
        description="Получить подтверждённые открытые заказы поставщикам из 1С (только чтение).",
        object_type="supplier_order",
    )
)
register_tool(
    _supply_tool(
        name="onec_get_goods_in_transit",
        description="Получить подтверждённые ТМЦ в пути из 1С (только чтение).",
        object_type="goods_in_transit",
    )
)
register_tool(
    _supply_tool(
        name="onec_get_internal_transfers",
        description="Получить возможные внутренние перемещения из 1С (только чтение).",
        object_type="internal_transfer",
    )
)
register_tool(
    _supply_tool(
        name="onec_get_available_semifinished_goods",
        description=(
            "Получить доступные полуфабрикаты из 1С, "
            "если возможность поддержана (только чтение)."
        ),
        object_type="semifinished_goods",
    )
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("***" if str(key).lower() in _SENSITIVE_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


__all__ = ["MCP_CLIENT_FACTORY"]
