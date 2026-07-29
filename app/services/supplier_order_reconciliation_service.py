from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.procurement_agent.mcp_client import (
    MCPCallError,
    MCPUnavailableError,
    OneCMCPClient,
)
from app.agents.procurement_role_agents.config import (
    PURCHASE_MANAGER_AGENT_ID,
    WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)
from app.agents.procurement_role_agents.warehouse_availability import (
    COMPLEX_CHIEF_SPEC,
    PICKER_SPEC,
    WarehouseAvailabilitySpec,
    is_warehouse_availability_case,
)
from app.agents.warehouse_picker_agent.department import is_montage_section_2_department
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType, TaskStatus
from app.models.procurement import (
    ProcurementCase,
    ProcurementCaseEvent,
    ProcurementSupplierOrderLine,
    ProcurementSupplierOrderLink,
)
from app.models.task import Task


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _coverage_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SupplierOrderReconciliationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        mcp_client: OneCMCPClient | None = None,
    ) -> None:
        self.db = db
        self.mcp = mcp_client or OneCMCPClient(timeout_seconds=650, max_attempts=2)

    async def reconcile(self) -> dict[str, Any]:
        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(
                    selectinload(ProcurementCase.positions),
                    selectinload(ProcurementCase.supplier_order_links).selectinload(
                        ProcurementSupplierOrderLink.lines
                    ),
                )
                .where(
                    ProcurementCase.source_type
                    == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
                    ProcurementCase.status.notin_(
                        [
                            ProcurementCaseStatus.CLOSED.value,
                            ProcurementCaseStatus.FAILED.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        cases = [
            case
            for case in cases
            if is_warehouse_availability_case(case, PICKER_SPEC)
            or is_warehouse_availability_case(case, COMPLEX_CHIEF_SPEC)
            or is_montage_section_2_department(case.department_name)
            or bool((case.case_metadata or {}).get("picker_invoked_at"))
            or bool((case.case_metadata or {}).get("complex_invoked_at"))
        ]
        if not cases:
            return {"status": "success", "cases_seen": 0, "cases_changed": 0}

        by_database: dict[str, list[ProcurementCase]] = {}
        for case in cases:
            by_database.setdefault(case.source_database or "default", []).append(case)

        changed = 0
        orders_seen = 0
        errors: list[str] = []
        for database, database_cases in by_database.items():
            try:
                response = await self.mcp.call_capability(
                    "read_procurement_list_supplier_orders",
                    {
                        "database": database,
                        "maxBasisDepth": 8,
                        "documentLimit": 20000,
                        "lineLimit": 100000,
                    },
                )
            except (MCPUnavailableError, MCPCallError) as exc:
                errors.append(f"{database}: {exc}")
                continue
            if response.get("status") == "capability_unavailable":
                errors.append(
                    f"{database}: "
                    f"{response.get('reason') or 'supplier order capability unavailable'}"
                )
                continue
            raw_orders = response.get("orders") or response.get("items") or []
            orders = [item for item in raw_orders if isinstance(item, dict)]
            orders_seen += len(orders)
            for case in database_cases:
                case_orders = [
                    order
                    for order in orders
                    if _text(
                        order.get("root_source_1c_ref")
                        or order.get("rootSourceRef")
                        or order.get("source_ref")
                        or (
                            (order.get("basisResolution") or {}).get("sourceRef")
                            if isinstance(order.get("basisResolution"), dict)
                            else None
                        )
                    ).lower()
                    == case.source_1c_ref.lower()
                    and (
                        not isinstance(order.get("basisResolution"), dict)
                        or (order.get("basisResolution") or {}).get("sourceType")
                        == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
                    )
                ]
                if await self._apply_case(case, case_orders):
                    changed += 1
        await self.db.flush()
        return {
            "status": "partial" if errors else "success",
            "cases_seen": len(cases),
            "cases_changed": changed,
            "orders_seen": orders_seen,
            "errors": errors,
        }

    @staticmethod
    def _two_month_cutoff(now: datetime | None = None) -> datetime:
        current = now or datetime.now(UTC)
        month = current.month - 2
        year = current.year
        if month <= 0:
            month += 12
            year -= 1
        # First day intentionally includes both complete calendar months.
        return current.replace(
            year=year,
            month=month,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    async def _apply_case(
        self,
        case: ProcurementCase,
        orders: list[dict[str, Any]],
    ) -> bool:
        now = datetime.now(UTC)
        order_by_ref = {
            _text(
                order.get("supplier_order_1c_ref")
                or order.get("ref")
                or order.get("Ref_Key")
            ): order
            for order in orders
        }
        order_by_ref = {key: value for key, value in order_by_ref.items() if key}
        existing = {link.supplier_order_1c_ref: link for link in case.supplier_order_links}
        newly_detected_refs = sorted(set(order_by_ref) - set(existing))
        for link in existing.values():
            link.active = False

        normalized_orders: list[dict[str, Any]] = []
        coverage_by_nomenclature: dict[str, list[dict[str, Any]]] = {}
        for order_ref, order in order_by_ref.items():
            link = existing.get(order_ref)
            basis = order.get("basis") if isinstance(order.get("basis"), dict) else {}
            resolution = (
                order.get("basisResolution")
                if isinstance(order.get("basisResolution"), dict)
                else {}
            )
            chain = order.get("chain") or resolution.get("chain")
            order_number = _text(
                order.get("supplier_order_number") or order.get("number") or order.get("Number")
            ) or None
            order_date = _parse_datetime(
                order.get("order_date") or order.get("date") or order.get("Date")
            )
            order_status = _text(order.get("order_status") or order.get("status")) or None
            supplier_name = _text(
                order.get("supplier_name")
                or order.get("supplierName")
                or order.get("partner_name")
                or order.get("partnerName")
            ) or None
            arrival_date = _parse_datetime(
                order.get("arrival_date")
                or order.get("arrivalDate")
                or order.get("desired_arrival_date")
                or order.get("desiredArrivalDate")
            )
            basis_ref = (
                _text(order.get("basis_1c_ref") or order.get("basis_ref") or basis.get("ref"))
                or None
            )
            basis_type = _text(order.get("basis_type") or basis.get("type")) or None
            if link is None:
                link = ProcurementSupplierOrderLink(
                    id=uuid.uuid4(),
                    case_id=case.id,
                    supplier_order_1c_ref=order_ref,
                    supplier_order_number=order_number,
                    order_date=order_date,
                    order_status=order_status,
                    basis_1c_ref=basis_ref,
                    basis_type=basis_type,
                    root_source_1c_ref=case.source_1c_ref,
                    root_source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
                    chain=chain if isinstance(chain, list) else [],
                    first_detected_at=now,
                    last_seen_at=now,
                    active=True,
                )
                link.lines = []
                self.db.add(link)
                case.supplier_order_links.append(link)
            else:
                link.supplier_order_number = order_number
                link.order_date = order_date
                link.order_status = order_status
                link.basis_1c_ref = basis_ref
                link.basis_type = basis_type
                link.root_source_1c_ref = case.source_1c_ref
                link.root_source_type = ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
                link.chain = chain if isinstance(chain, list) else []
                link.last_seen_at = now
                link.active = True

            incoming_lines = order.get("lines") if isinstance(order.get("lines"), list) else []
            existing_lines = {line.line_id: line for line in list(link.lines)}
            seen_line_ids: set[str] = set()
            normalized_lines: list[dict[str, Any]] = []
            for index, raw_line in enumerate(incoming_lines, start=1):
                if not isinstance(raw_line, dict) or bool(
                    raw_line.get("cancelled") or raw_line.get("Отменено")
                ):
                    continue
                nomenclature_id = _text(
                    raw_line.get("nomenclature_id") or raw_line.get("Номенклатура_Key")
                    or raw_line.get("nomenclatureRef")
                )
                if not nomenclature_id:
                    continue
                line_id = _text(
                    raw_line.get("line_id")
                    or raw_line.get("LineNumber")
                    or raw_line.get("line_number")
                    or raw_line.get("lineNumber")
                    or index
                )
                seen_line_ids.add(line_id)
                line = existing_lines.get(line_id)
                if line is None:
                    line = ProcurementSupplierOrderLine(
                        id=uuid.uuid4(),
                        link_id=link.id,
                        line_id=line_id,
                        nomenclature_id=nomenclature_id,
                    )
                    self.db.add(line)
                    link.lines.append(line)
                line.line_number = int(
                    raw_line.get("line_number")
                    or raw_line.get("LineNumber")
                    or raw_line.get("lineNumber")
                    or index
                )
                line.nomenclature_id = nomenclature_id
                line.characteristic_id = _text(
                    raw_line.get("characteristic_id") or raw_line.get("Характеристика_Key")
                    or raw_line.get("characteristicRef")
                ) or None
                line.quantity = _decimal(
                    raw_line.get("quantity")
                    or raw_line.get("Количество")
                    or raw_line.get("КоличествоУпаковок")
                )
                line.cancelled = False
                line.raw_payload = raw_line
                normalized_lines.append(
                    {
                        "line_id": line_id,
                        "nomenclature_id": nomenclature_id,
                        "quantity": str(line.quantity),
                    }
                )
                coverage_by_nomenclature.setdefault(nomenclature_id, []).append(
                    {
                        "supplier_order_1c_ref": order_ref,
                        "supplier_order_number": link.supplier_order_number or order_ref,
                        "order_date": (
                            link.order_date.isoformat() if link.order_date else None
                        ),
                        "order_status": link.order_status,
                        "supplier_name": supplier_name,
                        "arrival_date": (
                            arrival_date.isoformat() if arrival_date else None
                        ),
                        "quantity": str(line.quantity),
                    }
                )
            for line_id, line in existing_lines.items():
                if line_id not in seen_line_ids:
                    line.cancelled = True
            normalized_orders.append(
                {
                    "supplier_order_1c_ref": order_ref,
                    "supplier_order_number": link.supplier_order_number,
                    "order_date": link.order_date.isoformat() if link.order_date else None,
                    "order_status": link.order_status,
                    "supplier_name": supplier_name,
                    "arrival_date": arrival_date.isoformat() if arrival_date else None,
                    "chain": link.chain or [],
                    "lines": normalized_lines,
                }
            )

        normalized_orders.sort(key=lambda item: _text(item.get("supplier_order_1c_ref")))
        for linked_orders in coverage_by_nomenclature.values():
            linked_orders.sort(
                key=lambda item: (
                    _text(item.get("supplier_order_number")),
                    _text(item.get("supplier_order_1c_ref")),
                )
            )
        positions: list[dict[str, Any]] = []
        for position in case.positions:
            if position.cancelled:
                continue
            linked_orders = coverage_by_nomenclature.get(position.nomenclature_id, [])
            positions.append(
                {
                    "line_id": position.line_id,
                    "nomenclature_id": position.nomenclature_id,
                    "nomenclature_name": position.nomenclature_name,
                    "requested_quantity": str(position.quantity),
                    "purchasing": bool(linked_orders),
                    "is_reconciled": bool(linked_orders),
                    "ordered_quantity": (
                        str(position.quantity) if linked_orders else "0"
                    ),
                    "remaining_quantity": (
                        "0" if linked_orders else str(position.quantity)
                    ),
                    "supplier_orders": linked_orders,
                }
            )
        covered = sum(1 for position in positions if position["purchasing"])
        coverage_status = (
            "full"
            if positions and covered == len(positions)
            else "partial"
            if covered
            else "none"
        )
        snapshot = {
            "schema_version": "1.0",
            "coverage_status": coverage_status,
            "covered_positions": covered,
            "positions_count": len(positions),
            "positions": positions,
            "supplier_orders": normalized_orders,
            "checked_at": now.isoformat(),
            "calculated_at": now.isoformat(),
            "summary": (
                "Все позиции присутствуют в связанных заказах поставщику."
                if coverage_status == "full"
                else "Часть позиций уже присутствует в связанных заказах поставщику."
                if coverage_status == "partial"
                else "Связанные заказы поставщику не найдены."
            ),
            "recommended_next_step": (
                "Контролировать исполнение заказов поставщику."
                if coverage_status == "full"
                else "Создать заказы поставщику для непокрытых позиций."
                if coverage_status == "partial"
                else "Продолжить обеспечение заказа материалов."
            ),
            "decision_kind": "none",
        }
        stable_snapshot = {
            key: value
            for key, value in snapshot.items()
            if key not in {"checked_at", "calculated_at"}
        }
        fingerprint = _coverage_hash(stable_snapshot)
        metadata = dict(case.case_metadata or {})
        previous_fingerprint = _text(metadata.get("supplier_order_coverage_fingerprint"))
        previous_status = _text(
            (metadata.get("supplier_order_coverage") or {}).get("coverage_status")
            if isinstance(metadata.get("supplier_order_coverage"), dict)
            else ""
        )
        metadata["supplier_order_coverage"] = snapshot
        metadata["supplier_order_coverage_fingerprint"] = fingerprint

        assigned = list(dict.fromkeys(case.assigned_agents or []))
        if coverage_status in {"partial", "full"}:
            if PURCHASE_MANAGER_AGENT_ID not in assigned:
                assigned.append(PURCHASE_MANAGER_AGENT_ID)
            metadata.setdefault("purchase_manager_invoked_at", now.isoformat())
            metadata["purchase_manager_workspace_status"] = "awaiting_action"
            metadata.pop("purchase_manager_workspace_archived_at", None)
            metadata["purchase_manager_output"] = snapshot
            # Seed rich manager workspace (Jalko) without overriding existing search/PO state.
            manager_workspace = dict(metadata.get("procurement_manager") or {})
            manager_workspace.setdefault("lifecycle_state", "handoff_received")
            manager_workspace.setdefault("handoff_received_at", now.isoformat())
            manager_workspace.setdefault("payment_document_draft", None)
            manager_workspace.setdefault("recommendation_audit", [])
            manager_workspace.setdefault("purchase_order_drafts", [])
            metadata["procurement_manager"] = manager_workspace
        else:
            assigned = [agent for agent in assigned if agent != PURCHASE_MANAGER_AGENT_ID]
            if metadata.get("purchase_manager_invoked_at"):
                metadata["purchase_manager_workspace_status"] = "archived"
                metadata.setdefault("purchase_manager_workspace_archived_at", now.isoformat())

        availability_spec = self._availability_spec_for_case(case)
        agent_output = (
            metadata.get(availability_spec.output_key)
            if availability_spec is not None
            else None
        )
        if (
            availability_spec is not None
            and isinstance(agent_output, dict)
            and coverage_status in {"partial", "full"}
        ):
            updated_output = dict(agent_output)
            output_positions = (
                list(updated_output["positions"])
                if isinstance(updated_output.get("positions"), list)
                else []
            )
            coverage_by_line = {
                _text(item.get("line_id")): item for item in positions if _text(item.get("line_id"))
            }
            coverage_position_by_nomenclature = {
                _text(item.get("nomenclature_id")): item
                for item in positions
                if _text(item.get("nomenclature_id"))
            }
            merged_positions: list[dict[str, Any]] = []
            for raw_position in output_positions:
                if not isinstance(raw_position, dict):
                    continue
                position = dict(raw_position)
                line_id = _text(position.get("line_id"))
                nomenclature_id = _text(position.get("nomenclature_id"))
                covered_position = coverage_by_line.get(line_id) or (
                    coverage_position_by_nomenclature.get(nomenclature_id)
                )
                if covered_position and covered_position.get("purchasing"):
                    order_numbers = [
                        _text(order.get("supplier_order_number"))
                        for order in (covered_position.get("supplier_orders") or [])
                        if isinstance(order, dict) and _text(order.get("supplier_order_number"))
                    ]
                    ordered_qty = sum(
                        (
                            _decimal(order.get("quantity"))
                            for order in (covered_position.get("supplier_orders") or [])
                            if isinstance(order, dict)
                        ),
                        Decimal("0"),
                    )
                    if ordered_qty <= 0:
                        ordered_qty = _decimal(
                            covered_position.get("ordered_quantity")
                            or covered_position.get("requested_quantity")
                            or position.get("requested_quantity")
                        )
                    supplier_names = [
                        _text(order.get("supplier_name"))
                        for order in (covered_position.get("supplier_orders") or [])
                        if isinstance(order, dict) and _text(order.get("supplier_name"))
                    ]
                    arrival_dates = [
                        _text(order.get("arrival_date"))
                        for order in (covered_position.get("supplier_orders") or [])
                        if isinstance(order, dict) and _text(order.get("arrival_date"))
                    ]
                    position["already_being_purchased"] = True
                    position["supplier_order_numbers"] = list(dict.fromkeys(order_numbers))
                    position["ordered_quantity"] = format(ordered_qty.normalize(), "f")
                    position["supplier_name"] = next(iter(dict.fromkeys(supplier_names)), None)
                    position["arrival_date"] = next(iter(dict.fromkeys(arrival_dates)), None)
                    position["supplier_orders"] = covered_position.get("supplier_orders") or []
                    if coverage_status == "full":
                        position["quantity_to_purchase"] = "0"
                        position["confirmed_deficit"] = "0"
                        position["outcome"] = "covered_by_supplier_order"
                        position["recommendation"] = "Ведется закупка по заказу поставщику."
                else:
                    position["already_being_purchased"] = False
                merged_positions.append(position)
            if merged_positions:
                updated_output["positions"] = merged_positions
            if coverage_status == "full":
                updated_output["summary"] = (
                    "Закупка по складскому наличию не требуется: "
                    "все позиции уже в заказах поставщику."
                )
                updated_output["recommended_next_step"] = (
                    "Контролировать исполнение заказов поставщику."
                )
                updated_output["decision_kind"] = "none"
                metadata[availability_spec.key("decision_kind")] = "none"
            metadata[availability_spec.output_key] = updated_output

        if availability_spec is not None and coverage_status == "full":
            # All positions are in supplier orders: close availability agent, keep PM.
            metadata[availability_spec.key("workspace_status")] = "archived"
            metadata[availability_spec.key("workspace_archived_at")] = now.isoformat()
            metadata[availability_spec.key("archived_bucket")] = "success"
            metadata[availability_spec.key("auto_archived_reason")] = (
                "all_positions_in_supplier_orders"
            )
            metadata[availability_spec.key("procurement_status")] = "covered"
            case.status = ProcurementCaseStatus.ORDERED.value
            case.control_point = "purchase"
            case.requested_operation = "monitor_supplier_orders"
            if case.current_agent_id == availability_spec.agent_id:
                await self._cancel_current_task(case)
            case.current_agent_id = PURCHASE_MANAGER_AGENT_ID
            assigned = [
                agent for agent in assigned if agent != availability_spec.agent_id
            ]
            if PURCHASE_MANAGER_AGENT_ID not in assigned:
                assigned.append(PURCHASE_MANAGER_AGENT_ID)
        elif (
            availability_spec is not None
            and previous_status == "full"
            and coverage_status != "full"
        ):
            case.status = ProcurementCaseStatus.AGENT_WAITING.value
            case.control_point = "coverage"
            case.requested_operation = "assess_need"
            case.current_agent_id = availability_spec.agent_id
            if availability_spec.agent_id not in assigned:
                assigned.insert(0, availability_spec.agent_id)
            metadata[availability_spec.key("workspace_status")] = "awaiting_action"
            metadata.pop(availability_spec.key("workspace_archived_at"), None)
            metadata.pop(availability_spec.key("archived_bucket"), None)
            metadata.pop(availability_spec.key("auto_archived_reason"), None)
            metadata.pop(availability_spec.key("procurement_status"), None)
        elif availability_spec is not None and coverage_status == "partial":
            # Partial coverage: availability agent stays open, manager in parallel.
            metadata[availability_spec.key("workspace_status")] = "awaiting_action"
            metadata.pop(availability_spec.key("workspace_archived_at"), None)
            metadata.pop(availability_spec.key("archived_bucket"), None)
            metadata.pop(availability_spec.key("auto_archived_reason"), None)
            metadata[availability_spec.key("procurement_status")] = "partial"
            if availability_spec.agent_id not in assigned:
                assigned.insert(0, availability_spec.agent_id)
            if PURCHASE_MANAGER_AGENT_ID not in assigned:
                assigned.append(PURCHASE_MANAGER_AGENT_ID)

        case.assigned_agents = assigned
        case.case_metadata = metadata
        if previous_fingerprint == fingerprint:
            return False
        if newly_detected_refs:
            await self._append_event(
                case,
                event_type="supplier_order_detected",
                idempotency_key=(
                    f"supplier-order-detected:{case.id}:"
                    f"{_coverage_hash({'refs': newly_detected_refs})}"
                )[:255],
                payload={"supplier_order_refs": newly_detected_refs},
            )
        await self._append_event(
            case,
            event_type="supplier_coverage_changed",
            idempotency_key=f"supplier-coverage:{case.id}:{fingerprint}"[:255],
            payload={
                "previous_coverage_status": previous_status or "none",
                "coverage_status": coverage_status,
                "covered_positions": covered,
                "positions_count": len(positions),
                "supplier_order_refs": sorted(order_by_ref),
            },
        )
        if coverage_status in {"partial", "full"} and previous_status == "none":
            await self._append_event(
                case,
                event_type="purchase_manager_assigned",
                idempotency_key=f"purchase-manager-assigned:{case.id}:{fingerprint}"[:255],
                payload={"coverage_status": coverage_status},
            )
        if (
            availability_spec is not None
            and coverage_status == "full"
            and previous_status != "full"
        ):
            await self._append_event(
                case,
                event_type=availability_spec.auto_archive_event,
                idempotency_key=(
                    f"{availability_spec.prefix}-auto-archived:{case.id}:{fingerprint}"
                )[:255],
                payload={"reason": "all_positions_in_supplier_orders"},
            )
        return True

    @staticmethod
    def _availability_spec_for_case(
        case: ProcurementCase,
    ) -> WarehouseAvailabilitySpec | None:
        metadata = case.case_metadata or {}
        # Explicit picker markers win over "non-MU2" department fallback.
        if (
            case.current_agent_id == WAREHOUSE_PICKER_AGENT_ID
            or metadata.get("picker_invoked_at")
            or is_montage_section_2_department(case.department_name)
        ):
            return PICKER_SPEC
        if (
            case.current_agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_ID
            or metadata.get("complex_invoked_at")
            or (
                case.source_type == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
                and not is_montage_section_2_department(case.department_name)
            )
        ):
            return COMPLEX_CHIEF_SPEC
        return None

    async def _cancel_current_task(self, case: ProcurementCase) -> None:
        if case.current_task_id is None:
            return
        task = await self.db.get(Task, case.current_task_id)
        if task is not None and task.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now(UTC)
            task.error_message = "Все позиции уже присутствуют в заказах поставщику."
        case.current_task_id = None

    async def _append_event(
        self,
        case: ProcurementCase,
        *,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> None:
        exists = await self.db.scalar(
            select(ProcurementCaseEvent.id).where(
                ProcurementCaseEvent.case_id == case.id,
                ProcurementCaseEvent.idempotency_key == idempotency_key,
            )
        )
        if exists is not None:
            return
        self.db.add(
            ProcurementCaseEvent(
                case_id=case.id,
                correlation_id=case.correlation_id,
                event_type=event_type,
                agent_id="procurement_orchestrator",
                actor_role="procurement_orchestrator",
                previous_status=case.status,
                new_status=case.status,
                idempotency_key=idempotency_key,
                payload=payload,
            )
        )


__all__ = ["SupplierOrderReconciliationService"]
