from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.agents.warehouse_picker_agent.schemas import (
    PickerAssessmentLine,
    PickerCaseInput,
    PickerNeedLine,
    PickerOutcome,
    PickerSupplyItem,
    PickerValidationIssue,
    WarehousePickerOutput,
)

_STOCK_SOURCES = frozenset({"store_room", "warehouse"})


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_case(
    case: PickerCaseInput,
    needs: list[PickerNeedLine],
) -> list[PickerValidationIssue]:
    issues: list[PickerValidationIssue] = []
    if not case.source_1c_ref:
        issues.append(
            PickerValidationIssue(
                code="missing_source_ref",
                message="Отсутствует ссылка на заказ материалов 1С.",
                field="source_1c_ref",
            )
        )
    if not needs:
        issues.append(
            PickerValidationIssue(
                code="no_positions",
                message="В заказе нет позиций для проверки кладовой.",
                field="positions",
            )
        )
    for need in needs:
        if not need.nomenclature_id:
            issues.append(
                PickerValidationIssue(
                    code="missing_nomenclature",
                    message="У позиции не указана номенклатура.",
                    line_id=need.line_id,
                    field="nomenclature_id",
                )
            )
    return issues


def _target_warehouse(need: PickerNeedLine, case: PickerCaseInput) -> str | None:
    return need.warehouse_id or case.warehouse_1c_ref


def _exclusion_reason(
    supply: PickerSupplyItem,
    *,
    need: PickerNeedLine,
    case: PickerCaseInput,
) -> str | None:
    target_warehouse = _target_warehouse(need, case)
    if (
        target_warehouse
        and supply.warehouse_id
        and supply.warehouse_id != target_warehouse
    ):
        return "other_warehouse"
    if supply.reserved_for_other:
        return "reserved_for_other"
    if supply.assignment_id:
        if need.assignment_id and supply.assignment_id == need.assignment_id:
            pass
        else:
            return "reserved_for_assignment"
    if supply.quarantine or supply.source_type == "quarantine":
        return "quarantine"
    if supply.defective:
        return "defective"
    if supply.blocked or supply.source_type == "blocked":
        return "blocked"
    if not supply.available_for_issue:
        return "not_available_for_issue"
    if not supply.suitable or not supply.use_allowed:
        return "unsuitable"
    if not supply.exact_match:
        return "analogue_not_approved"
    if supply.quantity <= 0:
        return "non_positive"
    return None


def calculate_picker_assessment(
    *,
    case: PickerCaseInput,
    needs: list[PickerNeedLine],
    supplies: list[PickerSupplyItem],
    capability_issues: list[str] | None = None,
    calculated_at: datetime | None = None,
) -> WarehousePickerOutput:
    now = calculated_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    capability_issues = list(dict.fromkeys(capability_issues or []))
    validation_issues = validate_case(case, needs)
    missing_data = [issue.message for issue in validation_issues if issue.blocking]
    missing_data.extend(capability_issues)

    if missing_data and any(issue.blocking for issue in validation_issues):
        return WarehousePickerOutput(
            case=case,
            calculated_at=now,
            evidence_fingerprint=_stable_hash(
                {"case": case.case_id, "missing": missing_data}
            ),
            positions=[],
            validation_issues=validation_issues,
            missing_data=missing_data,
            excluded_capabilities=capability_issues,
            summary="Недостаточно данных для заключения по складскому наличию.",
            recommended_next_step="Уточнить позиции заказа и остатки склада в 1С.",
            decision_kind="critical_acknowledgement",
        )

    positions: list[PickerAssessmentLine] = []
    for need in needs:
        target_warehouse = _target_warehouse(need, case)
        matching: list[PickerSupplyItem] = []
        excluded: list[dict[str, Any]] = []
        reserved_other = Decimal("0")
        soft_reserved = Decimal("0")
        for supply in supplies:
            if supply.nomenclature_id != need.nomenclature_id:
                continue
            # Soft reserve from ЗапасыИПотребности (РезервироватьНаСкладе):
            # reduces Доступно even when ТоварыНаСкладах has no Назначение.
            if supply.source_type == "reservation" or (
                supply.reserved_for_other and supply.source_type not in _STOCK_SOURCES
            ):
                if (
                    target_warehouse
                    and supply.warehouse_id
                    and supply.warehouse_id != target_warehouse
                ):
                    excluded.append(
                        {
                            "supply_id": supply.supply_id,
                            "source_type": supply.source_type,
                            "quantity": str(supply.quantity),
                            "warehouse_id": supply.warehouse_id,
                            "assignment_id": supply.assignment_id,
                            "assignment_name": supply.assignment_name,
                            "reason": "other_warehouse",
                        }
                    )
                    continue
                soft_reserved += supply.quantity
                reserved_other += supply.quantity
                excluded.append(
                    {
                        "supply_id": supply.supply_id,
                        "source_type": supply.source_type,
                        "quantity": str(supply.quantity),
                        "warehouse_id": supply.warehouse_id,
                        "assignment_id": supply.assignment_id,
                        "assignment_name": supply.assignment_name,
                        "reason": "reserved_for_other",
                    }
                )
                continue
            reason = _exclusion_reason(supply, need=need, case=case)
            if reason is not None:
                if reason in {"reserved_for_assignment", "reserved_for_other"}:
                    reserved_other += supply.quantity
                excluded.append(
                    {
                        "supply_id": supply.supply_id,
                        "source_type": supply.source_type,
                        "quantity": str(supply.quantity),
                        "warehouse_id": supply.warehouse_id,
                        "assignment_id": supply.assignment_id,
                        "assignment_name": supply.assignment_name,
                        "reason": reason,
                    }
                )
                continue
            matching.append(supply)

        store_room = sum(
            (item.quantity for item in matching if item.source_type == "store_room"),
            Decimal("0"),
        )
        warehouse_stock = sum(
            (item.quantity for item in matching if item.source_type in _STOCK_SOURCES),
            Decimal("0"),
        )
        accounting = sum(
            (
                item.accounting_quantity
                if item.accounting_quantity is not None
                else item.quantity
                for item in matching
                if item.source_type in _STOCK_SOURCES
            ),
            Decimal("0"),
        )
        factual = sum(
            (
                item.factual_quantity
                if item.factual_quantity is not None
                else item.quantity
                for item in matching
                if item.source_type in _STOCK_SOURCES
            ),
            Decimal("0"),
        )
        # Доступно = остаток склада кейса − чужое назначение − резерв на складе.
        physical = warehouse_stock if warehouse_stock > 0 else store_room
        available = physical - soft_reserved
        if available < 0:
            available = Decimal("0")
        discrepancy = abs(accounting - factual)
        has_discrepancy = discrepancy > 0 and accounting > 0 and factual >= 0

        requested = need.requested_quantity
        if has_discrepancy and available < requested:
            outcome = PickerOutcome.DISCREPANCY_RETURN
            confirmed_available = Decimal("0")
            quantity_to_issue = Decimal("0")
            confirmed_deficit = requested
            quantity_to_purchase = requested
            recommendation = (
                "Обнаружено расхождение учёта и факта — вернуть кейс "
                "до устранения расхождений."
            )
            issue_allowed = False
        elif available >= requested:
            outcome = PickerOutcome.FULLY_AVAILABLE
            confirmed_available = requested
            quantity_to_issue = requested
            confirmed_deficit = Decimal("0")
            quantity_to_purchase = Decimal("0")
            recommendation = "ТМЦ доступны на складе кейса к полной выдаче."
            issue_allowed = True
        elif available > 0:
            outcome = PickerOutcome.PARTIAL_ISSUE
            confirmed_available = available
            quantity_to_issue = available
            confirmed_deficit = requested - available
            quantity_to_purchase = confirmed_deficit
            recommendation = (
                "Возможна частичная выдача со склада кейса; "
                "остаток — подтверждённый дефицит."
            )
            issue_allowed = True
        else:
            outcome = PickerOutcome.DEFICIT_CONFIRMED
            confirmed_available = Decimal("0")
            quantity_to_issue = Decimal("0")
            confirmed_deficit = requested
            quantity_to_purchase = requested
            if reserved_other > 0:
                recommendation = (
                    f"Свободного остатка нет: {reserved_other} под другим назначением. "
                    "Подтвердить дефицит к закупке."
                )
            else:
                recommendation = (
                    "На складе кейса нет доступного остатка — подтвердить дефицит к закупке."
                )
            issue_allowed = False

        evidence_ids = [item.evidence_id for item in matching if item.evidence_id]
        warehouse_label = case.warehouse_name or target_warehouse or "склад кейса"
        positions.append(
            PickerAssessmentLine(
                line_id=need.line_id,
                nomenclature_id=need.nomenclature_id,
                nomenclature_name=need.nomenclature_name,
                characteristic_id=need.characteristic_id,
                characteristic_name=need.characteristic_name,
                unit=need.unit or "шт",
                requested_quantity=requested,
                warehouse_id=target_warehouse,
                warehouse_name=case.warehouse_name,
                assignment_id=need.assignment_id,
                assignment_name=need.assignment_name,
                store_room_stock=store_room,
                warehouse_stock=warehouse_stock,
                accounting_quantity=accounting,
                factual_quantity=factual,
                available_for_issue=available,
                reserved_other_quantity=reserved_other,
                discrepancy_quantity=discrepancy,
                has_discrepancy=has_discrepancy,
                confirmed_available=confirmed_available,
                confirmed_deficit=confirmed_deficit,
                quantity_to_issue=quantity_to_issue,
                quantity_to_purchase=quantity_to_purchase,
                outcome=outcome,
                recommendation=recommendation,
                issue_allowed=issue_allowed,
                formulas={
                    "warehouse_filter": f"склад кейса: {warehouse_label}",
                    "assignment": (
                        f"назначение позиции: {need.assignment_name or need.assignment_id or 'не указано'}; "
                        f"чужое назначение/резерв исключено: {reserved_other}"
                    ),
                    "available": (
                        f"доступно = остаток склада кейса ({physical}) "
                        f"− резерв/чужое назначение ({soft_reserved}) = {available}"
                    ),
                    "deficit": (
                        f"дефицит = потребность {requested} − доступно {available} "
                        f"= {confirmed_deficit}"
                    ),
                    "discrepancy": (
                        f"расхождение = |учёт {accounting} − факт {factual}| = {discrepancy}"
                    ),
                },
                evidence_ids=evidence_ids,
                excluded_supply=excluded,
            )
        )

    has_discrepancy = any(
        item.has_discrepancy and item.outcome is PickerOutcome.DISCREPANCY_RETURN
        for item in positions
    )
    deficit_count = sum(1 for item in positions if item.confirmed_deficit > 0)
    has_deficit = deficit_count > 0
    has_issue = any(item.quantity_to_issue > 0 for item in positions)
    if has_discrepancy:
        decision_kind = "discrepancy_return"
        summary = "Есть расхождения учёта и факта — требуется возврат до устранения."
        next_step = "Подтвердить возврат кейса из-за расхождений."
    elif has_deficit and has_issue:
        decision_kind = "stock_confirmation"
        summary = (
            f"Обнаружено дефицитных позиций: {deficit_count}. "
            "Согласовать частичную выдачу и передать непокрытый дефицит в ОМТО."
        )
        next_step = "Согласовать частичную выдачу и дефицит к закупке."
    elif has_deficit:
        decision_kind = "deficit_confirmation"
        summary = (
            f"Обнаружено дефицитных позиций: {deficit_count}. "
            "Передать только непокрытый дефицит в контур снабжения."
        )
        next_step = "Подтвердить дефицит для передачи на закупку."
    elif has_issue:
        decision_kind = "stock_confirmation"
        summary = "ТМЦ доступны к выдаче со склада кейса."
        next_step = "Подтвердить выдачу из имеющегося остатка."
    else:
        decision_kind = "none"
        summary = "Заключение по складскому наличию сформировано без дефицита."
        next_step = "Передать результат оркестратору."

    total_available = sum((item.confirmed_available for item in positions), Decimal("0"))
    total_deficit = sum((item.confirmed_deficit for item in positions), Decimal("0"))
    total_issue = sum((item.quantity_to_issue for item in positions), Decimal("0"))
    total_purchase = sum((item.quantity_to_purchase for item in positions), Decimal("0"))
    total_requested = sum((item.requested_quantity for item in positions), Decimal("0"))
    conclusion = {
        "requested_quantity": str(total_requested),
        "available_quantity": str(total_available),
        "confirmed_deficit": str(total_deficit),
        "quantity_to_issue": str(total_issue),
        "quantity_to_purchase": str(total_purchase),
        "positions_count": len(positions),
        "deficit_positions": deficit_count,
        "warehouse_name": case.warehouse_name,
        "warehouse_1c_ref": case.warehouse_1c_ref,
    }
    fingerprint = _stable_hash(
        {
            "case_id": case.case_id,
            "source_version": case.source_data_version,
            "warehouse": case.warehouse_1c_ref,
            "positions": [item.model_dump(mode="json") for item in positions],
            "conclusion": conclusion,
        }
    )
    return WarehousePickerOutput(
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
        conclusion=conclusion,
    )


__all__ = ["calculate_picker_assessment", "validate_case"]
