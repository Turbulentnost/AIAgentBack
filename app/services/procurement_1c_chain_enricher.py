"""Enrich procurement cases from 1C subordination chain (ЗП → ЗРДС → ВК → приход)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.procurement_agent.chain_entities import (
    CHAIN_STAGE_ORDER,
    ENRICH_ENTITY_SETS,
    ChainStage,
)
from app.agents.procurement_agent.mcp_client import MCPCallError, MCPUnavailableError, OneCMCPClient
from app.agents.procurement_manager_agent.fulfillment import FULFILLMENT_LABELS
from app.core.logging import get_logger
from app.models.enums import ProcurementCaseStatus
from app.models.procurement import ProcurementCase

logger = get_logger(__name__)

METADATA_CHAIN_KEY = "procurement_1c_chain"

_PAID_MARKERS = frozenset(
    {
        "оплачен",
        "оплачена",
        "оплачено",
        "paid",
        "исполнен",
        "исполнена",
        "проведен",
        "проведена",
    }
)
_REJECTED_MARKERS = frozenset({"отклонен", "отклонена", "rejected", "отменен", "отменена"})
_OTK_OK_MARKERS = frozenset(
    {"годен", "ок", "ok", "passed", "соответствует", "без замечаний", "принят", "принята"}
)
_EMPTY_GUIDS = frozenset(
    {
        "",
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-0000-0000-000000000000",
    }
)


def _norm_status(value: Any) -> str:
    return str(value or "").strip().casefold()


def _ref_of(doc: dict[str, Any]) -> str | None:
    for key in ("Ref_Key", "ref_key", "id", "Id"):
        raw = doc.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text not in _EMPTY_GUIDS:
            return text
    return None


def _counterparty_filled(doc: dict[str, Any]) -> bool:
    for key in ("Контрагент_Key", "Партнер_Key", "counterparty_key", "partner_key"):
        raw = doc.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text not in _EMPTY_GUIDS:
            return True
    return bool(doc.get("is_definite") or doc.get("definite"))


def _status_matches(status: Any, markers: frozenset[str]) -> bool:
    text = _norm_status(status)
    if not text:
        return False
    return any(marker in text for marker in markers)


def is_definite_purchase_order(doc: dict[str, Any] | None) -> bool:
    if not isinstance(doc, dict):
        return False
    if doc.get("is_definite") is False:
        return False
    return _counterparty_filled(doc)


def is_cash_request_paid(doc: dict[str, Any] | None) -> bool:
    if not isinstance(doc, dict):
        return False
    if doc.get("paid") is True or doc.get("is_paid") is True:
        return True
    return _status_matches(doc.get("Статус") or doc.get("status"), _PAID_MARKERS)


def is_otk_ok(doc: dict[str, Any] | None) -> bool:
    if not isinstance(doc, dict):
        return False
    result = doc.get("РезультатКонтроля") or doc.get("result") or doc.get("status")
    return _status_matches(result, _OTK_OK_MARKERS)


def map_chain_to_statuses(chain: dict[str, Any]) -> dict[str, str]:
    """Map 1C chain snapshot → fulfillment_status + ProcurementCase.status."""
    po = _first(chain.get("purchase_orders"))
    cash = _first(chain.get("cash_requests"))
    otk = _first(chain.get("otk_presentations"))
    receipt = _first(chain.get("purchase_receipts")) or _first(chain.get("goods_receipt_orders"))

    has_receipt = bool(receipt)
    has_otk = bool(otk)
    otk_ok = is_otk_ok(otk) if has_otk else False
    paid = is_cash_request_paid(cash) if cash else False
    has_cash = bool(cash) and not _status_matches(
        (cash or {}).get("Статус") or (cash or {}).get("status"), _REJECTED_MARKERS
    )
    definite_po = is_definite_purchase_order(po)

    if has_receipt:
        fulfillment, case_status = "completed", ProcurementCaseStatus.POSTED.value
    elif has_otk and otk_ok:
        fulfillment, case_status = "posting", ProcurementCaseStatus.POSTING_REQUIRED.value
    elif has_otk:
        fulfillment, case_status = "otk_presentation", ProcurementCaseStatus.RECEIVING.value
    elif paid:
        fulfillment, case_status = "delivery", ProcurementCaseStatus.IN_TRANSIT.value
    elif has_cash:
        fulfillment, case_status = "payment", ProcurementCaseStatus.PAYMENT_PENDING.value
    elif definite_po:
        # Defined PO without treasury request yet — still purchase draft.
        fulfillment, case_status = "no_supplier", ProcurementCaseStatus.PURCHASE_DRAFT.value
    else:
        fulfillment, case_status = "no_supplier", ProcurementCaseStatus.PURCHASE_DRAFT.value

    if fulfillment not in FULFILLMENT_LABELS:
        fulfillment = "no_supplier"
    return {
        "fulfillment_status": fulfillment,
        "case_status": case_status,
    }


def _first(items: Any) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def build_chain_payload(
    *,
    source_ref: str,
    documents_by_stage: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    chain: dict[str, Any] = {
        "source_1c_ref": source_ref,
        "enriched_at": datetime.now(UTC).isoformat(),
        "purchase_orders": list(documents_by_stage.get("purchase_order") or []),
        "cash_requests": list(documents_by_stage.get("cash_request") or []),
        "otk_presentations": list(documents_by_stage.get("otk_presentation") or []),
        "purchase_receipts": list(documents_by_stage.get("purchase_receipt") or []),
        "goods_receipt_orders": list(documents_by_stage.get("goods_receipt_order") or []),
    }
    statuses = map_chain_to_statuses(chain)
    chain["mapped_fulfillment_status"] = statuses["fulfillment_status"]
    chain["mapped_case_status"] = statuses["case_status"]
    return chain


class Procurement1CChainEnricher:
    """Read subordinate 1C docs for active cases and update metadata + statuses."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        mcp: OneCMCPClient | None = None,
    ) -> None:
        self.db = db
        self.mcp = mcp or OneCMCPClient()

    async def enrich_active_cases(
        self,
        *,
        force: bool = False,
        limit: int = 200,
        stub_chains: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]:
        result = await self.db.execute(
            select(ProcurementCase)
            .where(
                ProcurementCase.status.notin_(
                    (ProcurementCaseStatus.CLOSED, ProcurementCaseStatus.POSTED)
                )
            )
            .order_by(ProcurementCase.updated_at.desc())
            .limit(limit)
        )
        cases = list(result.scalars().all())
        summary: dict[str, Any] = {
            "enriched": 0,
            "skipped": 0,
            "errors": [],
            "force": bool(force),
        }
        for case in cases:
            try:
                stub = None
                if stub_chains is not None:
                    stub = stub_chains.get(str(case.source_1c_ref) or str(case.id))
                changed = await self.enrich_case(case, force=force, stub_docs=stub)
                if changed:
                    summary["enriched"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "procurement.chain_enrich.case_failed",
                    case_id=str(case.id),
                )
                summary["errors"].append(f"{case.id}:{exc}")
        await self.db.flush()
        return summary

    async def enrich_case_by_id(
        self,
        case_id: UUID,
        *,
        force: bool = True,
        stub_docs: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        case = await self.db.get(ProcurementCase, case_id)
        if case is None:
            return {"status": "not_found", "case_id": str(case_id)}
        changed = await self.enrich_case(case, force=force, stub_docs=stub_docs)
        await self.db.flush()
        meta = dict(case.case_metadata or {})
        chain = meta.get(METADATA_CHAIN_KEY) or {}
        return {
            "status": "enriched" if changed else "unchanged",
            "case_id": str(case.id),
            "fulfillment_status": chain.get("mapped_fulfillment_status"),
            "case_status": case.status.value if hasattr(case.status, "value") else str(case.status),
            "chain": chain,
        }

    async def enrich_case(
        self,
        case: ProcurementCase,
        *,
        force: bool = False,
        stub_docs: dict[str, list[dict[str, Any]]] | None = None,
    ) -> bool:
        source_ref = str(case.source_1c_ref or "").strip()
        if not source_ref:
            return False

        meta = dict(case.case_metadata or {})
        workspace = dict(meta.get("workspace") or {})
        if workspace.get("fulfillment_status_manual") and not force:
            return False

        if stub_docs is not None:
            documents_by_stage = {
                stage: list(stub_docs.get(stage) or []) for stage in CHAIN_STAGE_ORDER
            }
        else:
            documents_by_stage = await self._fetch_chain_for_ref(
                source_ref,
                database=str(getattr(case, "source_database", None) or "default"),
            )

        chain = build_chain_payload(source_ref=source_ref, documents_by_stage=documents_by_stage)
        statuses = map_chain_to_statuses(chain)

        previous = meta.get(METADATA_CHAIN_KEY)
        meta[METADATA_CHAIN_KEY] = chain
        if not workspace.get("fulfillment_status_manual"):
            workspace["fulfillment_status"] = statuses["fulfillment_status"]
            workspace["fulfillment_status_manual"] = False
        meta["workspace"] = workspace
        case.case_metadata = meta

        try:
            case.status = ProcurementCaseStatus(statuses["case_status"])
        except ValueError:
            pass

        return previous != chain

    async def _fetch_chain_for_ref(
        self,
        source_ref: str,
        *,
        database: str,
    ) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {stage: [] for stage in CHAIN_STAGE_ORDER}
        basis_refs = {source_ref}
        for stage in CHAIN_STAGE_ORDER:
            docs = await self._search_stage_docs(
                stage=stage,  # type: ignore[arg-type]
                basis_refs=basis_refs,
                database=database,
            )
            found[stage] = docs
            for doc in docs:
                ref = _ref_of(doc)
                if ref:
                    basis_refs.add(ref)
        return found

    async def _search_stage_docs(
        self,
        *,
        stage: ChainStage,
        basis_refs: set[str],
        database: str,
    ) -> list[dict[str, Any]]:
        spec = ENRICH_ENTITY_SETS[stage]
        collected: list[dict[str, Any]] = []
        for basis in list(basis_refs):
            try:
                payload = await self.mcp.call_capability(
                    spec["mcp_search_capability"],
                    {
                        "database": database,
                        "entitySet": spec["entity_set"],
                        "filter": f"ДокументОснование eq guid'{basis}'",
                        "select": ",".join(spec["header_fields"]),
                        "top": 50,
                    },
                )
            except (MCPUnavailableError, MCPCallError) as exc:
                logger.info(
                    "procurement.chain_enrich.mcp_unavailable",
                    stage=stage,
                    error=str(exc),
                )
                continue
            rows = payload.get("documents") or payload.get("value") or payload.get("items") or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    collected.append(row)
        # Deduplicate by Ref_Key
        by_ref: dict[str, dict[str, Any]] = {}
        for doc in collected:
            ref = _ref_of(doc) or str(id(doc))
            by_ref[ref] = doc
        return list(by_ref.values())


__all__ = [
    "METADATA_CHAIN_KEY",
    "Procurement1CChainEnricher",
    "build_chain_payload",
    "is_cash_request_paid",
    "is_definite_purchase_order",
    "is_otk_ok",
    "map_chain_to_statuses",
]
