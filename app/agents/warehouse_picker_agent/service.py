from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.agents.procurement_agent.mcp_client import (
    MCPCallError,
    MCPUnavailableError,
    OneCMCPClient,
)
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.warehouse_picker_agent.calculator import calculate_picker_assessment
from app.agents.warehouse_picker_agent.schemas import (
    PickerCaseInput,
    PickerNeedLine,
    PickerSupplyItem,
)
from app.models.enums import ConfidenceLevel, ProcurementSourceType

STORE_ROOM_REGISTERS = (
    ("AccumulationRegister_ТоварыНаСкладах_RecordType", "warehouse"),
    # Soft warehouse reserves (РезервироватьНаСкладе) for Доступно = В наличии − резерв.
    # MCP sourceType enum is limited; items themselves come back as source_type=reservation.
    ("AccumulationRegister_ЗапасыИПотребности", "warehouse"),
)
ZERO_1C_REF = "00000000-0000-0000-0000-000000000000"


def _optional_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if not text or text == ZERO_1C_REF else text


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


class WarehousePickerService:
    def __init__(self, mcp_client: OneCMCPClient | None = None) -> None:
        self.mcp = mcp_client or OneCMCPClient(timeout_seconds=650, max_attempts=2)

    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента кладовщика не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )
        if request.source_type is not ProcurementSourceType.PRODUCTION_MATERIAL_ORDER:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Агент по закупке принимает только заказы материалов в производство.",
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="failed",
                output_data={"validation_errors": [{"field": "source_type"}]},
            )

        embedded = request.source_data
        external, capability_issues = await self._load_external_data(request)
        merged = {**embedded, **external}
        case = _build_case_input(request)
        needs = _parse_needs(merged, case)
        supplies, supply_issues = _parse_supplies(merged.get("supplies") or merged.get("items"))
        assessment = calculate_picker_assessment(
            case=case,
            needs=needs,
            supplies=supplies,
            capability_issues=[*capability_issues, *supply_issues],
        )
        output = assessment.model_dump(mode="json")

        if assessment.decision_kind == "critical_acknowledgement":
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="waiting_human",
                summary=assessment.summary,
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="waiting_human",
                wait_reason=assessment.summary,
                output_data=output,
            )
        if assessment.decision_kind in {
            "stock_confirmation",
            "deficit_confirmation",
            "discrepancy_return",
        }:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="waiting_human",
                summary=assessment.summary,
                data_confidence=(
                    ConfidenceLevel.MEDIUM
                    if assessment.excluded_capabilities
                    else ConfidenceLevel.HIGH
                ),
                requires_human_review=True,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="waiting_human",
                wait_reason=assessment.recommended_next_step,
                output_data=output,
            )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="completed",
            summary=assessment.summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=False,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="completed",
            output_data=output,
        )

    async def _load_external_data(
        self,
        request: ProcurementRoleAgentRequest,
    ) -> tuple[dict[str, Any], list[str]]:
        source_data = request.source_data
        if source_data.get("supplies") is not None and source_data.get("skip_external"):
            return {}, []

        database = str(source_data.get("source_database") or "default")
        material_ids = sorted(
            {
                str(item.get("nomenclature_id"))
                for item in source_data.get("positions") or []
                if isinstance(item, dict) and item.get("nomenclature_id")
            }
        )
        if not material_ids:
            return {"supplies": []}, ["Не получены номенклатуры для чтения остатков склада."]

        warehouse_id = _optional_ref(
            request.role_context.get("warehouse_1c_ref")
            or source_data.get("warehouse_1c_ref")
            or (source_data.get("warehouse_ids") or [None])[0]
        )
        warehouse_ids = [warehouse_id] if warehouse_id else []
        supplies: list[dict[str, Any]] = []
        issues: list[str] = []
        semaphore = asyncio.Semaphore(3)

        async def read_register(entity_set: str, source_type: str) -> None:
            async with semaphore:
                arguments: dict[str, Any] = {
                    "database": database,
                    "entitySet": entity_set,
                    "nomenclatureRefs": material_ids,
                    "sourceType": source_type,
                    "limit": 20000,
                }
                if warehouse_ids:
                    arguments["warehouseRefs"] = warehouse_ids
                    arguments["warehouse_ids"] = warehouse_ids
                try:
                    result = await self.mcp.call_capability(
                        "onec_get_production_supply_evidence",
                        arguments,
                    )
                except MCPUnavailableError:
                    issues.append(f"Регистр 1С «{entity_set}» недоступен.")
                    return
                except MCPCallError as exc:
                    issues.append(f"Ошибка чтения регистра «{entity_set}»: {exc}")
                    return
            if result.get("status") == "capability_unavailable":
                issues.append(
                    str(result.get("reason") or f"Регистр 1С «{entity_set}» не опубликован.")
                )
                return
            for value in result.get("items") or []:
                if isinstance(value, dict):
                    supplies.append(_normalize_supply_item(value, default_source=source_type))

        async def read_store_room() -> None:
            arguments: dict[str, Any] = {
                "database": database,
                "nomenclatureRefs": material_ids,
                "limit": 20000,
            }
            if warehouse_ids:
                arguments["warehouseRefs"] = warehouse_ids
                arguments["warehouse_ids"] = warehouse_ids
            try:
                result = await self.mcp.call_capability(
                    "onec_get_store_room_stock",
                    arguments,
                )
            except (MCPUnavailableError, MCPCallError) as exc:
                issues.append(f"Не удалось прочитать остатки кладовой: {exc}")
                return
            if result.get("status") == "capability_unavailable":
                issues.append(
                    str(result.get("reason") or "Остатки кладовой не опубликованы в OData 1С.")
                )
                return
            for value in result.get("items") or []:
                if isinstance(value, dict):
                    supplies.append(
                        _normalize_supply_item(value, default_source="store_room")
                    )

        await asyncio.gather(
            read_store_room(),
            *(
                read_register(entity_set, source_type)
                for entity_set, source_type in STORE_ROOM_REGISTERS
            ),
        )
        return {"supplies": supplies}, list(dict.fromkeys(issues))


def _normalize_supply_item(
    value: dict[str, Any],
    *,
    default_source: str = "store_room",
) -> dict[str, Any]:
    source_type = str(value.get("source_type") or default_source)
    if source_type in {"warehouse_stock", "free_stock"}:
        source_type = "warehouse"
    if default_source == "reservation" and source_type not in {
        "reservation",
        "warehouse",
        "store_room",
    }:
        source_type = "reservation"
    quantity = (
        value.get("quantity")
        or value.get("reserved_quantity")
        or value.get("Количество")
        or 0
    )
    accounting = value.get("accounting_quantity")
    factual = value.get("factual_quantity")
    assignment_id = _optional_ref(
        value.get("assignment_id")
        or value.get("project_id")
        or value.get("Назначение_Key")
        or value.get("purpose_id")
    )
    reserved_for_other = bool(
        value.get("reserved_for_other")
        or source_type == "reservation"
        or value.get("Зарезервировано") is True
    )
    return {
        "supply_id": str(value.get("supply_id") or value.get("Ref_Key") or ""),
        "source_type": source_type if source_type in {
            "store_room", "warehouse", "reservation", "quality", "quarantine", "blocked"
        } else "store_room",
        "nomenclature_id": str(
            value.get("nomenclature_id") or value.get("Номенклатура_Key") or ""
        ),
        "characteristic_id": _optional_ref(
            value.get("characteristic_id") or value.get("Характеристика_Key")
        ),
        "unit": str(value.get("unit") or value.get("ЕдиницаИзмерения") or ""),
        "quantity": quantity,
        "warehouse_id": _optional_ref(
            value.get("warehouse_id")
            or value.get("Склад_Key")
            or value.get("Склад")
            or value.get("warehouseRef")
        ),
        "assignment_id": assignment_id,
        "assignment_name": value.get("assignment_name")
        or value.get("Назначение")
        or value.get("purpose_name"),
        "accounting_quantity": accounting if accounting is not None else quantity,
        "factual_quantity": factual if factual is not None else quantity,
        "available_for_issue": bool(value.get("available_for_issue", True)),
        "reserved_for_other": reserved_for_other,
        "quarantine": bool(value.get("quarantine")),
        "defective": bool(value.get("defective")),
        "blocked": bool(value.get("blocked")),
        "suitable": bool(value.get("suitable", True)),
        "exact_match": bool(value.get("exact_match", True)),
        "use_allowed": bool(value.get("use_allowed", True)),
        "evidence_id": value.get("evidence_id"),
    }


def _build_case_input(request: ProcurementRoleAgentRequest) -> PickerCaseInput:
    source = request.source_data
    context = request.role_context
    return PickerCaseInput(
        case_id=request.case_id,
        case_number=str(source.get("case_number") or request.source_number or request.case_id),
        source_1c_ref=request.source_1c_ref,
        source_number=request.source_number,
        source_date=source.get("source_date") or context.get("source_date"),
        source_status=source.get("source_status") or context.get("source_status"),
        source_data_version=source.get("source_data_version") or context.get("source_data_version"),
        source_synced_at=source.get("source_synced_at") or context.get("source_synced_at"),
        department_1c_ref=context.get("department_1c_ref"),
        department_name=context.get("department_name"),
        warehouse_1c_ref=context.get("warehouse_1c_ref"),
        warehouse_name=context.get("warehouse_name"),
        organization_1c_ref=context.get("organization_1c_ref"),
        initiator_name=context.get("initiator_name"),
        required_date=source.get("required_date") or context.get("required_date"),
        production_order_1c_ref=source.get("production_order_1c_ref")
        or context.get("production_order_1c_ref"),
        production_order_number=source.get("production_order_number"),
    )


def _parse_needs(merged: dict[str, Any], case: PickerCaseInput) -> list[PickerNeedLine]:
    needs: list[PickerNeedLine] = []
    for index, item in enumerate(merged.get("positions") or [], start=1):
        if not isinstance(item, dict):
            continue
        quantity = _decimal(
            item.get("quantity")
            or item.get("gross_quantity")
            or item.get("direct_quantity"),
            Decimal("0"),
        )
        if quantity <= 0:
            continue
        raw = (
            item.get("raw_payload")
            if isinstance(item.get("raw_payload"), dict)
            else item
        )
        assignment_id = _optional_ref(
            item.get("assignment_id")
            or item.get("project_id")
            or (raw or {}).get("Назначение_Key")
            or (raw or {}).get("assignment_id")
        )
        needs.append(
            PickerNeedLine(
                line_id=str(item.get("line_id") or index),
                nomenclature_id=str(item.get("nomenclature_id") or ""),
                nomenclature_name=str(
                    item.get("nomenclature_name") or item.get("name") or ""
                ),
                characteristic_id=_optional_ref(item.get("characteristic_id")),
                characteristic_name=item.get("characteristic_name"),
                unit=item.get("unit"),
                requested_quantity=quantity,
                required_date=item.get("required_date") or case.required_date,
                warehouse_id=_optional_ref(item.get("warehouse_id"))
                or case.warehouse_1c_ref,
                assignment_id=assignment_id,
                assignment_name=item.get("assignment_name")
                or (raw or {}).get("Назначение")
                or item.get("project_name"),
                raw_payload=raw if isinstance(raw, dict) else item,
            )
        )
    return [item for item in needs if item.nomenclature_id]


def _parse_supplies(raw: Any) -> tuple[list[PickerSupplyItem], list[str]]:
    if not isinstance(raw, list):
        return [], []
    items: list[PickerSupplyItem] = []
    issues: list[str] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        try:
            items.append(PickerSupplyItem.model_validate(value))
        except ValidationError as exc:
            issues.append(f"Некорректный остаток кладовой: {exc.errors()[0]}")
    return items, issues
