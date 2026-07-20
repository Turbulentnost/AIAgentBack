from __future__ import annotations

import asyncio
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
from app.agents.production_preparation_engineer_agent.calculator import (
    calculate_engineer_assessment,
)
from app.agents.production_preparation_engineer_agent.schemas import (
    EngineerCaseInput,
    EngineerNeedLine,
    EngineerSupplyItem,
    ResourceSpecification,
)
from app.models.enums import ConfidenceLevel, ProcurementSourceType

PRODUCTION_SUPPLY_REGISTERS = (
    ("AccumulationRegister_ТоварыНаСкладах_RecordType", "warehouse"),
    (
        "AccumulationRegister_МатериалыИРаботыВПроизводстве_RecordType",
        "semifinished_production",
    ),
)
ZERO_1C_REF = "00000000-0000-0000-0000-000000000000"


def _optional_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if not text or text == ZERO_1C_REF else text


def _onec_date(value: Any) -> Any:
    text = str(value or "")
    return None if text.startswith("0001-01-01") else value


class ProductionPreparationEngineerService:
    def __init__(self, mcp_client: OneCMCPClient | None = None) -> None:
        self.mcp = mcp_client or OneCMCPClient(timeout_seconds=650, max_attempts=2)

    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента инженера СПП не прошли проверку.",
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
                summary="Агент инженера СПП принимает только заказы материалов в производство.",
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
        needs, parse_issues = _parse_needs(merged, case)
        specifications, spec_issues = _parse_models(
            merged.get("specifications"),
            ResourceSpecification,
            "Некорректный формат ресурсной спецификации",
        )
        supplies, supply_issues = _parse_models(
            merged.get("supplies") or merged.get("items"),
            EngineerSupplyItem,
            "Некорректный формат источника обеспечения",
        )
        assessment = calculate_engineer_assessment(
            case=case,
            needs=needs,
            specifications=specifications,
            supplies=supplies,
            capability_issues=[
                *capability_issues,
                *parse_issues,
                *spec_issues,
                *supply_issues,
            ],
        )
        output = assessment.model_dump(mode="json")
        if assessment.validation_issues or assessment.missing_data:
            reason = "; ".join(assessment.missing_data[:3]) or assessment.summary
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="waiting_human",
                summary=assessment.summary,
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="waiting_human",
                wait_reason=reason,
                output_data=output,
            )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="completed",
            summary=assessment.summary,
            data_confidence=(
                ConfidenceLevel.MEDIUM if assessment.excluded_capabilities else ConfidenceLevel.HIGH
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
        if source_data.get("specifications") is not None:
            return {}, []
        database = str(source_data.get("source_database") or "default")
        production_order_ref = source_data.get("production_order_1c_ref")
        production_order_type = source_data.get("production_order_type")
        direct_positions = [
            value for value in source_data.get("positions") or [] if isinstance(value, dict)
        ]
        resolution_issues: list[str] = []
        if not production_order_ref:
            (
                production_order_ref,
                production_order_type,
                resolution_issues,
            ) = await self._resolve_production_order(
                database=database,
                material_order_ref=request.source_1c_ref,
            )
            if production_order_ref:
                source_data["production_order_1c_ref"] = production_order_ref
                source_data["production_order_type"] = production_order_type
        if not production_order_ref and direct_positions:
            # A production material order may be an independent, explicitly quantified
            # material request. In that case its lines are already the gross requirement:
            # there is no production product to explode through a resource specification.
            products = direct_positions
            specifications = _direct_requirement_specifications(direct_positions)
            material_ids = sorted(
                {
                    str(value.get("nomenclature_id"))
                    for value in direct_positions
                    if value.get("nomenclature_id")
                }
            )
            issues: list[str] = []
            if not source_data.get("required_date"):
                source_data["required_date"] = (
                    source_data.get("requested_date") or source_data.get("source_date")
                )
        else:
            product_rows, product_issues = await self._load_production_order_products(
                database=database,
                production_order_ref=production_order_ref,
                production_order_type=production_order_type,
            )
            issues = [*resolution_issues, *product_issues]
            products = _normalize_products(product_rows)
            if products:
                source_data["production_order_number"] = products[0].get(
                    "production_order_number"
                )
                source_data["production_order_status"] = products[0].get(
                    "production_order_status"
                )
            if not products:
                products = [
                    value for value in source_data.get("products") or [] if isinstance(value, dict)
                ]
            product_ids = [
                str(value.get("nomenclature_id"))
                for value in products
                if value.get("nomenclature_id")
            ]
            if not product_ids:
                issues.append(
                    "Не получены изделия производственного заказа "
                    "для поиска ресурсной спецификации."
                )
                return {"products": products}, issues
            try:
                response = await self.mcp.call_capability(
                    "onec_get_active_resource_specifications",
                    {
                        "database": database,
                        "specificationRefs": [],
                        "productRefs": list(dict.fromkeys(product_ids)),
                        "limit": 5000,
                    },
                )
            except MCPUnavailableError:
                issues.append("Чтение действующих ресурсных спецификаций недоступно в MCP 1С.")
                return {"products": products}, issues
            except MCPCallError as exc:
                issues.append(f"Ошибка чтения действующих ресурсных спецификаций: {exc}")
                return {"products": products}, issues
            if response.get("status") == "capability_unavailable":
                issues.append(
                    str(
                        response.get("reason")
                        or "Ресурсные спецификации не опубликованы в OData 1С."
                    )
                )
            specifications = _normalize_specifications(response.get("specifications") or [])
            material_ids = sorted(
                {
                    str(material.get("nomenclature_id"))
                    for spec in specifications
                    for material in spec.get("materials") or []
                    if material.get("nomenclature_id")
                }
            )
        if not material_ids:
            issues.append("В действующих ресурсных спецификациях не получены материалы.")
            return {
                "products": products,
                "specifications": specifications,
                "supplies": [],
            }, issues

        supplies: list[dict[str, Any]] = []
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
                            "limit": 5000,
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
            supplies.extend(
                _normalize_supply_item(value)
                for value in result.get("items") or []
                if isinstance(value, dict)
            )

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
            supplies.extend(
                _normalize_supply_item(value)
                for value in result.get("items") or []
                if isinstance(value, dict)
            )

        await asyncio.gather(
            read_open_supply_documents(),
            *(
                read_register(entity_set, source_type)
                for entity_set, source_type in PRODUCTION_SUPPLY_REGISTERS
            ),
        )
        return {
            "products": products,
            "specifications": specifications,
            "supplies": supplies,
        }, list(dict.fromkeys(issues))

    async def _resolve_production_order(
        self,
        *,
        database: str,
        material_order_ref: str,
    ) -> tuple[str | None, str | None, list[str]]:
        try:
            response = await self.mcp.call_capability(
                "read_production_resolve_material_order_context",
                {
                    "database": database,
                    "materialOrderRef": material_order_ref,
                    "limit": 1000,
                },
            )
        except (MCPUnavailableError, MCPCallError) as exc:
            return None, None, [f"Не удалось определить связь с производством: {exc}"]
        for row in response.get("items") or []:
            if not isinstance(row, dict):
                continue
            reference = row.get("Распоряжение") or row.get("Распоряжение_Key")
            reference_type = str(row.get("Распоряжение_Type") or "")
            if reference and "ЗаказНаПроизводство" in reference_type:
                return str(reference), reference_type, []
        return (
            None,
            None,
            [
                "В регистрах 1С не найдена прямая связь заказа материалов "
                "с производственным заказом."
            ],
        )

    async def _load_production_order_products(
        self,
        *,
        database: str,
        production_order_ref: Any,
        production_order_type: Any,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not production_order_ref:
            return [], ["Не определена ссылка на производственный заказ."]
        type_name = str(production_order_type or "")
        entity_set = (
            "Document_ЗаказНаПроизводство2_2"
            if "ЗаказНаПроизводство2_2" in type_name or not type_name
            else "Document_ЗаказНаПроизводство"
        )
        try:
            response = await self.mcp.call_capability(
                "read_document_get_documents",
                {
                    "database": database,
                    "entitySet": entity_set,
                    "refs": [str(production_order_ref)],
                    "fields": [
                        "Ref_Key",
                        "Number",
                        "Date",
                        "DeletionMark",
                        "Posted",
                        "Статус",
                        "ДатаПотребности",
                        "Продукция",
                    ],
                },
            )
        except (MCPUnavailableError, MCPCallError) as exc:
            return [], [f"Не удалось прочитать производственный заказ: {exc}"]
        documents = response.get("items") or []
        if not documents:
            return [], ["Производственный заказ не найден в 1С."]
        document = documents[0]
        if document.get("DeletionMark"):
            return [], ["Производственный заказ помечен на удаление."]
        status = str(document.get("Статус") or "")
        if status.casefold().replace("ё", "е") in {
            "закрыт",
            "отменен",
            "closed",
            "cancelled",
        }:
            return [], ["Производственный заказ больше не актуален."]
        rows = document.get("Продукция") or []
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("production_order_number", document.get("Number"))
                row.setdefault("production_order_status", status)
                row.setdefault(
                    "required_date",
                    row.get("ДатаПотребности") or document.get("ДатаПотребности"),
                )
        return [value for value in rows if isinstance(value, dict)], []


def _normalize_products(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        nomenclature_id = value.get("Номенклатура_Key") or value.get("nomenclature_id")
        if not nomenclature_id:
            continue
        products.append(
            {
                "line_id": str(value.get("LineNumber") or value.get("line_id") or index),
                "nomenclature_id": str(nomenclature_id),
                "nomenclature_name": str(
                    value.get("Номенклатура") or value.get("nomenclature_name") or nomenclature_id
                ),
                "characteristic_id": _optional_ref(
                    value.get("Характеристика_Key") or value.get("characteristic_id")
                ),
                "unit_id": _optional_ref(value.get("Упаковка_Key") or value.get("unit_id")),
                "unit": value.get("ЕдиницаИзмерения")
                or value.get("unit")
                or _optional_ref(value.get("Упаковка_Key")),
                "product_quantity": value.get("Количество") or value.get("product_quantity"),
                "required_date": value.get("required_date") or value.get("ДатаПотребности"),
                "warehouse_id": value.get("Склад_Key") or value.get("warehouse_id"),
                "project_id": value.get("Назначение_Key") or value.get("project_id"),
                "production_stage_id": value.get("Этап_Key") or value.get("production_stage_id"),
                "production_order_number": value.get("production_order_number"),
                "production_order_status": value.get("production_order_status"),
                "raw_payload": value,
            }
        )
    return products


def _direct_requirement_specifications(
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    specifications: list[dict[str, Any]] = []
    for index, position in enumerate(positions, start=1):
        nomenclature_id = str(position.get("nomenclature_id") or "")
        if not nomenclature_id:
            continue
        line_id = str(position.get("line_id") or index)
        name = str(position.get("nomenclature_name") or nomenclature_id)
        stage_id = position.get("production_stage_id") or "direct_material_order"
        specifications.append(
            {
                "specification_id": f"direct-material-order:{line_id}",
                "name": "Прямая потребность заказа материалов",
                "version": "1C",
                "status": "Действует",
                "approved": True,
                "product_id": nomenclature_id,
                "product_characteristic_id": _optional_ref(position.get("characteristic_id")),
                "completeness_score": 100,
                "production_stage_id": stage_id,
                "materials": [
                    {
                        "line_id": line_id,
                        "nomenclature_id": nomenclature_id,
                        "nomenclature_name": name,
                        "characteristic_id": _optional_ref(position.get("characteristic_id")),
                        "unit_id": _optional_ref(position.get("unit_id")),
                        "unit": position.get("unit") or "шт",
                        "consumption_rate": "1",
                        "technological_loss_percent": "0",
                        "production_stage_id": stage_id,
                        "production_stage_name": "Прямая потребность заказа материалов",
                        "warehouse_id": position.get("warehouse_id"),
                        "use_conditions": "Количество явно задано в заказе материалов 1С",
                    }
                ],
                "evidence_id": f"material-order-line:{line_id}",
            }
        )
    return specifications


def _normalize_specifications(values: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        outputs = [item for item in value.get("outputs") or [] if isinstance(item, dict)]
        materials: list[dict[str, Any]] = []
        for index, material in enumerate(value.get("materials") or [], start=1):
            nomenclature_id = material.get("Номенклатура_Key")
            rate = (
                material.get("Количество")
                or material.get("КоличествоУпаковок")
                or material.get("consumption_rate")
            )
            if not nomenclature_id or rate in (None, ""):
                continue
            materials.append(
                {
                    "line_id": str(material.get("LineNumber") or index),
                    "nomenclature_id": str(nomenclature_id),
                    "nomenclature_name": str(
                        material.get("Номенклатура")
                        or material.get("nomenclature_name")
                        or nomenclature_id
                    ),
                    "characteristic_id": _optional_ref(material.get("Характеристика_Key")),
                    "unit_id": _optional_ref(
                        material.get("ЕдиницаИзмерения_Key") or material.get("Упаковка_Key")
                    ),
                    "unit": material.get("ЕдиницаИзмерения")
                    or material.get("Упаковка")
                    or _optional_ref(material.get("Упаковка_Key")),
                    "consumption_rate": rate,
                    "technological_loss_percent": (
                        material.get("ПроцентТехнологическихПотерь")
                        or material.get("ПроцентПотерь")
                        or 0
                    ),
                    "production_stage_id": material.get("Этап_Key"),
                    "costing_item_id": material.get("СтатьяКалькуляции_Key"),
                    "allowed_analogs": material.get("allowed_analogs") or [],
                    "allowed_semifinished": material.get("allowed_semifinished") or [],
                    "material_ownership": material.get("material_ownership") or "unknown",
                    "use_conditions": material.get("ПрименениеМатериала"),
                }
            )
        output = outputs[0] if outputs else {}
        product_id = (
            output.get("Номенклатура_Key")
            or value.get("ОсновноеИзделиеНоменклатура_Key")
            or value.get("product_id")
        )
        if not product_id:
            continue
        result.append(
            {
                "specification_id": str(value.get("Ref_Key") or value.get("specification_id")),
                "name": str(value.get("Description") or value.get("name") or product_id),
                "version": value.get("ИдентификаторВерсииДанных")
                or value.get("DataVersion")
                or value.get("Code"),
                "status": str(value.get("Статус") or value.get("status") or ""),
                "deletion_mark": bool(value.get("DeletionMark")),
                "valid_from": _onec_date(value.get("НачалоДействия")),
                "valid_to": _onec_date(value.get("КонецДействия")),
                "product_id": str(product_id),
                "product_characteristic_id": _optional_ref(output.get("Характеристика_Key")),
                "approved": str(value.get("Статус") or "").casefold().replace("ё", "е")
                == "действует",
                "completeness_score": sum(
                    1 for item in value.values() if item not in (None, "", [], {})
                ),
                "production_stage_id": value.get("ОсновноеИзделиеЭтап_Key")
                or output.get("Этап_Key"),
                "materials": materials,
                "evidence_id": f"resource-specification:{value.get('Ref_Key')}",
            }
        )
    return result


def _normalize_supply_item(value: dict[str, Any]) -> dict[str, Any]:
    source_type = str(value.get("source_type") or "warehouse")
    if source_type == "semifinished_production":
        source_type = "work_in_progress"
    return {
        "supply_id": str(value.get("supply_id") or value.get("Ref_Key") or ""),
        "source_type": source_type,
        "nomenclature_id": str(value.get("nomenclature_id") or value.get("Номенклатура_Key") or ""),
        "characteristic_id": _optional_ref(
            value.get("characteristic_id") or value.get("Характеристика_Key")
        ),
        "unit": str(
            value.get("unit")
            or value.get("ЕдиницаИзмерения")
            or value.get("ЕдиницаИзмерения_Key")
            or ""
        ),
        "quantity": value.get("quantity") or value.get("Количество") or 0,
        "warehouse_id": value.get("warehouse_id") or value.get("Склад_Key"),
        "available_at": value.get("available_at") or value.get("ДатаПоступления"),
        "confirmed": bool(value.get("confirmed")),
        "reserved_for_other": bool(value.get("reserved_for_other")),
        "safety_stock_forbidden": bool(value.get("safety_stock_forbidden")),
        "incoming_control_passed": bool(value.get("incoming_control_passed", False)),
        "quarantine": bool(value.get("quarantine")),
        "defective": bool(value.get("defective")),
        "blocked": bool(value.get("blocked")),
        "expired": bool(value.get("expired")),
        "suitable": bool(value.get("suitable")),
        "exact_match": bool(value.get("exact_match")),
        "use_allowed": bool(value.get("use_allowed", True)),
        "linked_document_number": value.get("linked_document_number"),
        "linked_document_ref": value.get("linked_document_ref"),
        "evidence_id": value.get("evidence_id"),
    }


def _build_case_input(request: ProcurementRoleAgentRequest) -> EngineerCaseInput:
    source = request.source_data
    context = request.role_context
    return EngineerCaseInput(
        case_id=request.case_id,
        case_number=str(source.get("case_number") or request.source_number or request.case_id),
        source_1c_ref=request.source_1c_ref,
        source_number=request.source_number,
        source_date=source.get("source_date") or context.get("source_date"),
        source_status=source.get("source_status") or context.get("source_status"),
        source_data_version=source.get("source_data_version") or context.get("source_data_version"),
        source_synced_at=source.get("source_synced_at") or context.get("source_synced_at"),
        initiator_1c_ref=context.get("initiator_1c_ref"),
        initiator_name=context.get("initiator_name"),
        department_1c_ref=context.get("department_1c_ref"),
        department_name=context.get("department_name"),
        organization_1c_ref=context.get("organization_1c_ref"),
        warehouse_1c_ref=context.get("warehouse_1c_ref"),
        warehouse_name=context.get("warehouse_name"),
        priority_1c_ref=context.get("priority_1c_ref"),
        required_date=(
            source.get("required_date")
            or source.get("requested_date")
            or context.get("required_date")
        ),
        production_order_1c_ref=(
            source.get("production_order_1c_ref") or context.get("production_order_1c_ref")
        ),
        production_order_number=source.get("production_order_number"),
        production_order_status=source.get("production_order_status"),
    )


def _parse_needs(
    source: dict[str, Any],
    case: EngineerCaseInput,
) -> tuple[list[EngineerNeedLine], list[str]]:
    raw_values = source.get("products")
    if not isinstance(raw_values, list) or not raw_values:
        raw_values = source.get("positions") or []
    needs: list[EngineerNeedLine] = []
    issues: list[str] = []
    for index, raw in enumerate(raw_values):
        if not isinstance(raw, dict):
            issues.append(f"Некорректный формат изделия {index + 1}.")
            continue
        candidate = dict(raw)
        candidate.setdefault("nomenclature_name", candidate.get("nomenclature_id") or "")
        candidate.setdefault(
            "direct_quantity", candidate.get("quantity") or candidate.get("gross_quantity")
        )
        candidate.setdefault("required_date", case.required_date)
        candidate.setdefault("warehouse_id", case.warehouse_1c_ref)
        try:
            needs.append(EngineerNeedLine.model_validate(candidate))
        except ValidationError as exc:
            issues.append(f"Некорректные данные изделия {index + 1}: {exc.errors()[0]['msg']}.")
    return needs, issues


def _parse_models(
    values: Any,
    model: type[ResourceSpecification] | type[EngineerSupplyItem],
    label: str,
) -> tuple[list[Any], list[str]]:
    records: list[Any] = []
    issues: list[str] = []
    for index, value in enumerate(values if isinstance(values, list) else []):
        try:
            records.append(model.model_validate(value))
        except ValidationError:
            issues.append(f"{label}, запись {index + 1}.")
    return records, issues


__all__ = ["ProductionPreparationEngineerService"]
