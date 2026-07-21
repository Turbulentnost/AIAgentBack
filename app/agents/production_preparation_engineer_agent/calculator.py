from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.agents.production_preparation_engineer_agent.schemas import (
    EngineerAssessmentLine,
    EngineerCaseInput,
    EngineerCriticalImpact,
    EngineerCriticality,
    EngineerExcludedSupply,
    EngineerNeedLine,
    EngineerOutcome,
    EngineerSupplyBreakdown,
    EngineerSupplyItem,
    EngineerValidationIssue,
    ProductionPreparationEngineerOutput,
    ResourceSpecification,
    ResourceSpecificationMaterial,
)

ACTIVE_SPEC_STATUSES = frozenset({"действует", "active", "approved", "утверждена"})
FUTURE_SUPPLY_TYPES = frozenset({"supplier_order", "in_transit", "production_plan"})
TRANSFER_SUPPLY_TYPES = frozenset({"internal_transfer"})
OTHER_WAREHOUSE_TYPES = frozenset({"warehouse", "store_room"})


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_active_spec(spec: ResourceSpecification, production_date: datetime) -> bool:
    if spec.deletion_mark or not spec.approved:
        return False
    if spec.status.strip().casefold().replace("ё", "е") not in ACTIVE_SPEC_STATUSES:
        return False
    if spec.valid_from and production_date < spec.valid_from:
        return False
    return not (spec.valid_to and production_date > spec.valid_to)


def select_resource_specification(
    need: EngineerNeedLine,
    specifications: list[ResourceSpecification],
    production_date: datetime,
) -> ResourceSpecification | None:
    candidates = [
        spec
        for spec in specifications
        if spec.product_id == need.nomenclature_id
        and (
            not need.characteristic_id
            or not spec.product_characteristic_id
            or spec.product_characteristic_id == need.characteristic_id
        )
        and _is_active_spec(spec, production_date)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda value: (
            value.completeness_score,
            len(value.materials),
            value.version or "",
            value.valid_from or datetime.min.replace(tzinfo=UTC),
            value.specification_id,
        ),
    )


def validate_case(
    case: EngineerCaseInput, needs: list[EngineerNeedLine]
) -> list[EngineerValidationIssue]:
    issues: list[EngineerValidationIssue] = []
    if not case.source_1c_ref:
        issues.append(_issue("document_missing", "Документ не найден в 1С.", "source_1c_ref", "1c"))
    status = (case.source_status or "").strip().casefold().replace("ё", "е")
    if not status:
        issues.append(
            _issue(
                "document_status_missing", "Не указан статус документа 1С.", "source_status", "1c"
            )
        )
    elif status in {"закрыт", "отменен", "cancelled", "closed"}:
        issues.append(
            _issue(
                "document_inactive",
                "Статус документа не допускает обеспечение.",
                "source_status",
                "1c",
            )
        )
    is_direct_material_order = bool(needs) and all(
        need.direct_quantity is not None for need in needs
    )
    if not case.production_order_1c_ref and not is_direct_material_order:
        issues.append(
            _issue(
                "production_order_missing",
                "Не определён производственный заказ, связанный с потребностью.",
                "production_order_1c_ref",
                "1c",
            )
        )
    if not case.required_date and not any(item.required_date for item in needs):
        issues.append(
            _issue(
                "required_date_missing", "Не заполнен требуемый срок обеспечения.", "required_date"
            )
        )
    if not case.warehouse_1c_ref:
        issues.append(
            _issue(
                "recipient_missing",
                "Не указан склад, кладовая или подразделение-получатель.",
                "warehouse_1c_ref",
            )
        )
    if not needs:
        issues.append(
            _issue("positions_missing", "В документе отсутствуют активные изделия.", "positions")
        )
    for need in needs:
        if not need.unit:
            issues.append(
                _issue(
                    "unit_missing",
                    f"Для позиции «{need.nomenclature_name}» не определена единица измерения.",
                    "unit",
                    "case",
                    need.line_id,
                )
            )
        if not need.required_date and not case.required_date:
            issues.append(
                _issue(
                    "line_required_date_missing",
                    f"Для позиции «{need.nomenclature_name}» не указана требуемая дата.",
                    "required_date",
                    "case",
                    need.line_id,
                )
            )
    return issues


def validate_specification(
    need: EngineerNeedLine,
    spec: ResourceSpecification | None,
) -> list[EngineerValidationIssue]:
    if spec is None:
        return [
            _issue(
                "active_specification_missing",
                f"Для изделия «{need.nomenclature_name}» не найдена "
                "утверждённая действующая ресурсная спецификация.",
                "specification",
                "specification",
                need.line_id,
            )
        ]
    issues: list[EngineerValidationIssue] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for material in spec.materials:
        key = (
            material.nomenclature_id,
            material.characteristic_id,
            material.unit,
            material.production_stage_id,
        )
        if key in seen:
            issues.append(
                _issue(
                    "duplicate_specification_line",
                    f"В спецификации «{spec.name}» дублируется материал "
                    f"«{material.nomenclature_name}».",
                    "materials",
                    "specification",
                    need.line_id,
                )
            )
        seen.add(key)
        if not material.unit:
            issues.append(
                _issue(
                    "material_unit_missing",
                    f"Для материала «{material.nomenclature_name}» отсутствует единица измерения.",
                    "unit",
                    "specification",
                    material.line_id,
                )
            )
        if not material.production_stage_id:
            issues.append(
                _issue(
                    "production_stage_missing",
                    f"Для материала «{material.nomenclature_name}» "
                    "не указан производственный этап.",
                    "production_stage_id",
                    "specification",
                    material.line_id,
                )
            )
    if not spec.materials:
        issues.append(
            _issue(
                "specification_materials_missing",
                f"В спецификации «{spec.name}» отсутствуют материалы и работы.",
                "materials",
                "specification",
                need.line_id,
            )
        )
    return issues


def _issue(
    code: str,
    message: str,
    field: str | None = None,
    source: str = "case",
    line_id: str | None = None,
) -> EngineerValidationIssue:
    return EngineerValidationIssue(
        code=code,
        message=message,
        field=field,
        source=source,  # type: ignore[arg-type]
        line_id=line_id,
    )


def _exclusion_reason(
    supply: EngineerSupplyItem,
    *,
    material: ResourceSpecificationMaterial,
    required_date: datetime,
) -> str | None:
    if not supply.confirmed:
        return "unconfirmed"
    if supply.reserved_for_other:
        return "reserved_for_other_order"
    if supply.safety_stock_forbidden:
        return "forbidden_safety_stock"
    if supply.quarantine:
        return "quarantine"
    if supply.defective:
        return "defective"
    if supply.blocked:
        return "blocked"
    if not supply.incoming_control_passed:
        return "incoming_control_not_passed"
    if supply.expired:
        return "overdue_without_confirmed_date"
    if not supply.suitable or not supply.use_allowed:
        return "use_not_allowed"
    if not supply.exact_match:
        return "analogue_not_approved"
    if supply.unit != material.unit and supply.unit != "base_unit":
        return "unit_mismatch"
    if material.characteristic_id and supply.characteristic_id != material.characteristic_id:
        return "characteristic_mismatch"
    if supply.available_at and _aware_datetime(supply.available_at) > _aware_datetime(
        required_date
    ):
        return "available_after_required_date"
    return None


def _criticality(
    need: EngineerNeedLine,
    *,
    net: Decimal,
    now: datetime,
    required_date: datetime,
    has_confirmed_future_supply: bool,
) -> EngineerCriticality:
    if net <= 0:
        return EngineerCriticality.NORMAL
    lead_time_breached = (
        need.procurement_lead_days is not None
        and now + timedelta(days=need.procurement_lead_days) > required_date
    )
    no_analogue = need.acceptable_analog_available is False
    critical = (
        (
            need.stage_cannot_start_without_material
            and no_analogue
            and not has_confirmed_future_supply
        )
        or lead_time_breached
        or need.critical_production_order
        or need.section_stop_risk
    )
    if critical:
        return EngineerCriticality.CRITICAL
    if need.long_lead_time or required_date <= now + timedelta(days=14):
        return EngineerCriticality.HIGH
    return EngineerCriticality.NORMAL


def _outcome(
    *,
    gross: Decimal,
    net: Decimal,
    breakdown: dict[str, Decimal],
    criticality: EngineerCriticality,
) -> EngineerOutcome:
    if criticality is EngineerCriticality.CRITICAL and net > 0:
        return EngineerOutcome.CRITICAL_SHORTAGE
    if net > 0 and gross > net:
        return EngineerOutcome.PARTIALLY_COVERED
    if net > 0:
        return EngineerOutcome.PROCUREMENT_REQUIRED
    if any(breakdown.get(value, Decimal("0")) > 0 for value in FUTURE_SUPPLY_TYPES):
        return EngineerOutcome.COVERED_BY_OPEN_ORDER
    if breakdown.get("internal_transfer", Decimal("0")) > 0:
        return EngineerOutcome.TRANSFER_REQUIRED
    return EngineerOutcome.FULLY_COVERED


def _recommendation(outcome: EngineerOutcome) -> tuple[str, str]:
    values = {
        EngineerOutcome.FULLY_COVERED: ("Склад / кладовая", "Зарезервировать и выдать материал."),
        EngineerOutcome.TRANSFER_REQUIRED: (
            "Внутреннее перемещение",
            "Согласовать внутреннее перемещение.",
        ),
        EngineerOutcome.PARTIALLY_COVERED: (
            "Наличие и закупка",
            "Зарезервировать доступное количество и передать остаточный дефицит в ОМТО.",
        ),
        EngineerOutcome.COVERED_BY_OPEN_ORDER: (
            "Открытый заказ / подтверждённое поступление",
            "Связать кейс с существующим заказом; новую закупку не создавать.",
        ),
        EngineerOutcome.PROCUREMENT_REQUIRED: (
            "Закупка",
            "Передать подтверждённый дефицит в ОМТО.",
        ),
        EngineerOutcome.CRITICAL_SHORTAGE: (
            "Срочная закупка / эскалация",
            "Эскалировать главному диспетчеру и начальнику СПП.",
        ),
        EngineerOutcome.CLARIFICATION_REQUIRED: (
            "Уточнение",
            "Вернуть исходные данные на уточнение.",
        ),
    }
    return values[outcome]


def _aggregate_selected_requirements(
    selected: list[tuple[EngineerNeedLine, ResourceSpecification]],
    *,
    default_date: datetime,
) -> list[tuple[EngineerNeedLine, ResourceSpecification]]:
    grouped: dict[
        tuple[Any, ...], tuple[EngineerNeedLine, ResourceSpecification, Decimal, list[str]]
    ] = {}
    for need, spec in selected:
        quantity = need.product_quantity or need.direct_quantity or Decimal("0")
        for material in spec.materials:
            required_date = need.required_date or default_date
            key = (
                material.nomenclature_id,
                material.characteristic_id,
                material.unit,
                need.project_id,
                required_date,
                material.production_stage_id or need.production_stage_id,
                material.use_conditions,
                material.consumption_rate,
                material.technological_loss_percent,
                need.warehouse_id,
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = (
                    need.model_copy(deep=True),
                    spec.model_copy(update={"materials": [material.model_copy(deep=True)]}),
                    quantity,
                    [need.line_id],
                )
                continue
            first_need, first_spec, total_quantity, line_ids = existing
            grouped[key] = (
                first_need,
                first_spec,
                total_quantity + quantity,
                [*line_ids, need.line_id],
            )
    result: list[tuple[EngineerNeedLine, ResourceSpecification]] = []
    for need, spec, quantity, line_ids in grouped.values():
        need.line_id = "+".join(line_ids)
        need.product_quantity = quantity
        need.direct_quantity = None
        result.append((need, spec))
    return result


def calculate_engineer_assessment(
    *,
    case: EngineerCaseInput,
    needs: list[EngineerNeedLine],
    specifications: list[ResourceSpecification],
    supplies: list[EngineerSupplyItem],
    capability_issues: list[str] | None = None,
    calculated_at: datetime | None = None,
) -> ProductionPreparationEngineerOutput:
    now = calculated_at or datetime.now(UTC)
    issues = validate_case(case, needs)
    selected: list[tuple[EngineerNeedLine, ResourceSpecification]] = []
    for need in needs:
        production_date = need.required_date or case.required_date or now
        spec = select_resource_specification(need, specifications, production_date)
        issues.extend(validate_specification(need, spec))
        if spec:
            selected.append((need, spec))
    supply_by_id: dict[str, EngineerSupplyItem] = {}
    for supply in supplies:
        previous = supply_by_id.get(supply.supply_id)
        if previous is not None and previous.model_dump() != supply.model_dump():
            issues.append(
                _issue(
                    "conflicting_supply_duplicate",
                    f"Источник обеспечения «{supply.supply_id}» получен "
                    "с противоречивыми данными; двойной учёт исключён.",
                    "supply_id",
                    "calculation",
                )
            )
        supply_by_id[supply.supply_id] = supply

    capability_messages = list(dict.fromkeys(capability_issues or []))
    if issues:
        messages = [item.message for item in issues]
        return ProductionPreparationEngineerOutput(
            case=case,
            calculated_at=now,
            evidence_fingerprint=_stable_hash(
                {"case": case.model_dump(mode="json"), "issues": messages}
            ),
            specifications=[spec for _, spec in selected],
            validation_issues=issues,
            missing_data=messages,
            excluded_capabilities=capability_messages,
            summary=(
                "Расчёт остановлен: обязательные данные отсутствуют или противоречат друг другу."
            ),
            recommended_next_step="Уточнить перечисленные данные в 1С и повторить расчёт.",
        )

    selected = _aggregate_selected_requirements(
        selected,
        default_date=case.required_date or now,
    )
    remaining = {item.supply_id: item.quantity for item in supplies}
    positions: list[EngineerAssessmentLine] = []
    for need, spec in sorted(
        selected,
        key=lambda pair: pair[0].required_date or case.required_date or now,
    ):
        product_quantity = need.product_quantity or need.direct_quantity or Decimal("0")
        required_date = need.required_date or case.required_date
        assert required_date is not None
        for material in spec.materials:
            gross = (
                product_quantity
                * material.consumption_rate
                * (Decimal("1") + material.technological_loss_percent / Decimal("100"))
            )
            warehouse_stock_before = sum(
                (
                    remaining.get(supply.supply_id, Decimal("0"))
                    for supply in supplies
                    if supply.nomenclature_id == material.nomenclature_id
                    and supply.source_type in OTHER_WAREHOUSE_TYPES
                    and (
                        not supply.warehouse_id
                        or supply.warehouse_id == case.warehouse_1c_ref
                    )
                    and _exclusion_reason(
                        supply,
                        material=material,
                        required_date=required_date,
                    )
                    is None
                ),
                Decimal("0"),
            )
            included: list[tuple[EngineerSupplyItem, Decimal]] = []
            excluded: list[EngineerExcludedSupply] = []
            for supply in sorted(
                supplies,
                key=lambda value: (
                    0
                    if value.source_type in OTHER_WAREHOUSE_TYPES
                    and (
                        not value.warehouse_id
                        or value.warehouse_id == case.warehouse_1c_ref
                    )
                    else 1,
                    _aware_datetime(value.available_at) if value.available_at else now,
                    value.supply_id,
                ),
            ):
                if supply.nomenclature_id != material.nomenclature_id:
                    continue
                reason = _exclusion_reason(supply, material=material, required_date=required_date)
                available_quantity = remaining.get(supply.supply_id, Decimal("0"))
                if reason or available_quantity <= 0:
                    excluded.append(
                        EngineerExcludedSupply(
                            supply_id=supply.supply_id,
                            source_type=supply.source_type,
                            quantity=supply.quantity,
                            reason=reason or "already_allocated",
                            evidence_id=supply.evidence_id,
                        )
                    )
                    continue
                needed = max(Decimal("0"), gross - sum((qty for _, qty in included), Decimal("0")))
                if needed <= 0:
                    break
                allocated = min(needed, available_quantity)
                remaining[supply.supply_id] = available_quantity - allocated
                included.append((supply, allocated))

            breakdown: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            breakdown_ids: dict[str, list[str]] = defaultdict(list)
            linked_documents: list[dict[str, str]] = []
            for supply, quantity in included:
                breakdown[supply.source_type] += quantity
                breakdown_ids[supply.source_type].append(supply.supply_id)
                if supply.linked_document_number or supply.linked_document_ref:
                    linked_documents.append(
                        {
                            "number": supply.linked_document_number or "",
                            "ref": supply.linked_document_ref or "",
                            "source_type": supply.source_type,
                        }
                    )
            available = sum(breakdown.values(), Decimal("0"))
            net = max(Decimal("0"), gross - available)
            criticality = _criticality(
                need,
                net=net,
                now=now,
                required_date=required_date,
                has_confirmed_future_supply=any(
                    breakdown.get(value, Decimal("0")) > 0 for value in FUTURE_SUPPLY_TYPES
                ),
            )
            outcome = _outcome(
                gross=gross,
                net=net,
                breakdown=breakdown,
                criticality=criticality,
            )
            coverage_method, recommendation = _recommendation(outcome)
            free_stock = sum(
                quantity
                for supply, quantity in included
                if supply.source_type in OTHER_WAREHOUSE_TYPES
                and (not supply.warehouse_id or supply.warehouse_id == case.warehouse_1c_ref)
            )
            other_stock = sum(
                quantity
                for supply, quantity in included
                if supply.source_type in OTHER_WAREHOUSE_TYPES
                and supply.warehouse_id
                and supply.warehouse_id != case.warehouse_1c_ref
            )
            warehouse_stock_used = free_stock
            warehouse_stock_remaining = max(
                Decimal("0"),
                warehouse_stock_before - warehouse_stock_used,
            )
            confirmed_arrivals = sum(
                breakdown.get(value, Decimal("0")) for value in FUTURE_SUPPLY_TYPES
            )
            impact = None
            if criticality is EngineerCriticality.CRITICAL:
                impact = EngineerCriticalImpact(
                    production_order=case.production_order_number or case.production_order_1c_ref,
                    production_stage=material.production_stage_name or need.production_stage_name,
                    shortage_start_date=required_date,
                    possible_stop_date=required_date if need.section_stop_risk else None,
                    unprovided_product_quantity=(
                        net / material.consumption_rate if material.consumption_rate > 0 else None
                    ),
                    consequence=(
                        "Производственный этап не сможет начаться или участок будет остановлен."
                        if need.stage_cannot_start_without_material or need.section_stop_risk
                        else "Нарушается срок критического производственного заказа."
                    ),
                    recommended_priority="Критический",
                )
            positions.append(
                EngineerAssessmentLine(
                    line_id=f"{need.line_id}:{material.line_id}",
                    nomenclature_id=material.nomenclature_id,
                    nomenclature_name=material.nomenclature_name,
                    characteristic_id=material.characteristic_id,
                    characteristic_name=material.characteristic_name,
                    unit=material.unit or "",
                    production_order=case.production_order_number or case.production_order_1c_ref,
                    production_stage=material.production_stage_name or need.production_stage_name,
                    product_quantity=product_quantity,
                    consumption_rate=material.consumption_rate,
                    technological_loss_percent=material.technological_loss_percent,
                    gross_requirement=gross,
                    free_stock=free_stock,
                    available_other_warehouses=other_stock,
                    warehouse_stock_before=warehouse_stock_before,
                    warehouse_stock_used=warehouse_stock_used,
                    warehouse_stock_remaining=warehouse_stock_remaining,
                    confirmed_arrivals=confirmed_arrivals,
                    total_available_supply=available,
                    net_requirement=net,
                    required_date=required_date,
                    criticality=criticality,
                    outcome=outcome,
                    coverage_method=coverage_method,
                    recommendation=recommendation,
                    specification_id=spec.specification_id,
                    specification_version=spec.version,
                    supply_breakdown=[
                        EngineerSupplyBreakdown(
                            source_type=source_type,
                            quantity=quantity,
                            supply_ids=breakdown_ids[source_type],
                        )
                        for source_type, quantity in sorted(breakdown.items())
                    ],
                    excluded_supply=excluded,
                    linked_documents=linked_documents,
                    critical_impact=impact,
                    evidence_ids=sorted(
                        {
                            value
                            for value in [
                                spec.evidence_id,
                                *(item.evidence_id for item, _ in included),
                            ]
                            if value
                        }
                    ),
                )
            )

    deficits = [line for line in positions if line.net_requirement > 0]
    critical = [line for line in positions if line.criticality is EngineerCriticality.CRITICAL]
    if critical:
        summary = f"Обнаружено критических дефицитов: {len(critical)}."
        next_step = "Эскалировать критические позиции и передать подтверждённый дефицит в ОМТО."
    elif deficits:
        summary = f"Обнаружено дефицитных позиций: {len(deficits)}."
        next_step = "Передать только непокрытый дефицит в контур снабжения."
    else:
        summary = "Потребность полностью покрыта подтверждёнными источниками."
        next_step = "Связать подтверждённые источники с кейсом; новую закупку не создавать."
    evidence_ids = sorted({evidence_id for line in positions for evidence_id in line.evidence_ids})
    fingerprint = _stable_hash(
        {
            "case": case.model_dump(mode="json"),
            "specifications": [spec.model_dump(mode="json") for _, spec in selected],
            "supplies": [item.model_dump(mode="json") for item in supplies],
        }
    )
    return ProductionPreparationEngineerOutput(
        case=case,
        calculated_at=now,
        evidence_fingerprint=fingerprint,
        specifications=[spec for _, spec in selected],
        positions=positions,
        excluded_capabilities=capability_messages,
        evidence_ids=evidence_ids,
        summary=summary,
        recommended_next_step=next_step,
    )


__all__ = [
    "calculate_engineer_assessment",
    "select_resource_specification",
    "validate_case",
    "validate_specification",
]
