"""Reconcile TMC presentation journal against supplier orders and hand off to OTK."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
    QUALITY_ENGINEER_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType, TaskStatus
from app.models.procurement import ProcurementCase, ProcurementCaseEvent, ProcurementSupplierOrderLink
from app.models.task import Task


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_ref(value: Any) -> str:
    return _text(value).replace("{", "").replace("}", "").lower()


def build_otk_presentations_for_case(
    case: ProcurementCase,
    *,
    journal_by_order_ref: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build OTK UI cards from journal rows + case/supplier-order context."""
    coverage = (
        (case.case_metadata or {}).get("supplier_order_coverage")
        if isinstance((case.case_metadata or {}).get("supplier_order_coverage"), dict)
        else {}
    )
    positions = coverage.get("positions") if isinstance(coverage.get("positions"), list) else []
    nom_names = {
        _norm_ref(item.get("nomenclature_id") or item.get("nomenclature_ref")): _text(
            item.get("nomenclature_name")
        )
        for item in positions
        if isinstance(item, dict)
    }
    for position in case.positions or []:
        key = _norm_ref(position.nomenclature_id)
        if key and not nom_names.get(key):
            nom_names[key] = _text(position.nomenclature_name)

    cards: list[dict[str, Any]] = []
    for link in case.supplier_order_links or []:
        order_ref = _norm_ref(link.supplier_order_1c_ref)
        journal = journal_by_order_ref.get(order_ref)
        if not journal:
            continue
        journal_ref = _norm_ref(journal.get("ref")) or order_ref
        lines_out: list[dict[str, Any]] = []
        raw_lines = journal.get("lines") if isinstance(journal.get("lines"), list) else []
        if not raw_lines and link.lines:
            for index, line in enumerate(link.lines):
                nom_ref = _norm_ref(line.nomenclature_id)
                lines_out.append(
                    {
                        "id": f"{journal_ref}-l{index + 1}",
                        "code": nom_ref,
                        "nomenclature": nom_names.get(nom_ref) or nom_ref,
                        "storage_unit": "шт",
                        "qty_upd": float(line.quantity or 0),
                        "qty_fact": float(line.quantity or 0),
                        "category": "other",
                    }
                )
        else:
            for index, line in enumerate(raw_lines):
                if not isinstance(line, dict):
                    continue
                nom_ref = _norm_ref(line.get("nomenclatureRef") or line.get("nomenclature_ref"))
                qty_upd = line.get("qtyUpd") if line.get("qtyUpd") is not None else line.get("quantity")
                qty_fact = (
                    line.get("qtyFact") if line.get("qtyFact") is not None else line.get("quantity")
                )
                try:
                    qty_upd_f = float(qty_upd or 0)
                except (TypeError, ValueError):
                    qty_upd_f = 0.0
                try:
                    qty_fact_f = float(qty_fact or 0)
                except (TypeError, ValueError):
                    qty_fact_f = 0.0
                lines_out.append(
                    {
                        "id": f"{journal_ref}-l{index + 1}",
                        "code": nom_ref,
                        "nomenclature": nom_names.get(nom_ref) or nom_ref,
                        "storage_unit": "шт",
                        "qty_upd": qty_upd_f,
                        "qty_fact": qty_fact_f,
                        "category": "other",
                    }
                )
        supplier = _text(journal.get("supplierName") or journal.get("supplier_name"))
        cards.append(
            {
                "id": journal_ref or f"{case.id}:{order_ref}",
                "case_id": str(case.id),
                "source_number": case.source_number,
                "source_1c_ref": case.source_1c_ref,
                "organization": "",
                "purchase_order": _text(link.supplier_order_number) or order_ref,
                "supplier": supplier,
                "counterparty": supplier,
                "warehouse": "",
                "invoice_date": _text(journal.get("invoiceDate") or journal.get("invoice_date"))[:10],
                "invoice_number": _text(journal.get("invoiceNumber") or journal.get("invoice_number")),
                "storage_zone": _text(journal.get("storageZone") or journal.get("storage_zone")),
                "presentation_place": _text(
                    journal.get("presentationPlace") or journal.get("presentation_place")
                ),
                "otk_incoming_warehouse": "",
                "executor_id": "",
                "due_at": _text(journal.get("dueAt") or journal.get("due_at") or journal.get("date")),
                "status": "queued",
                "journal_number": _text(journal.get("number")),
                "journal_status": _text(journal.get("status")),
                "journal_stage": _text(journal.get("documentStage") or journal.get("document_stage")),
                "supplier_order_1c_ref": order_ref,
                "lines": lines_out,
            }
        )
    return cards


class TmcPresentationReconciliationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        mcp_client: OneCMCPClient | None = None,
        enqueue_case: bool = True,
    ) -> None:
        self.db = db
        self.mcp = mcp_client or OneCMCPClient(timeout_seconds=650, max_attempts=2)
        self.enqueue_case = enqueue_case

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
                            ProcurementCaseStatus.QUALITY_RELEASED.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        eligible = []
        for case in cases:
            if not case.supplier_order_links:
                continue
            metadata = case.case_metadata or {}
            tmc = (
                metadata.get("tmc_presentation_coverage")
                if isinstance(metadata.get("tmc_presentation_coverage"), dict)
                else {}
            )
            # Already fully handed off to OTK and PM archived — nothing left to reconcile.
            if metadata.get("otk_handed_off_at") and _text(tmc.get("status")) == "full":
                continue
            if not (
                metadata.get("purchase_manager_invoked_at")
                or PURCHASE_MANAGER_AGENT_ID in (case.assigned_agents or [])
                or case.current_agent_id == PURCHASE_MANAGER_AGENT_ID
                or metadata.get("otk_started_at")
            ):
                continue
            eligible.append(case)
        if not eligible:
            return {"status": "success", "cases_seen": 0, "cases_changed": 0, "handed_off": 0}

        by_database: dict[str, list[ProcurementCase]] = {}
        for case in eligible:
            by_database.setdefault(case.source_database or "default", []).append(case)

        changed = 0
        handed_off = 0
        presentations_seen = 0
        errors: list[str] = []
        for database, database_cases in by_database.items():
            order_refs = sorted(
                {
                    _norm_ref(link.supplier_order_1c_ref)
                    for case in database_cases
                    for link in case.supplier_order_links
                    if _norm_ref(link.supplier_order_1c_ref)
                }
            )
            try:
                response = await self.mcp.call_capability(
                    "read_procurement_list_tmc_presentations",
                    {
                        "database": database,
                        "documentLimit": 20000,
                        "lineLimit": 100000,
                        "supplierOrderRefs": order_refs,
                    },
                )
            except (MCPUnavailableError, MCPCallError) as exc:
                errors.append(f"{database}: {exc}")
                continue
            if response.get("status") == "capability_unavailable":
                errors.append(
                    f"{database}: "
                    f"{response.get('reason') or 'tmc presentation capability unavailable'}"
                )
                continue
            raw = response.get("presentations") or response.get("items") or []
            presentations = [item for item in raw if isinstance(item, dict)]
            presentations_seen += len(presentations)
            journal_by_order: dict[str, dict[str, Any]] = {}
            for item in presentations:
                order_ref = _norm_ref(
                    item.get("supplierOrderRef")
                    or item.get("supplier_order_ref")
                    or (
                        (item.get("basis") or {}).get("ref")
                        if isinstance(item.get("basis"), dict)
                        else None
                    )
                )
                if not order_ref:
                    continue
                # Keep the newest journal row per supplier order.
                previous = journal_by_order.get(order_ref)
                if previous is None or _text(item.get("date")) >= _text(previous.get("date")):
                    journal_by_order[order_ref] = item

            for case in database_cases:
                result = await self._apply_case(case, journal_by_order)
                if result["changed"]:
                    changed += 1
                if result["handed_off"]:
                    handed_off += 1

        await self.db.flush()
        return {
            "status": "partial" if errors else "success",
            "cases_seen": len(eligible),
            "cases_changed": changed,
            "handed_off": handed_off,
            "presentations_seen": presentations_seen,
            "errors": errors,
        }

    async def _apply_case(
        self,
        case: ProcurementCase,
        journal_by_order: dict[str, dict[str, Any]],
    ) -> dict[str, bool]:
        active_links = list(case.supplier_order_links or [])
        if not active_links:
            return {"changed": False, "handed_off": False}

        order_rows: list[dict[str, Any]] = []
        covered = 0
        for link in active_links:
            order_ref = _norm_ref(link.supplier_order_1c_ref)
            journal = journal_by_order.get(order_ref)
            found = journal is not None
            if found:
                covered += 1
            order_rows.append(
                {
                    "supplier_order_1c_ref": order_ref,
                    "supplier_order_number": _text(link.supplier_order_number),
                    "found": found,
                    "journal_ref": _norm_ref(journal.get("ref")) if journal else None,
                    "journal_number": _text(journal.get("number")) if journal else None,
                    "journal_date": _text(journal.get("date")) if journal else None,
                    "journal_status": _text(journal.get("status")) if journal else None,
                }
            )

        total = len(active_links)
        if covered == 0:
            coverage_status = "none"
        elif covered == total:
            coverage_status = "full"
        else:
            coverage_status = "partial"

        now = datetime.now(UTC)
        metadata = dict(case.case_metadata or {})
        previous = (
            metadata.get("tmc_presentation_coverage")
            if isinstance(metadata.get("tmc_presentation_coverage"), dict)
            else {}
        )
        previous_status = _text(previous.get("status")) or "none"
        snapshot = {
            "status": coverage_status,
            "covered_orders": covered,
            "orders_count": total,
            "orders": order_rows,
            "checked_at": now.isoformat(),
        }
        metadata["tmc_presentation_coverage"] = snapshot
        changed = previous_status != coverage_status or previous.get("orders") != order_rows

        handed_off = False
        so_coverage = (
            metadata.get("supplier_order_coverage")
            if isinstance(metadata.get("supplier_order_coverage"), dict)
            else {}
        )
        so_status = _text(so_coverage.get("coverage_status"))
        assigned = list(case.assigned_agents or [])

        # Any journal row starts OTK (cards for found orders). PM closes only on full.
        if coverage_status in {"partial", "full"}:
            cards = build_otk_presentations_for_case(
                case, journal_by_order_ref=journal_by_order
            )
            previous_cards = metadata.get("otk_presentations")
            metadata["otk_presentations"] = cards
            metadata["otk_workspace_status"] = metadata.get("otk_workspace_status") or (
                "awaiting_action"
            )
            metadata["quality_stage"] = ProcurementCaseStatus.QUALITY_ASSIGNED.value
            metadata["quality_context"] = {
                "presentation_ref": cards[0]["id"] if cards else None,
                "presentations_count": len(cards),
                "source_number": case.source_number,
                "supplier_order_refs": [
                    row["supplier_order_1c_ref"] for row in order_rows if row.get("found")
                ],
                "tmc_coverage_status": coverage_status,
            }
            if QUALITY_ENGINEER_AGENT_ID not in assigned:
                assigned.append(QUALITY_ENGINEER_AGENT_ID)
            # Пока журнал неполный — менеджер остаётся в assigned (не завершён).
            if coverage_status == "partial" and PURCHASE_MANAGER_AGENT_ID not in assigned:
                assigned.append(PURCHASE_MANAGER_AGENT_ID)
            otk_just_started = not metadata.get("otk_started_at")
            if otk_just_started:
                metadata["otk_started_at"] = now.isoformat()
                changed = True

            if otk_just_started:
                await self._append_event(
                    case,
                    event_type="otk_agent_assigned",
                    idempotency_key=(
                        f"otk-assigned:{case.id}:{snapshot['checked_at']}"
                    )[:255],
                    payload={
                        "presentations_count": len(cards),
                        "coverage_status": coverage_status,
                        "parallel_purchase_manager": coverage_status == "partial",
                        "parallel_picker": so_status == "partial",
                    },
                )
                if self.enqueue_case:
                    from app.services.procurement_orchestrator_service import (
                        ProcurementOrchestratorService,
                    )

                    previous_agent = case.current_agent_id
                    previous_status = case.status
                    case.current_agent_id = QUALITY_ENGINEER_AGENT_ID
                    case.status = ProcurementCaseStatus.QUALITY_ASSIGNED.value
                    orch = ProcurementOrchestratorService(self.db, enqueue_case=True)
                    await orch._enqueue_role_agent(case)
                    # Partial TMC: restore previous current agent — OTK runs in parallel.
                    if coverage_status == "partial":
                        case.current_agent_id = previous_agent
                        case.status = previous_status

            if coverage_status == "full" and not metadata.get("otk_handed_off_at"):
                metadata["otk_handed_off_at"] = now.isoformat()
                metadata["purchase_manager_workspace_status"] = "archived"
                metadata["purchase_manager_workspace_archived_at"] = now.isoformat()
                metadata["purchase_manager_auto_archived_reason"] = (
                    "tmc_presentation_journal_full"
                )
                assigned = [
                    agent for agent in assigned if agent != PURCHASE_MANAGER_AGENT_ID
                ]
                if so_status == "partial":
                    if WAREHOUSE_PICKER_AGENT_ID not in assigned:
                        assigned.insert(0, WAREHOUSE_PICKER_AGENT_ID)
                else:
                    assigned = [
                        agent for agent in assigned if agent != WAREHOUSE_PICKER_AGENT_ID
                    ]
                if case.current_agent_id == PURCHASE_MANAGER_AGENT_ID:
                    await self._cancel_current_task(case)
                case.status = ProcurementCaseStatus.QUALITY_ASSIGNED.value
                case.control_point = "quality"
                case.requested_operation = "otk_incoming_control"
                case.current_agent_id = QUALITY_ENGINEER_AGENT_ID
                await self._append_event(
                    case,
                    event_type="purchase_manager_auto_archived",
                    idempotency_key=(
                        f"pm-archived-tmc:{case.id}:{snapshot['checked_at']}"
                    )[:255],
                    payload={
                        "reason": "tmc_presentation_journal_full",
                        "orders": order_rows,
                    },
                )
                if self.enqueue_case and not otk_just_started:
                    from app.services.procurement_orchestrator_service import (
                        ProcurementOrchestratorService,
                    )

                    orch = ProcurementOrchestratorService(self.db, enqueue_case=True)
                    await orch._enqueue_role_agent(case)
                handed_off = True
                changed = True
            elif coverage_status == "partial":
                # Keep picker on coverage; surface quality stage only when PM is current.
                if case.current_agent_id == PURCHASE_MANAGER_AGENT_ID:
                    case.control_point = "quality"
                if previous_cards != cards:
                    changed = True

            case.assigned_agents = assigned
            case.case_metadata = metadata
        else:
            case.case_metadata = metadata

        if changed:
            await self._append_event(
                case,
                event_type="tmc_presentation_coverage_changed",
                idempotency_key=(
                    f"tmc-coverage:{case.id}:{coverage_status}:{covered}:{total}"
                )[:255],
                payload=snapshot,
            )

        return {"changed": changed, "handed_off": handed_off}

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
            task.error_message = "Передача работнику ОТК по журналу предъявления ТМЦ."

    async def _append_event(
        self,
        case: ProcurementCase,
        *,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> None:
        existing = (
            await self.db.execute(
                select(ProcurementCaseEvent.id).where(
                    ProcurementCaseEvent.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self.db.add(
            ProcurementCaseEvent(
                id=uuid.uuid4(),
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


__all__ = [
    "TmcPresentationReconciliationService",
    "build_otk_presentations_for_case",
]
