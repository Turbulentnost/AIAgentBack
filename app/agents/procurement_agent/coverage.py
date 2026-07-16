from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from app.agents.procurement_agent.schemas import (
    ProcurementExcludedSupply,
    ProcurementHumanActionCard,
    ProcurementKT1Result,
    ProcurementNeedPosition,
    ProcurementPositionCoverage,
    ProcurementSupplyBreakdown,
    ProcurementSupplyItem,
)


def _gross_requirement(position: ProcurementNeedPosition) -> tuple[Decimal, str]:
    if position.gross_quantity is not None:
        return position.gross_quantity, "direct_material_quantity"
    if position.product_quantity is None or position.consumption_rate is None:
        return Decimal("0"), "missing_calculation_inputs"
    return (
        position.product_quantity * position.consumption_rate * position.loss_factor,
        "production_norm",
    )


def _exclusion_reason(item: ProcurementSupplyItem) -> str | None:
    if not item.confirmed:
        return "unconfirmed"
    if item.reserved_for_other:
        return "reserved_for_other_need"
    if item.quarantine:
        return "quarantine"
    if item.defective:
        return "defective"
    if not item.incoming_control_passed:
        return "incoming_control_not_passed"
    if item.expired:
        return "expired"
    if item.illiquid:
        return "illiquid_without_permission"
    if not item.suitable:
        return "unsuitable"
    if not item.exact_match:
        return "analogue_requires_human"
    return None


def calculate_coverage(
    *,
    case_id: str,
    source_basis: dict,
    positions: list[ProcurementNeedPosition],
    supplies: list[ProcurementSupplyItem],
    evidence_ids: list[str],
    data_issues: list[str] | None = None,
    completed_at: datetime | None = None,
) -> ProcurementKT1Result:
    issues = list(data_issues or [])
    by_nomenclature: dict[str, list[ProcurementSupplyItem]] = defaultdict(list)
    for item in supplies:
        by_nomenclature[item.nomenclature_id].append(item)

    position_results: list[ProcurementPositionCoverage] = []
    human_reasons: list[str] = []
    for position in positions:
        gross, calculation_source = _gross_requirement(position)
        warnings: list[str] = []
        insufficient = False
        if calculation_source == "missing_calculation_inputs":
            insufficient = True
            warnings.append("Недостаточно данных для расчёта валовой потребности.")
        if position.match_status != "exact" or not position.nomenclature_id:
            insufficient = True
            reason = (
                "Неоднозначное сопоставление номенклатуры."
                if position.match_status == "ambiguous"
                else "Номенклатура не сопоставлена."
            )
            warnings.append(reason)
            human_reasons.append(reason)
        if position.possible_units and (
            len(set(position.possible_units)) > 1 or position.unit not in position.possible_units
        ):
            insufficient = True
            warnings.append("Обнаружены несовместимые единицы измерения.")
            human_reasons.append("Требуется выбрать единицу измерения.")

        included: dict[str, ProcurementSupplyItem] = {}
        excluded: list[ProcurementExcludedSupply] = []
        duplicate_conflict = False
        candidates = by_nomenclature.get(position.nomenclature_id or "", [])
        for item in sorted(candidates, key=lambda value: (value.source_type, value.supply_id)):
            reason = _exclusion_reason(item)
            if item.unit != position.unit:
                reason = "unit_mismatch"
                insufficient = True
                human_reasons.append("Единицы измерения потребности и обеспечения не совпадают.")
            previous = included.get(item.supply_id)
            if previous is not None:
                if previous.quantity != item.quantity or previous.unit != item.unit:
                    duplicate_conflict = True
                    insufficient = True
                    human_reasons.append(
                        f"Противоречивые данные по обеспечению {item.supply_id}; "
                        "двойной учёт исключён."
                    )
                reason = "duplicate_supply"
            if reason is not None:
                excluded.append(
                    ProcurementExcludedSupply(
                        supply_id=item.supply_id,
                        source_type=item.source_type,
                        quantity=item.quantity,
                        reason=reason,
                        evidence_id=item.evidence_id,
                    )
                )
                continue
            included[item.supply_id] = item

        breakdown_quantities: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        breakdown_ids: dict[str, list[str]] = defaultdict(list)
        for item in included.values():
            breakdown_quantities[item.source_type] += item.quantity
            breakdown_ids[item.source_type].append(item.supply_id)
        available = sum(breakdown_quantities.values(), Decimal("0"))
        net = max(Decimal("0"), gross - available)
        if insufficient or duplicate_conflict:
            status = "data_insufficient"
        elif net == 0:
            status = "covered"
        elif available > 0:
            status = "partially_covered"
        else:
            status = "uncovered"

        position_results.append(
            ProcurementPositionCoverage(
                line_id=position.line_id,
                nomenclature_id=position.nomenclature_id,
                nomenclature_name=position.nomenclature_name,
                unit=position.unit,
                required_date=position.required_date,
                gross_requirement=gross,
                gross_calculation_source=calculation_source,
                available_supply=available,
                supply_breakdown=[
                    ProcurementSupplyBreakdown(
                        source_type=source_type,
                        quantity=quantity,
                        supply_ids=breakdown_ids[source_type],
                    )
                    for source_type, quantity in sorted(breakdown_quantities.items())
                ],
                excluded_supply=excluded,
                net_requirement=net,
                status=status,
                warnings=warnings,
                evidence_ids=sorted(
                    {
                        item.evidence_id
                        for item in candidates
                        if item.evidence_id
                    }
                ),
            )
        )

    statuses = {position.status for position in position_results}
    if issues or "data_insufficient" in statuses or not position_results:
        overall_status = "data_insufficient"
    elif statuses == {"covered"}:
        overall_status = "covered"
    elif "uncovered" in statuses and statuses <= {"uncovered"}:
        overall_status = "uncovered"
    else:
        overall_status = "partially_covered"

    critical = [
        position.line_id
        for position in position_results
        if position.status in {"uncovered", "data_insufficient"}
    ]
    human_action = None
    if overall_status == "data_insufficient":
        requested = list(dict.fromkeys([*issues, *human_reasons]))
        human_action = ProcurementHumanActionCard(
            stopped_by="Недостаточно достоверных данных для завершения КТ1.",
            obtained_data=evidence_ids,
            requested_from_human=requested or ["Предоставить отсутствующие данные."],
            options=["Уточнить исходные данные", "Восстановить недоступную возможность MCP"],
            risks=["Ошибочный расчёт дефицита при продолжении без подтверждения."],
            evidence_ids=evidence_ids,
        )

    next_step = (
        "Передать подтверждённый дефицит в следующий контур планирования закупки."
        if overall_status in {"partially_covered", "uncovered"}
        else (
            "Завершить КТ1: потребность обеспечена."
            if overall_status == "covered"
            else "Запросить уточнение у ответственного пользователя."
        )
    )
    return ProcurementKT1Result(
        case_id=case_id,
        status=overall_status,
        source_basis=source_basis,
        positions=position_results,
        critical_positions=critical,
        missing_data=issues,
        evidence_ids=evidence_ids,
        warnings=list(dict.fromkeys(human_reasons)),
        recommended_next_step=next_step,
        human_action_required=human_action,
        completed_at=completed_at or datetime.now(UTC),
    )


__all__ = ["calculate_coverage"]
