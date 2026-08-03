from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Any

from app.agents.production_dispatcher_agent.schemas import (
    DispatcherAssessmentLine,
    DispatcherCaseInput,
    DispatcherExcludedSupply,
    DispatcherNeedLine,
    DispatcherOutcome,
    DispatcherRecommendation,
    DispatcherSupplyBreakdown,
    DispatcherSupplyItem,
    DispatcherUrgency,
    DispatcherValidationIssue,
    ProductionDispatcherOutput,
)

FREE_STOCK_TYPES = {"warehouse", "store_room"}
EXPECTED_TYPES = {"in_transit", "in_progress", "work_in_progress"}
CONFIRMED_ARRIVAL_TYPES = {"supplier_order", "internal_transfer", "semifinished", *EXPECTED_TYPES}
OTHER_WAREHOUSE_TYPES = {"warehouse", "store_room"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ceil_multiple(quantity: Decimal, multiple: Decimal | None) -> Decimal:
    if multiple is None or multiple <= 0 or quantity <= 0:
        return quantity
    units = (quantity / multiple).to_integral_value(rounding=ROUND_CEILING)
    return units * multiple


def validate_case(
    case: DispatcherCaseInput,
    needs: list[DispatcherNeedLine],
) -> list[DispatcherValidationIssue]:
    issues: list[DispatcherValidationIssue] = []
    if not case.source_1c_ref:
        issues.append(
            DispatcherValidationIssue(
                code="missing_source_ref",
                message="Отсутствует ссылка на документ-основание 1С.",
                field="source_1c_ref",
            )
        )
    if not needs:
        issues.append(
            DispatcherValidationIssue(
                code="no_positions",
                message="В кейсе нет позиций для расчёта диспетчера.",
                field="positions",
            )
        )
    for need in needs:
        if not need.nomenclature_id:
            issues.append(
                DispatcherValidationIssue(
                    code="missing_nomenclature",
                    message="У позиции не указана номенклатура.",
                    line_id=need.line_id,
                    field="nomenclature_id",
                )
            )
        if need.minimum_stock is None and need.reorder_point is None:
            issues.append(
                DispatcherValidationIssue(
                    code="missing_minimum",
                    message=(
                        f"Для «{need.nomenclature_name}» не задан минимум "
                        "или точка заказа."
                    ),
                    line_id=need.line_id,
                    field="minimum_stock",
                    blocking=False,
                )
            )
    return issues


def _exclusion_reason(supply: DispatcherSupplyItem) -> str | None:
    if supply.reserved_for_other:
        return "reserved_for_other"
    if supply.quarantine:
        return "quarantine"
    if supply.defective:
        return "defective"
    if supply.blocked:
        return "blocked"
    if supply.expired:
        return "expired"
    if not supply.incoming_control_passed and supply.source_type in FREE_STOCK_TYPES:
        return "incoming_control_failed"
    if not supply.confirmed and supply.source_type in CONFIRMED_ARRIVAL_TYPES:
        return "unconfirmed_supply"
    if not supply.suitable or not supply.use_allowed:
        return "unsuitable"
    if not supply.exact_match:
        return "analogue_not_approved"
    if supply.quantity <= 0:
        return "non_positive"
    return None


def _resolve_thresholds(
    need: DispatcherNeedLine,
    *,
    case_coefficient: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    coefficient = need.stock_growth_coefficient or case_coefficient or Decimal("1")
    if coefficient <= 0:
        coefficient = Decimal("1")
    raw_min = _d(need.minimum_stock)
    raw_max = _d(need.maximum_stock)
    if raw_max <= 0 and need.quantity > 0:
        raw_max = need.quantity
    if raw_min <= 0 and raw_max > 0:
        raw_min = (raw_max / Decimal("3")).quantize(Decimal("0.001"))
    minimum = (raw_min * coefficient).quantize(Decimal("0.001"))
    maximum = (raw_max * coefficient).quantize(Decimal("0.001"))
    if need.reorder_point is not None:
        reorder_point = (_d(need.reorder_point) * coefficient).quantize(Decimal("0.001"))
    elif need.daily_consumption and need.lead_time_days is not None:
        reorder_point = (
            need.daily_consumption * Decimal(need.lead_time_days) + minimum
        ).quantize(Decimal("0.001"))
    else:
        reorder_point = minimum
    if reorder_point < minimum:
        reorder_point = minimum
    if maximum < reorder_point:
        maximum = reorder_point
    return minimum, maximum, reorder_point, coefficient


def _urgency(
    *,
    below_minimum: bool,
    below_reorder: bool,
    net_deficit: Decimal,
    wait_allowed: bool,
    required_date: datetime | None,
    now: datetime,
) -> DispatcherUrgency:
    if below_minimum or (net_deficit > 0 and not wait_allowed):
        return DispatcherUrgency.CRITICAL
    if below_reorder or net_deficit > 0:
        if required_date and _aware(required_date) and _aware(required_date) <= now + timedelta(days=3):
            return DispatcherUrgency.HIGH
        return DispatcherUrgency.HIGH if below_reorder else DispatcherUrgency.NORMAL
    return DispatcherUrgency.NORMAL


def calculate_dispatcher_assessment(
    *,
    case: DispatcherCaseInput,
    needs: list[DispatcherNeedLine],
    supplies: list[DispatcherSupplyItem],
    capability_issues: list[str] | None = None,
    calculated_at: datetime | None = None,
) -> ProductionDispatcherOutput:
    now = _aware(calculated_at) or datetime.now(UTC)
    capability_issues = list(dict.fromkeys(capability_issues or []))
    validation_issues = validate_case(case, needs)
    missing_data = [
        issue.message for issue in validation_issues if issue.blocking
    ]
    missing_data.extend(capability_issues)

    remaining = {item.supply_id: item.quantity for item in supplies}
    positions: list[DispatcherAssessmentLine] = []
    evidence_ids: list[str] = []

    if missing_data and any(issue.blocking for issue in validation_issues):
        return ProductionDispatcherOutput(
            case=case,
            calculated_at=now,
            evidence_fingerprint=_stable_hash({"case": case.case_id, "missing": missing_data}),
            positions=[],
            validation_issues=validation_issues,
            missing_data=missing_data,
            excluded_capabilities=capability_issues,
            summary="Недостаточно данных для расчёта диспетчера.",
            recommended_next_step="Уточнить обязательные параметры точки заказа и остатков.",
            decision_kind="critical_acknowledgement",
        )

    for need in needs:
        minimum, maximum, reorder_point, coefficient = _resolve_thresholds(
            need,
            case_coefficient=case.stock_growth_coefficient,
        )
        required_date = _aware(need.required_date) or _aware(case.required_date)
        production_demand = _d(need.production_deficit, need.quantity)

        included: list[tuple[DispatcherSupplyItem, Decimal]] = []
        excluded: list[DispatcherExcludedSupply] = []
        for supply in sorted(
            supplies,
            key=lambda value: (
                0 if value.source_type in FREE_STOCK_TYPES and (
                    not value.warehouse_id
                    or value.warehouse_id == (need.warehouse_id or case.warehouse_1c_ref)
                ) else 1,
                _aware(value.available_at) or now,
                value.supply_id,
            ),
        ):
            if supply.nomenclature_id != need.nomenclature_id:
                continue
            reason = _exclusion_reason(supply)
            available_quantity = remaining.get(supply.supply_id, Decimal("0"))
            if reason or available_quantity <= 0:
                excluded.append(
                    DispatcherExcludedSupply(
                        supply_id=supply.supply_id,
                        source_type=supply.source_type,
                        quantity=supply.quantity,
                        reason=reason or "already_allocated",
                        evidence_id=supply.evidence_id,
                    )
                )
                continue
            # For stock assessment we keep full remaining free/expected amounts,
            # allocation happens only for production deficit coverage.
            included.append((supply, available_quantity))

        free_stock = sum(
            (
                qty
                for supply, qty in included
                if supply.source_type in FREE_STOCK_TYPES
                and (
                    not supply.warehouse_id
                    or supply.warehouse_id == (need.warehouse_id or case.warehouse_1c_ref)
                )
            ),
            Decimal("0"),
        )
        store_room_stock = sum(
            (
                qty
                for supply, qty in included
                if supply.source_type == "store_room"
            ),
            Decimal("0"),
        )
        other_stock = sum(
            (
                qty
                for supply, qty in included
                if supply.source_type in OTHER_WAREHOUSE_TYPES
                and supply.warehouse_id
                and supply.warehouse_id != (need.warehouse_id or case.warehouse_1c_ref)
            ),
            Decimal("0"),
        )
        expected_in_transit = sum(
            (qty for supply, qty in included if supply.source_type == "in_transit"),
            Decimal("0"),
        )
        expected_in_progress = sum(
            (
                qty
                for supply, qty in included
                if supply.source_type in {"in_progress", "work_in_progress"}
            ),
            Decimal("0"),
        )
        expected_total = expected_in_transit + expected_in_progress
        confirmed_arrivals = sum(
            (
                qty
                for supply, qty in included
                if supply.source_type in CONFIRMED_ARRIVAL_TYPES and supply.confirmed
            ),
            Decimal("0"),
        )
        stock_position = free_stock + confirmed_arrivals - production_demand
        if stock_position < 0:
            stock_position = Decimal("0")
        # Forecast: free + expected - production demand
        forecast_stock = free_stock + expected_total - production_demand

        below_minimum = free_stock < minimum
        below_reorder = stock_position < reorder_point or forecast_stock < reorder_point
        available_to_date = free_stock + confirmed_arrivals + other_stock
        net_deficit = max(Decimal("0"), production_demand - available_to_date)
        if below_minimum and net_deficit <= 0:
            # Need to restore stock to maximum even if current production is covered.
            net_deficit = max(Decimal("0"), maximum - stock_position)

        recommended_qty = max(Decimal("0"), maximum - stock_position)
        if production_demand > available_to_date:
            recommended_qty = max(recommended_qty, production_demand - available_to_date)
        recommended_qty = _ceil_multiple(recommended_qty, need.package_multiple)

        wait_allowed = True
        if need.lead_time_days is not None and required_date is not None:
            earliest = now + timedelta(days=need.lead_time_days)
            wait_allowed = earliest <= required_date

        recommendations: list[DispatcherRecommendation] = []
        if free_stock >= production_demand and not below_minimum:
            outcome = DispatcherOutcome.RESERVE_STOCK
            coverage = "Свободный остаток"
            recommendation = "Зарезервировать свободный остаток на складе/в кладовой."
            recommendations.append(
                DispatcherRecommendation(
                    method="reserve_stock",
                    quantity=production_demand,
                    label="Резервирование остатка",
                    details=f"Свободный остаток {free_stock}, потребность {production_demand}.",
                )
            )
        elif free_stock >= production_demand and below_minimum:
            outcome = DispatcherOutcome.PROCUREMENT_REQUIRED
            coverage = "Остаток + восстановление запаса"
            recommendation = (
                "Потребность покрывается остатком, но запас уйдёт ниже минимума — "
                "нужно пополнение."
            )
            recommendations.append(
                DispatcherRecommendation(
                    method="reserve_stock",
                    quantity=production_demand,
                    label="Резервирование остатка",
                    details="Текущая потребность покрыта, требуется восстановление минимума.",
                )
            )
            recommendations.append(
                DispatcherRecommendation(
                    method="procurement",
                    quantity=recommended_qty,
                    label="Закупка для восстановления запаса",
                    details=f"До максимума {maximum}, позиция запаса {stock_position}.",
                )
            )
        elif other_stock > 0 and free_stock + other_stock >= production_demand:
            outcome = DispatcherOutcome.TRANSFER_PROPOSED
            coverage = "Внутреннее перемещение"
            recommendation = "Предложить перемещение со другого склада."
            transfer_qty = min(other_stock, max(Decimal("0"), production_demand - free_stock))
            recommendations.append(
                DispatcherRecommendation(
                    method="transfer",
                    quantity=transfer_qty,
                    label="Предложение перемещения",
                    details=f"Доступно на других складах: {other_stock}.",
                )
            )
        elif confirmed_arrivals > 0 and free_stock + confirmed_arrivals >= production_demand:
            outcome = DispatcherOutcome.LINK_INCOMING
            coverage = "Подтверждённое поступление"
            recommendation = "Привязать ожидаемое поступление к потребности."
            recommendations.append(
                DispatcherRecommendation(
                    method="link_incoming",
                    quantity=max(Decimal("0"), production_demand - free_stock),
                    label="Привязка поступления",
                    details=(
                        f"Ожидаемые (в пути/в работе): {expected_total}; "
                        f"подтверждённые поступления: {confirmed_arrivals}."
                    ),
                )
            )
        elif net_deficit <= 0 and not below_reorder:
            outcome = DispatcherOutcome.ALREADY_COVERED
            coverage = "Покрыто существующим обеспечением"
            recommendation = "Дополнительная закупка не требуется."
            recommendations.append(
                DispatcherRecommendation(
                    method="none",
                    quantity=Decimal("0"),
                    label="Без действий",
                    details="Позиция запаса не ниже точки заказа.",
                    requires_confirmation=False,
                )
            )
        else:
            outcome = (
                DispatcherOutcome.CRITICAL_SHORTAGE
                if below_minimum or not wait_allowed
                else DispatcherOutcome.PROCUREMENT_REQUIRED
            )
            coverage = "Закупка"
            recommendation = "Передать решение о закупке на подтверждение."
            recommendations.append(
                DispatcherRecommendation(
                    method="procurement",
                    quantity=recommended_qty,
                    label="Закупка",
                    details=(
                        f"Чистый дефицит {net_deficit}, рекомендуемое количество "
                        f"{recommended_qty} (до максимума {maximum})."
                    ),
                )
            )

        urgency = _urgency(
            below_minimum=below_minimum,
            below_reorder=below_reorder,
            net_deficit=net_deficit,
            wait_allowed=wait_allowed,
            required_date=required_date,
            now=now,
        )

        breakdown_map: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        breakdown_ids: dict[str, list[str]] = defaultdict(list)
        for supply, qty in included:
            if qty <= 0:
                continue
            breakdown_map[supply.source_type] += qty
            breakdown_ids[supply.source_type].append(supply.supply_id)
            if supply.evidence_id:
                evidence_ids.append(supply.evidence_id)

        line_evidence = [
            supply.evidence_id
            for supply, _ in included
            if supply.evidence_id
        ]
        positions.append(
            DispatcherAssessmentLine(
                line_id=need.line_id,
                nomenclature_id=need.nomenclature_id,
                nomenclature_name=need.nomenclature_name,
                characteristic_id=need.characteristic_id,
                characteristic_name=need.characteristic_name,
                unit=need.unit or "",
                warehouse_id=need.warehouse_id or case.warehouse_1c_ref,
                minimum_stock=minimum,
                maximum_stock=maximum,
                reorder_point=reorder_point,
                stock_growth_coefficient=coefficient,
                free_stock=free_stock,
                store_room_stock=store_room_stock,
                expected_in_transit=expected_in_transit,
                expected_in_progress=expected_in_progress,
                expected_total=expected_total,
                confirmed_arrivals=confirmed_arrivals,
                available_other_warehouses=other_stock,
                production_demand=production_demand,
                stock_position=stock_position,
                forecast_stock=forecast_stock,
                below_minimum=below_minimum,
                below_reorder_point=below_reorder,
                net_deficit=net_deficit,
                recommended_order_quantity=recommended_qty,
                required_date=required_date,
                urgency=urgency,
                wait_allowed=wait_allowed,
                outcome=outcome,
                coverage_method=coverage,
                recommendation=recommendation,
                recommendations=recommendations,
                supply_breakdown=[
                    DispatcherSupplyBreakdown(
                        source_type=source_type,
                        quantity=quantity,
                        supply_ids=breakdown_ids[source_type],
                    )
                    for source_type, quantity in breakdown_map.items()
                ],
                excluded_supply=excluded,
                formulas={
                    "minimum": f"min_raw × К-т = {minimum}",
                    "maximum": f"max_raw × К-т = {maximum}",
                    "reorder_point": f"точка заказа = {reorder_point}",
                    "stock_position": (
                        f"свободный ({free_stock}) + подтверждённые поступления "
                        f"({confirmed_arrivals}) − потребность ({production_demand}) "
                        f"= {stock_position}"
                    ),
                    "forecast_stock": (
                        f"свободный ({free_stock}) + ожидаемые "
                        f"({expected_total}) − потребность ({production_demand}) "
                        f"= {forecast_stock}"
                    ),
                    "expected": (
                        f"ожидаемые = в пути ({expected_in_transit}) + "
                        f"в работе ({expected_in_progress}) = {expected_total}"
                    ),
                    "order_quantity": (
                        f"max(0, максимум ({maximum}) − позиция ({stock_position})) "
                        f"= {recommended_qty}"
                    ),
                },
                evidence_ids=line_evidence,
            )
        )

    has_deficit = any(
        position.net_deficit > 0
        or position.outcome
        in {
            DispatcherOutcome.PROCUREMENT_REQUIRED,
            DispatcherOutcome.CRITICAL_SHORTAGE,
            DispatcherOutcome.TRANSFER_PROPOSED,
            DispatcherOutcome.LINK_INCOMING,
        }
        for position in positions
    )
    has_critical = any(
        position.urgency is DispatcherUrgency.CRITICAL
        or position.outcome is DispatcherOutcome.CRITICAL_SHORTAGE
        for position in positions
    )
    if validation_issues and any(issue.blocking for issue in validation_issues):
        decision_kind = "critical_acknowledgement"
        summary = "Требуется уточнение данных по точке заказа."
        next_step = "Вернуть кейс на уточнение обязательных параметров."
    elif has_critical or has_deficit:
        decision_kind = "supply_confirmation"
        critical_count = sum(1 for item in positions if item.below_minimum)
        deficit_count = sum(1 for item in positions if item.net_deficit > 0)
        summary = (
            f"Ниже минимума: {critical_count}. Требуют пополнения: {deficit_count}."
        )
        next_step = "Подтвердить выбранный способ обеспечения."
    else:
        decision_kind = "none"
        summary = "Запас и ожидаемое обеспечение покрывают потребность."
        next_step = "Передать результат оркестратору без новой закупки."

    fingerprint = _stable_hash(
        {
            "case_id": case.case_id,
            "source_version": case.source_data_version,
            "positions": [item.model_dump(mode="json") for item in positions],
            "issues": [item.model_dump(mode="json") for item in validation_issues],
        }
    )
    return ProductionDispatcherOutput(
        case=case,
        calculated_at=now,
        evidence_fingerprint=fingerprint,
        positions=positions,
        validation_issues=validation_issues,
        missing_data=missing_data,
        excluded_capabilities=capability_issues,
        summary=summary,
        recommended_next_step=next_step,
        decision_kind=decision_kind,  # type: ignore[arg-type]
    )


__all__ = [
    "calculate_dispatcher_assessment",
    "validate_case",
]
