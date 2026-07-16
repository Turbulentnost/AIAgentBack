from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from app.agents.procurement_agent.schemas import ProcurementNormalizedMCPRecord


class ProcurementNormalizationResult(BaseModel):
    records: list[ProcurementNormalizedMCPRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pagination_complete: bool = False


def normalize_inventory_response(
    raw: Any,
    *,
    correlation_id: str,
    retrieved_at: datetime,
    requested_nomenclature_ids: list[str],
    requested_warehouse_ids: list[str] | None = None,
    requested_organization_id: str | None = None,
    limit: int = 1000,
    freshness_ttl: timedelta = timedelta(minutes=15),
) -> ProcurementNormalizationResult:
    if not isinstance(raw, dict):
        return ProcurementNormalizationResult(errors=["invalid_response_type"])
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        return ProcurementNormalizationResult(errors=["missing_items"])

    effective_at = _parse_datetime(raw.get("asOf")) or retrieved_at
    is_stale = datetime.now(UTC) - _as_utc(effective_at) > freshness_ttl
    requested_ids = set(requested_nomenclature_ids)
    requested_warehouses = set(requested_warehouse_ids or [])
    response_organization = _optional_string(
        raw.get("organizationRef") or raw.get("organization")
    )
    records: list[ProcurementNormalizedMCPRecord] = []
    errors: list[str] = []
    warnings: list[str] = []

    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            errors.append(f"item_{index}:invalid_type")
            continue
        nomenclature_id = _optional_string(item.get("ref"))
        nomenclature_name = _optional_string(item.get("item"))
        if not nomenclature_id or not nomenclature_name or "quantity" not in item:
            errors.append(f"item_{index}:missing_required_field")
            continue
        if requested_ids and nomenclature_id not in requested_ids:
            continue
        try:
            quantity = Decimal(str(item["quantity"]))
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"item_{index}:invalid_quantity")
            continue

        characteristic_id = _optional_string(
            item.get("characteristicRef") or item.get("characteristic_id")
        )
        warehouse_id = _optional_string(item.get("warehouseRef") or item.get("warehouse_id"))
        organization_id = _optional_string(
            item.get("organizationRef")
            or item.get("organization_id")
            or response_organization
        )
        unit = _optional_string(item.get("unit") or item.get("unitName"))
        reasons: list[str] = []
        if quantity < 0:
            reasons.append("invalid_negative_quantity")
        if unit is None:
            reasons.append("unit_unavailable")
        if warehouse_id is None:
            reasons.append("warehouse_unavailable")
        elif requested_warehouses and warehouse_id not in requested_warehouses:
            reasons.append("warehouse_mismatch")
        if requested_organization_id:
            if organization_id is None:
                reasons.append("organization_unavailable")
            elif organization_id != requested_organization_id:
                reasons.append("organization_mismatch")
        if is_stale:
            reasons.append("stale_evidence")

        # The MCP inventory tool exposes an accounting balance only. It does
        # not expose reservations, quarantine, quality control or free stock.
        reasons.extend(
            [
                "reservations_not_applied",
                "quality_status_unavailable",
                "free_stock_not_confirmed",
            ]
        )
        records.append(
            ProcurementNormalizedMCPRecord(
                source_tool="read_analytics_get_inventory",
                source_object_type="AccountingRegister_Хозрасчетный",
                source_object_id=(
                    f"{nomenclature_id}:{characteristic_id}"
                    if characteristic_id
                    else nomenclature_id
                ),
                nomenclature_id=nomenclature_id,
                nomenclature_name=nomenclature_name,
                characteristic_id=characteristic_id,
                warehouse_id=warehouse_id,
                organization_id=organization_id,
                quantity=quantity,
                unit=unit,
                status="accounting_balance",
                effective_at=effective_at,
                retrieved_at=retrieved_at,
                confirmation_status="unconfirmed_for_coverage",
                eligibility_status="data_insufficient",
                exclusion_reason=";".join(dict.fromkeys(reasons)),
                correlation_id=correlation_id,
            )
        )

    if not raw_items:
        warnings.append("empty_inventory_response")
    if len(raw_items) >= limit:
        warnings.append("pagination_or_truncation_unknown")
    characteristic_counts: dict[str, set[str | None]] = {}
    for record in records:
        characteristic_counts.setdefault(record.nomenclature_id, set()).add(
            record.characteristic_id
        )
    if any(len(values) > 1 for values in characteristic_counts.values()):
        warnings.append("multiple_characteristics")
    return ProcurementNormalizationResult(
        records=records,
        errors=errors,
        warnings=warnings,
        pagination_complete=len(raw_items) < limit,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) == 10:
        normalized = f"{normalized}T23:59:59+00:00"
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _optional_string(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None


__all__ = ["ProcurementNormalizationResult", "normalize_inventory_response"]
