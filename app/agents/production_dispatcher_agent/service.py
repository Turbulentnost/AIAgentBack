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
from app.agents.production_dispatcher_agent.calculator import (
    calculate_dispatcher_assessment,
)
from app.agents.production_dispatcher_agent.schemas import (
    DispatcherCaseInput,
    DispatcherNeedLine,
    DispatcherSupplyItem,
)
from app.models.enums import ConfidenceLevel, ProcurementSourceType

PRODUCTION_SUPPLY_REGISTERS = (
    ("AccumulationRegister_ТоварыНаСкладах_RecordType", "warehouse"),
    (
        "AccumulationRegister_МатериалыИРаботыВПроизводстве_RecordType",
        "in_progress",
    ),
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


class ProductionDispatcherService:
    def __init__(self, mcp_client: OneCMCPClient | None = None) -> None:
        self.mcp = mcp_client or OneCMCPClient(timeout_seconds=650, max_attempts=2)

    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента диспетчера не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        allowed = {
            ProcurementSourceType.REORDER_POINT,
            ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
        }
        if request.source_type not in allowed:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Агент диспетчера принимает точки заказа и кейсы после инженера.",
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
        needs = _parse_needs(merged, case, request)
        supplies, supply_issues = _parse_supplies(merged.get("supplies") or merged.get("items"))
        assessment = calculate_dispatcher_assessment(
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
        if assessment.decision_kind == "supply_confirmation":
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
                wait_reason="Требуется подтверждение способа обеспечения.",
                output_data=output,
            )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="completed",
            summary=assessment.summary,
            data_confidence=(
                ConfidenceLevel.MEDIUM
                if assessment.excluded_capabilities
                else ConfidenceLevel.HIGH
            ),
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
        # Engineer handoff: use deficit nomenclature from engineer output.
        engineer_output = source_data.get("production_preparation_engineer_output") or {}
        if not material_ids and isinstance(engineer_output, dict):
            material_ids = sorted(
                {
                    str(item.get("nomenclature_id"))
                    for item in engineer_output.get("positions") or []
                    if isinstance(item, dict) and item.get("nomenclature_id")
                }
            )
        if not material_ids:
            return {"supplies": []}, ["Не получены номенклатуры для чтения остатков."]

        supplies: list[dict[str, Any]] = []
        issues: list[str] = []
        semaphore = asyncio.Semaphore(3)

        async def read_register(entity_set: str, source_type: str) -> None:
            async with semaphore:
                try:
                    result = await self.mcp.call_capability(
                        "onec_get_production_supply_evidence",
                        {
                            "database": database,
                            "entitySet": entity_set,
                            "nomenclatureRefs": material_ids,
                            "sourceType": source_type,
                            "limit": 20000,
                        },
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

        async def read_open_supply_documents() -> None:
            try:
                result = await self.mcp.call_capability(
                    "read_production_get_open_supply_documents",
                    {
                        "database": database,
                        "nomenclatureRefs": material_ids,
                        "limit": 5000,
                    },
                )
            except (MCPUnavailableError, MCPCallError) as exc:
                issues.append(f"Не удалось прочитать открытые заказы обеспечения: {exc}")
                return
            if result.get("status") == "capability_unavailable":
                issues.append(
                    str(
                        result.get("reason")
                        or "Документы поставок и перемещений не опубликованы в OData 1С."
                    )
                )
                return
            for value in result.get("items") or []:
                if isinstance(value, dict):
                    supplies.append(_normalize_supply_item(value))

        await asyncio.gather(
            read_open_supply_documents(),
            *(
                read_register(entity_set, source_type)
                for entity_set, source_type in PRODUCTION_SUPPLY_REGISTERS
            ),
        )
        return {"supplies": supplies}, list(dict.fromkeys(issues))


def _normalize_supply_item(
    value: dict[str, Any],
    *,
    default_source: str = "warehouse",
) -> dict[str, Any]:
    source_type = str(value.get("source_type") or default_source)
    if source_type == "semifinished_production":
        source_type = "in_progress"
    if source_type == "work_in_progress":
        source_type = "in_progress"
    return {
        "supply_id": str(value.get("supply_id") or value.get("Ref_Key") or ""),
        "source_type": source_type,
        "nomenclature_id": str(value.get("nomenclature_id") or value.get("Номенклатура_Key") or ""),
        "characteristic_id": _optional_ref(
            value.get("characteristic_id") or value.get("Характеристика_Key")
        ),
        "unit": str(value.get("unit") or value.get("ЕдиницаИзмерения") or ""),
        "quantity": value.get("quantity") or value.get("Количество") or 0,
        "warehouse_id": value.get("warehouse_id") or value.get("Склад_Key"),
        "available_at": value.get("available_at") or value.get("ДатаПоступления"),
        "confirmed": bool(value.get("confirmed", True)),
        "reserved_for_other": bool(value.get("reserved_for_other")),
        "incoming_control_passed": bool(value.get("incoming_control_passed", True)),
        "quarantine": bool(value.get("quarantine")),
        "defective": bool(value.get("defective")),
        "blocked": bool(value.get("blocked")),
        "expired": bool(value.get("expired")),
        "suitable": bool(value.get("suitable", True)),
        "exact_match": bool(value.get("exact_match", True)),
        "use_allowed": bool(value.get("use_allowed", True)),
        "linked_document_number": value.get("linked_document_number"),
        "linked_document_ref": value.get("linked_document_ref"),
        "evidence_id": value.get("evidence_id"),
    }


def _build_case_input(request: ProcurementRoleAgentRequest) -> DispatcherCaseInput:
    source = request.source_data
    context = request.role_context
    return DispatcherCaseInput(
        case_id=request.case_id,
        case_number=str(source.get("case_number") or request.source_number or request.case_id),
        source_type=request.source_type.value,
        source_1c_ref=request.source_1c_ref,
        source_number=request.source_number,
        source_date=source.get("source_date") or context.get("source_date"),
        source_status=source.get("source_status") or context.get("source_status"),
        source_data_version=source.get("source_data_version") or context.get("source_data_version"),
        source_synced_at=source.get("source_synced_at") or context.get("source_synced_at"),
        warehouse_1c_ref=context.get("warehouse_1c_ref"),
        warehouse_name=context.get("warehouse_name"),
        department_1c_ref=context.get("department_1c_ref"),
        department_name=context.get("department_name"),
        organization_1c_ref=context.get("organization_1c_ref"),
        initiator_1c_ref=context.get("initiator_1c_ref"),
        initiator_name=context.get("initiator_name"),
        required_date=source.get("required_date") or context.get("required_date"),
        production_order_1c_ref=(
            source.get("production_order_1c_ref") or context.get("production_order_1c_ref")
        ),
        production_order_number=source.get("production_order_number"),
        source_basis_1c_ref=context.get("source_basis_1c_ref") or source.get("source_basis_1c_ref"),
        source_basis_number=context.get("source_basis_number") or source.get("source_basis_number"),
        stock_growth_coefficient=_decimal(
            source.get("stock_growth_coefficient") or context.get("stock_growth_coefficient") or 1,
            Decimal("1"),
        ),
    )


def _parse_needs(
    merged: dict[str, Any],
    case: DispatcherCaseInput,
    request: ProcurementRoleAgentRequest,
) -> list[DispatcherNeedLine]:
    engineer_output = merged.get("production_preparation_engineer_output")
    if isinstance(engineer_output, dict) and engineer_output.get("positions"):
        needs: list[DispatcherNeedLine] = []
        for item in engineer_output.get("positions") or []:
            if not isinstance(item, dict):
                continue
            net = _decimal(item.get("net_requirement"))
            gross = _decimal(item.get("gross_requirement"), Decimal("1"))
            needs.append(
                DispatcherNeedLine(
                    line_id=str(item.get("line_id") or len(needs) + 1),
                    nomenclature_id=str(item.get("nomenclature_id") or ""),
                    nomenclature_name=str(item.get("nomenclature_name") or ""),
                    characteristic_id=_optional_ref(item.get("characteristic_id")),
                    characteristic_name=item.get("characteristic_name"),
                    unit=item.get("unit"),
                    quantity=gross if gross > 0 else Decimal("1"),
                    required_date=item.get("required_date") or case.required_date,
                    warehouse_id=case.warehouse_1c_ref,
                    minimum_stock=_decimal(item.get("free_stock"), Decimal("0")),
                    maximum_stock=gross,
                    reorder_point=gross,
                    production_deficit=net if net > 0 else gross,
                    raw_payload=item,
                )
            )
        return [item for item in needs if item.nomenclature_id]

    needs = []
    for index, item in enumerate(merged.get("positions") or [], start=1):
        if not isinstance(item, dict):
            continue
        raw = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else item
        min_stock = _decimal(
            raw.get("МинимальноеКоличествоЗапаса_После")
            or item.get("minimum_stock")
            or raw.get("МинимальноеКоличествоЗапаса_До")
        )
        max_stock = _decimal(
            raw.get("МаксимальноеКоличествоЗапаса_После")
            or item.get("maximum_stock")
            or item.get("quantity")
            or item.get("gross_quantity")
            or raw.get("МаксимальноеКоличествоЗапаса_До")
        )
        quantity = _decimal(item.get("quantity") or item.get("gross_quantity") or max_stock, Decimal("1"))
        if quantity <= 0:
            continue
        needs.append(
            DispatcherNeedLine(
                line_id=str(item.get("line_id") or index),
                nomenclature_id=str(item.get("nomenclature_id") or ""),
                nomenclature_name=str(
                    item.get("nomenclature_name") or item.get("name") or ""
                ),
                characteristic_id=_optional_ref(item.get("characteristic_id")),
                characteristic_name=item.get("characteristic_name"),
                unit=item.get("unit"),
                quantity=quantity,
                required_date=item.get("required_date") or case.required_date,
                warehouse_id=item.get("warehouse_id") or case.warehouse_1c_ref,
                minimum_stock=min_stock if min_stock > 0 else None,
                maximum_stock=max_stock if max_stock > 0 else quantity,
                reorder_point=_decimal(item.get("reorder_point")) or None,
                stock_growth_coefficient=_decimal(
                    item.get("stock_growth_coefficient") or case.stock_growth_coefficient,
                    Decimal("1"),
                ),
                production_deficit=_decimal(item.get("production_deficit")) or None,
                raw_payload=raw if isinstance(raw, dict) else {},
            )
        )
    return [item for item in needs if item.nomenclature_id]


def _parse_supplies(raw: Any) -> tuple[list[DispatcherSupplyItem], list[str]]:
    if not isinstance(raw, list):
        return [], []
    items: list[DispatcherSupplyItem] = []
    issues: list[str] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        try:
            items.append(DispatcherSupplyItem.model_validate(value))
        except ValidationError as exc:
            issues.append(f"Некорректный источник обеспечения: {exc.errors()[0]}")
    return items, issues
