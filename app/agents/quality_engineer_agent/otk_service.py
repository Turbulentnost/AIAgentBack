"""OTK worker presentation service — case-backed list + sample_rule + 1C stub."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models.procurement import ProcurementCase


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


def _card_payload(card: dict[str, Any]) -> dict[str, Any]:
    """Strip non-schema keys before pydantic validation."""
    allowed = {
        "id",
        "organization",
        "purchase_order",
        "supplier",
        "counterparty",
        "warehouse",
        "invoice_date",
        "invoice_number",
        "storage_zone",
        "presentation_place",
        "otk_incoming_warehouse",
        "executor_id",
        "due_at",
        "status",
        "lines",
    }
    return {key: card.get(key) for key in allowed if key in card or key == "lines"}


def _enrich_card(card: dict[str, Any]) -> OtkPresentationCardRead:
    lines = [_enrich_line(line, card) for line in (card.get("lines") or [])]
    payload = {
        **_card_payload(card),
        "lines": [line.model_dump(mode="json") for line in lines],
    }
    return OtkPresentationCardRead.model_validate(payload)


def _to_summary(card: dict[str, Any]) -> OtkPresentationSummary:
    return OtkPresentationSummary(
        id=str(card["id"]),
        organization=str(card.get("organization") or ""),
        purchase_order=str(card.get("purchase_order") or ""),
        supplier=str(card.get("supplier") or ""),
        invoice_number=str(card.get("invoice_number") or ""),
        due_at=str(card.get("due_at") or ""),
        status=card.get("status") or "queued",  # type: ignore[arg-type]
        lines_count=len(card.get("lines") or []),
        executor_id=str(card.get("executor_id") or ""),
    )


def _use_mock_store() -> bool:
    return os.getenv("OTK_USE_MOCK_STORE", "").strip().lower() in {"1", "true", "yes"}


def _case_eligible_for_otk_list(case: ProcurementCase) -> bool:
    metadata = case.case_metadata or {}
    if not metadata.get("purchase_manager_invoked_at") and not metadata.get(
        "purchase_manager_output"
    ):
        return False
    coverage = metadata.get("tmc_presentation_coverage")
    if not isinstance(coverage, dict):
        return False
    # Show as soon as there is at least one TMC journal row for the case's SO.
    if str(coverage.get("status") or "") not in {"partial", "full"}:
        return False
    presentations = metadata.get("otk_presentations")
    return isinstance(presentations, list) and bool(presentations)


class OtkPresentationService:
    def __init__(
        self,
        db: AsyncSession | None = None,
        store: OtkPresentationStore | None = None,
    ) -> None:
        self.db = db
        self.store = store or get_otk_store()

    async def list_presentations(self) -> OtkPresentationListResponse:
        if self.db is None or _use_mock_store():
            return self._list_from_store()
        cases = (
            await self.db.execute(select(ProcurementCase).order_by(ProcurementCase.updated_at.desc()))
        ).scalars().all()
        cards: list[dict[str, Any]] = []
        for case in cases:
            if not _case_eligible_for_otk_list(case):
                continue
            presentations = (case.case_metadata or {}).get("otk_presentations") or []
            for item in presentations:
                if isinstance(item, dict) and item.get("id"):
                    cards.append(item)
        summaries = [_to_summary(card) for card in cards]
        pending = [item for item in summaries if item.status != "done"]
        earliest: str | None = None
        if pending:
            earliest = min(pending, key=lambda item: item.due_at or "").due_at or None
        workers = [OtkWorkerRead.model_validate(w) for w in self.store.list_workers()]
        return OtkPresentationListResponse(
            items=summaries,
            pending_count=len(pending),
            earliest_due_at=earliest,
            workers=workers,
        )

    def _list_from_store(self) -> OtkPresentationListResponse:
        cards = self.store.list_presentations()
        summaries = [_to_summary(card) for card in cards]
        pending = [item for item in summaries if item.status != "done"]
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

    async def get_presentation(self, presentation_id: str) -> OtkPresentationCardRead | None:
        card = await self._load_card(presentation_id)
        if card is None:
            return None
        return _enrich_card(card)

    async def update_presentation(
        self,
        presentation_id: str,
        patch: OtkPresentationUpdate,
    ) -> OtkPresentationCardRead | None:
        if self.db is None or _use_mock_store():
            card = self.store.get_presentation(presentation_id)
            if card is None:
                return None
            card.update(patch.model_dump(exclude_unset=True))
            return _enrich_card(self.store.save_presentation(card))
        loaded = await self._load_mutable(presentation_id)
        if loaded is None:
            return None
        case, cards, index = loaded
        data = patch.model_dump(exclude_unset=True)
        cards[index] = {**cards[index], **data}
        await self._save_cards(case, cards)
        return _enrich_card(cards[index])

    async def add_line(
        self,
        presentation_id: str,
        payload: OtkShipmentLineCreate,
    ) -> OtkPresentationCardRead | None:
        if self.db is None or _use_mock_store():
            card = self.store.get_presentation(presentation_id)
            if card is None:
                return None
            line = payload.model_dump(mode="json")
            line["id"] = self.store.new_line_id()
            lines = list(card.get("lines") or [])
            lines.append(line)
            card["lines"] = lines
            return _enrich_card(self.store.save_presentation(card))
        loaded = await self._load_mutable(presentation_id)
        if loaded is None:
            return None
        case, cards, index = loaded
        line = payload.model_dump(mode="json")
        line["id"] = f"l-{uuid.uuid4()}"
        lines = list(cards[index].get("lines") or [])
        lines.append(line)
        cards[index]["lines"] = lines
        await self._save_cards(case, cards)
        return _enrich_card(cards[index])

    async def update_line(
        self,
        presentation_id: str,
        line_id: str,
        patch: OtkShipmentLineUpdate,
    ) -> OtkPresentationCardRead | None:
        if self.db is None or _use_mock_store():
            card = self.store.get_presentation(presentation_id)
            if card is None:
                return None
            lines = list(card.get("lines") or [])
            data = patch.model_dump(exclude_unset=True)
            found = False
            for idx, line in enumerate(lines):
                if line.get("id") != line_id:
                    continue
                lines[idx] = {**line, **data}
                found = True
                break
            if not found:
                return None
            card["lines"] = lines
            return _enrich_card(self.store.save_presentation(card))
        loaded = await self._load_mutable(presentation_id)
        if loaded is None:
            return None
        case, cards, index = loaded
        lines = list(cards[index].get("lines") or [])
        found = False
        data = patch.model_dump(exclude_unset=True)
        for idx, line in enumerate(lines):
            if line.get("id") != line_id:
                continue
            lines[idx] = {**line, **data}
            found = True
            break
        if not found:
            return None
        cards[index]["lines"] = lines
        await self._save_cards(case, cards)
        return _enrich_card(cards[index])

    async def delete_line(
        self,
        presentation_id: str,
        line_id: str,
    ) -> OtkPresentationCardRead | None:
        if self.db is None or _use_mock_store():
            saved = self.store.delete_line(presentation_id, line_id)
            if saved is None:
                return None
            return _enrich_card(saved)
        loaded = await self._load_mutable(presentation_id)
        if loaded is None:
            return None
        case, cards, index = loaded
        lines = [line for line in (cards[index].get("lines") or []) if line.get("id") != line_id]
        if len(lines) == len(cards[index].get("lines") or []):
            return None
        cards[index]["lines"] = lines
        await self._save_cards(case, cards)
        return _enrich_card(cards[index])

    async def write_check_to_1c(self, presentation_id: str) -> OtkWriteTo1CResult | None:
        card = await self._load_card(presentation_id)
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

    async def _load_card(self, presentation_id: str) -> dict[str, Any] | None:
        if self.db is None or _use_mock_store():
            return self.store.get_presentation(presentation_id)
        cases = (await self.db.execute(select(ProcurementCase))).scalars().all()
        for case in cases:
            if not _case_eligible_for_otk_list(case):
                continue
            for item in (case.case_metadata or {}).get("otk_presentations") or []:
                if isinstance(item, dict) and str(item.get("id")) == presentation_id:
                    return item
        return None

    async def _load_mutable(
        self, presentation_id: str
    ) -> tuple[ProcurementCase, list[dict[str, Any]], int] | None:
        if self.db is None:
            return None
        cases = (await self.db.execute(select(ProcurementCase))).scalars().all()
        for case in cases:
            metadata = dict(case.case_metadata or {})
            presentations = metadata.get("otk_presentations")
            if not isinstance(presentations, list):
                continue
            for index, item in enumerate(presentations):
                if isinstance(item, dict) and str(item.get("id")) == presentation_id:
                    cards = [
                        dict(row) if isinstance(row, dict) else row for row in presentations
                    ]
                    return case, cards, index  # type: ignore[return-value]
        return None

    async def _save_cards(self, case: ProcurementCase, cards: list[dict[str, Any]]) -> None:
        metadata = dict(case.case_metadata or {})
        metadata["otk_presentations"] = cards
        case.case_metadata = metadata
        await self.db.flush()


def make_service(path: Path | None = None) -> OtkPresentationService:
    if path is None:
        return OtkPresentationService()
    return OtkPresentationService(store=get_otk_store(path))


__all__ = [
    "OtkPresentationService",
    "compute_line_sample_rule",
    "make_service",
]
