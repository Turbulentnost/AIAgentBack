from __future__ import annotations

from typing import Any, Protocol

from app.agents.procurement_manager_agent.schemas import ApprovalRecord


class ProcurementMutationAdapter(Protocol):
    async def execute(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class MutationGate:
    """Enforces human approval and the absolute payment-execution prohibition."""

    _ALLOWED = {
        "select_supplier",
        "approve_price",
        "send_rfq",
        "create_supplier_order",
        "update_supplier_order",
        "record_shipment",
    }

    @classmethod
    def authorize(
        cls,
        operation: str,
        approval_id: str | None,
        approvals: list[ApprovalRecord],
    ) -> ApprovalRecord:
        if "payment" in operation.casefold() or "оплат" in operation.casefold():
            raise PermissionError("Payment execution is forbidden for this agent")
        if operation not in cls._ALLOWED:
            raise PermissionError(f"Mutation {operation!r} is not allowed")
        if not approval_id:
            raise PermissionError("approval_id is required for every mutation")
        approval = next(
            (
                item
                for item in approvals
                if item.approval_id == approval_id and item.operation == operation
            ),
            None,
        )
        if approval is None or approval.status != "approved":
            raise PermissionError("A matching approved approval_id is required")
        return approval


class SafeMutationExecutor:
    def __init__(self, adapter: ProcurementMutationAdapter) -> None:
        self.adapter = adapter

    async def execute(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        approval_id: str | None,
        approvals: list[ApprovalRecord],
    ) -> dict[str, Any]:
        MutationGate.authorize(operation, approval_id, approvals)
        safe_payload = {**payload, "approval_id": approval_id}
        return await self.adapter.execute(operation, safe_payload)


__all__ = ["MutationGate", "ProcurementMutationAdapter", "SafeMutationExecutor"]
