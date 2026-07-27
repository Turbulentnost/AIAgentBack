"""OTK worker presentation service — CRUD + sample_rule + 1C stub."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.agents.quality_control_agent.rules_registry import build_sample_rule
from app.agents.quality_engineer_agent.otk_schemas import (
    OtkPresentationCardRead,
    OtkPresentationListResponse,
    OtkPresentationSummary,
    OtkPresentationUpdate,
    OtkShipmentLineCreate,
    OtkShipmentLineRead,
    OtkShipmentLineUpdate,
    OtkWorkerRead,
    OtkWriteTo1CResult,
)
from app.agents.quality_engineer_agent.otk_store import (
    OtkPresentationStore,
    get_otk_store,
)


def _lot_qty(line: dict[str, Any]) -> float | None:
    qty_fact = line.get("qty_fact")
    qty_upd = line.get("qty_upd")
    try:
        fact = float(qty_fact) if qty_fact is not None else 0.0
    except (TypeError, ValueError):
        fact = 0.0
    try:
        upd = float(qty_upd) if qty_upd is not None else 0.0
    except (TypeError, ValueError):
        upd = 0.0
    lot = fact if fact > 0 else upd
    return lot if lot > 0 else None


def compute_line_sample_rule(
    line: dict[str, Any],
    *,
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Server-side sample_rule from category (+ rating / lot qty)."""
    category = line.get("category") or "other"
    rule = build_sample_rule(
        str(category),
        lot_qty=_lot_qty(line),
        presentation_ref=(presentation or {}).get("invoice_number")
        or (presentation or {}).get("id"),
        nomenclature_ref=line.get("nomenclature") or line.get("code"),
        supplier_ref=(presentation or {}).get("supplier"),
        supplier_quality_rating=line.get("supplier_quality_rating"),
    )
    return rule.model_dump(mode="json")


def _enrich_line(
    line: dict[str, Any],
    presentation: dict[str, Any] | None = None,
) -> OtkShipmentLineRead:
    sample = compute_line_sample_rule(line, presentation=presentation)
    return OtkShipmentLineRead.model_validate({**line, "sample_rule": sample})


def _enrich_card(card: dict[str, Any]) -> OtkPresentationCardRead:
    lines = [_enrich_line(line, card) for line in (card.get("lines") or [])]
    payload = {**card, "lines": [line.model_dump(mode="json") for line in lines]}
    return OtkPresentationCardRead.model_validate(payload)


def _is_fully_accepted(card: dict[str, Any]) -> bool:
    lines = card.get("lines") or []
    if not lines:
        return False
    return all(bool(line.get("accepted")) for line in lines)


def _to_summary(card: dict[str, Any]) -> OtkPresentationSummary:
    project_code = card.get("project_code")
    project_name = card.get("project_name")
    return OtkPresentationSummary(
        id=str(card["id"]),
        organization=str(card.get("organization") or ""),
        purchase_order=str(card.get("purchase_order") or ""),
        supplier=str(card.get("supplier") or ""),
        invoice_number=str(card.get("invoice_number") or ""),
        due_at=str(card.get("due_at") or ""),
        status=card.get("status") or "queued",  # type: ignore[arg-type]
        lines_count=len(card.get("lines") or []),
        all_accepted=_is_fully_accepted(card),
        executor_id=str(card.get("executor_id") or ""),
        project_code=str(project_code) if project_code else None,
        project_name=str(project_name) if project_name else None,
    )


class OtkPresentationService:
    def __init__(self, store: OtkPresentationStore | None = None) -> None:
        self.store = store or get_otk_store()

    def list_presentations(self) -> OtkPresentationListResponse:
        cards = self.store.list_presentations()
        summaries = [_to_summary(card) for card in cards]
        pending = [
            item
            for item in summaries
            if item.status != "done" and not item.all_accepted
        ]
        earliest: str | None = None
        if pending:
            earliest = min(pending, key=lambda item: item.due_at).due_at
        workers = [OtkWorkerRead.model_validate(w) for w in self.store.list_workers()]
        return OtkPresentationListResponse(
            items=summaries,
            pending_count=len(pending),
            earliest_due_at=earliest,
            workers=workers,
        )

    def get_presentation(self, presentation_id: str) -> OtkPresentationCardRead | None:
        card = self.store.get_presentation(presentation_id)
        if card is None:
            return None
        return _enrich_card(card)

    def create_presentation(self, payload: dict[str, Any]) -> OtkPresentationCardRead:
        from app.agents.quality_engineer_agent.otk_schemas import OtkPresentationCreate

        data = OtkPresentationCreate.model_validate(payload).model_dump(mode="json")
        lines_in = list(data.pop("lines") or [])
        card = {
            **data,
            "id": self.store.new_presentation_id(),
            "lines": [
                {**line, "id": self.store.new_line_id()}
                for line in lines_in
            ],
        }
        if not card.get("invoice_date"):
            from datetime import date

            card["invoice_date"] = date.today().isoformat()
        if not card.get("due_at"):
            from datetime import datetime, timedelta, timezone

            card["due_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        if not card.get("invoice_number"):
            card["invoice_number"] = f"УПД-{card['id'][-6:].upper()}"
        workers = self.store.list_workers()
        if not card.get("executor_id") and workers:
            card["executor_id"] = str(workers[0].get("id") or "")
        saved = self.store.save_presentation(card)
        return _enrich_card(saved)

    def update_presentation(
        self,
        presentation_id: str,
        patch: OtkPresentationUpdate,
    ) -> OtkPresentationCardRead | None:
        card = self.store.get_presentation(presentation_id)
        if card is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        card.update(data)
        saved = self.store.save_presentation(card)
        return _enrich_card(saved)

    def add_line(
        self,
        presentation_id: str,
        payload: OtkShipmentLineCreate,
    ) -> OtkPresentationCardRead | None:
        card = self.store.get_presentation(presentation_id)
        if card is None:
            return None
        line = payload.model_dump(mode="json")
        line["id"] = self.store.new_line_id()
        lines = list(card.get("lines") or [])
        lines.append(line)
        card["lines"] = lines
        saved = self.store.save_presentation(card)
        return _enrich_card(saved)

    def update_line(
        self,
        presentation_id: str,
        line_id: str,
        patch: OtkShipmentLineUpdate,
    ) -> OtkPresentationCardRead | None:
        card = self.store.get_presentation(presentation_id)
        if card is None:
            return None
        lines = list(card.get("lines") or [])
        found = False
        data = patch.model_dump(exclude_unset=True)
        for idx, line in enumerate(lines):
            if line.get("id") != line_id:
                continue
            updated = {**line, **data}
            lines[idx] = updated
            found = True
            break
        if not found:
            return None
        card["lines"] = lines
        saved = self.store.save_presentation(card)
        return _enrich_card(saved)

    def delete_line(
        self,
        presentation_id: str,
        line_id: str,
    ) -> OtkPresentationCardRead | None:
        saved = self.store.delete_line(presentation_id, line_id)
        if saved is None:
            # Distinguish missing presentation vs missing line
            if self.store.get_presentation(presentation_id) is None:
                return None
            return None
        return _enrich_card(saved)

    def write_check_to_1c(self, presentation_id: str) -> OtkWriteTo1CResult | None:
        card = self.store.get_presentation(presentation_id)
        if card is None:
            return None
        return OtkWriteTo1CResult(
            ok=True,
            stub=True,
            message=(
                "Скоро: пайплайн обработки карточки и занесение проверки в 1С (заглушка)."
            ),
            presentation_id=presentation_id,
            extra={
                "invoice_number": card.get("invoice_number"),
                "lines_count": len(card.get("lines") or []),
                "stubbed_at": datetime.now().isoformat(),
            },
        )


def make_service(path: Path | None = None) -> OtkPresentationService:
    if path is None:
        return OtkPresentationService()
    return OtkPresentationService(get_otk_store(path))


__all__ = [
    "OtkPresentationService",
    "compute_line_sample_rule",
    "make_service",
]
