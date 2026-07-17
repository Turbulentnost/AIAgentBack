from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.procurement_agent import config as procurement_config
from app.agents.procurement_agent.mcp_client import MCPCallError, MCPUnavailableError, OneCMCPClient
from app.agents.procurement_agent.source_discovery import (
    NormalizedSourceDocument,
    get_source_capability,
    list_source_capabilities,
    normalize_source_document,
    positions_to_agent_source_data,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType, TaskStatus
from app.models.procurement import (
    ProcurementCase,
    ProcurementCaseEvent,
    ProcurementCasePosition,
    ProcurementSourceSyncState,
)
from app.models.task import Task

logger = get_logger(__name__)

ACTIVE_CASE_STATUSES = frozenset(
    {
        ProcurementCaseStatus.NEW.value,
        ProcurementCaseStatus.DATA_CHECK.value,
        ProcurementCaseStatus.COVERAGE_CHECK.value,
        ProcurementCaseStatus.HUMAN_REQUIRED.value,
        ProcurementCaseStatus.BLOCKED.value,
    }
)
PROCUREMENT_DOCUMENT_FIELDS = [
    "DataVersion",
    "Number",
    "Date",
    "Posted",
    "DeletionMark",
    "Отменен",
    "Отменён",
    "Отменено",
    "Статус",
    "Автор_Key",
    "Ответственный_Key",
    "Подразделение_Key",
    "Склад_Key",
    "ЦеховаяКладовая_Key",
    "СкладОтправитель_Key",
    "СкладПолучатель_Key",
    "Организация_Key",
    "Приоритет_Key",
    "ЖелаемаяДатаПоступления",
    "ДатаОтгрузки",
    "ДатаУтверждения",
    "Товары",
]
TERMINAL_CASE_STATUSES = frozenset(
    {
        ProcurementCaseStatus.CLOSED.value,
        ProcurementCaseStatus.FAILED.value,
    }
)
ZERO_1C_REF = "00000000-0000-0000-0000-000000000000"
CLOSED_REASON_LABELS = {
    "cancelled": "Документ отменён в 1С",
    "deletion_mark": "Документ помечен на удаление",
    "no_active_positions": "Нет активных строк потребности",
    "inactive_supply_action": "Действие строк больше не «К обеспечению»",
    "terminal_status": "Документ закрыт или завершён в 1С",
    "inactive_supply_action_or_cancelled": "Основание больше не актуально в 1С",
}
DEFAULT_ROUTE_STAGES = [
    {"stage_id": "basis", "label": "Основание", "order": 1},
    {"stage_id": "data", "label": "Данные", "order": 2},
    {"stage_id": "coverage", "label": "Обеспечение", "order": 3},
    {"stage_id": "purchase", "label": "Закупка", "order": 4},
    {"stage_id": "payment", "label": "Оплата", "order": 5},
    {"stage_id": "delivery", "label": "Поставка", "order": 6},
    {"stage_id": "receipt", "label": "Оприходование", "order": 7},
]


class ProcurementOrchestratorService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        mcp_client: OneCMCPClient | None = None,
        enqueue_case: bool = True,
    ) -> None:
        self.db = db
        self.mcp = mcp_client or OneCMCPClient(
            timeout_seconds=650,
            max_attempts=2,
        )
        self.enqueue_case = enqueue_case
        self.pending_dispatches: list[tuple[str, str]] = []

    async def poll_once(self) -> dict[str, Any]:
        started = datetime.now(UTC)
        summary: dict[str, Any] = {
            "started_at": started.isoformat(),
            "databases": [],
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "enqueued": 0,
            "errors": [],
            "sources": [],
        }
        try:
            health = await self.mcp.call_capability("read_system_health_check", {})
        except (MCPUnavailableError, MCPCallError) as exc:
            summary["errors"].append(f"health_check:{exc}")
            summary["finished_at"] = datetime.now(UTC).isoformat()
            return summary

        databases = await self._resolve_databases(health)
        summary["databases"] = databases
        for database in databases:
            for capability in list_source_capabilities():
                source_summary = await self._poll_source(
                    database=database,
                    source_type=capability.source_type,
                )
                summary["sources"].append(source_summary)
                summary["created"] += source_summary.get("created", 0)
                summary["updated"] += source_summary.get("updated", 0)
                summary["skipped"] += source_summary.get("skipped", 0)
                summary["enqueued"] += source_summary.get("enqueued", 0)
                if source_summary.get("error"):
                    summary["errors"].append(
                        f"{capability.source_type.value}:{source_summary['error']}"
                    )
        summary["finished_at"] = datetime.now(UTC).isoformat()
        return summary

    async def _resolve_databases(self, health: dict[str, Any]) -> list[str]:
        default_name = str(health.get("database") or "default")
        try:
            listed = await self.mcp.call_capability("read_system_list_databases", {})
        except (MCPUnavailableError, MCPCallError):
            return [default_name]
        names = [
            str(item.get("name"))
            for item in listed.get("databases") or []
            if isinstance(item, dict) and item.get("name")
        ]
        return names or [default_name]

    async def _poll_source(
        self,
        *,
        database: str,
        source_type: ProcurementSourceType,
    ) -> dict[str, Any]:
        capability = get_source_capability(source_type)
        state = await self._get_or_create_sync_state(database, source_type, capability.entity_set)
        state.last_polled_at = datetime.now(UTC)
        result = {
            "database": database,
            "source_type": source_type.value,
            "entity_set": capability.entity_set,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "enqueued": 0,
            "error": None,
        }

        if not capability.available or not capability.entity_set:
            state.capability_status = "capability_unavailable"
            state.capability_message = capability.unavailable_reason
            state.last_error = capability.unavailable_reason
            await self.db.flush()
            result["error"] = capability.unavailable_reason
            return result

        state.capability_status = "available"
        state.capability_message = None
        try:
            documents = await self._discover_documents(
                database=database,
                source_type=source_type,
                entity_set=capability.entity_set,
                state=state,
            )
            for document in documents:
                action = await self._upsert_case_from_document(document)
                if action == "created":
                    result["created"] += 1
                    state.cases_created += 1
                elif action == "updated":
                    result["updated"] += 1
                    state.cases_updated += 1
                elif action == "enqueued":
                    result["enqueued"] += 1
                else:
                    result["skipped"] += 1
                    state.cases_skipped += 1
            state.documents_seen += len(documents)
            state.last_success_at = datetime.now(UTC)
            state.last_error = None
            await self._advance_watermark(state, documents)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "procurement.orchestrator.poll_source_failed",
                source_type=source_type.value,
                database=database,
            )
            state.last_error = str(exc)
            state.capability_status = "error"
            state.capability_message = str(exc)
            result["error"] = str(exc)
        await self.db.flush()
        return result

    async def _discover_documents(
        self,
        *,
        database: str,
        source_type: ProcurementSourceType,
        entity_set: str,
        state: ProcurementSourceSyncState,
    ) -> list[NormalizedSourceDocument]:
        capability = get_source_capability(source_type)
        if not capability.lines_entity_set:
            raise MCPUnavailableError(
                f"Табличная часть для {source_type.value} не настроена"
            )
        selection = await self.mcp.call_capability(
            "read_procurement_get_active_document_refs",
            {
                "database": database,
                "linesEntitySet": capability.lines_entity_set,
            },
        )
        candidate_refs = {
            str(value).lower()
            for value in (selection.get("activeRefs") or [])
            if value
        }
        await self._close_cases_outside_active_refs(
            database=database,
            source_type=source_type,
            active_refs=candidate_refs,
        )
        selected_documents = {
            str(raw.get("Ref_Key") or raw.get("ref")).lower(): raw
            for raw in (selection.get("documents") or [])
            if isinstance(raw, dict) and (raw.get("Ref_Key") or raw.get("ref"))
        }
        raw_documents = [
            selected_documents[ref]
            for ref in sorted(candidate_refs)
            if ref in selected_documents
        ]
        sorted_refs = sorted(candidate_refs - set(selected_documents))
        ref_chunks = [
            sorted_refs[index:index + 50]
            for index in range(0, len(sorted_refs), 50)
        ]
        semaphore = asyncio.Semaphore(2)

        async def fetch_chunk(refs: list[str]) -> list[dict[str, Any]]:
            async with semaphore:
                response = await self.mcp.call_capability(
                    "read_document_get_documents",
                    {
                        "database": database,
                        "entitySet": entity_set,
                        "refs": refs,
                        "fields": PROCUREMENT_DOCUMENT_FIELDS,
                    },
                )
            return [
                item
                for item in (response.get("items") or [])
                if isinstance(item, dict)
            ]

        batches = await asyncio.gather(*(fetch_chunk(refs) for refs in ref_chunks))
        documents = [
            normalize_source_document(
                source_type=source_type,
                database=database,
                entity_set=entity_set,
                raw=raw,
            )
            for batch in [raw_documents, *batches]
            for raw in batch
        ]
        existing_hashes = {
            str(ref).lower(): content_hash
            for ref, content_hash in (
                await self.db.execute(
                    select(
                        ProcurementCase.source_1c_ref,
                        ProcurementCase.source_content_hash,
                    ).where(
                        ProcurementCase.source_database == database,
                        ProcurementCase.source_type == source_type.value,
                        ProcurementCase.source_1c_ref.in_(candidate_refs),
                    )
                )
            ).all()
        }
        state.watermark_refs = []
        await self._enrich_document_presentations(
            database,
            [
                document
                for document in documents
                if not document.skip_reason
                and existing_hashes.get(document.ref_key.lower()) != document.content_hash
            ],
        )
        return documents

    async def _close_cases_outside_active_refs(
        self,
        *,
        database: str,
        source_type: ProcurementSourceType,
        active_refs: set[str],
    ) -> None:
        cases = (
            await self.db.execute(
                select(ProcurementCase).where(
                    ProcurementCase.source_database == database,
                    ProcurementCase.source_type == source_type.value,
                    ProcurementCase.status.in_(list(ACTIVE_CASE_STATUSES)),
                )
            )
        ).scalars().all()
        inactive_cases = [
            case for case in cases if case.source_1c_ref.lower() not in active_refs
        ]
        if not inactive_cases:
            return

        capability = get_source_capability(source_type)
        reasons = await self._probe_inactive_reasons(
            database=database,
            entity_set=capability.entity_set or "",
            source_type=source_type,
            refs=[case.source_1c_ref for case in inactive_cases],
        )
        for case in inactive_cases:
            reason = reasons.get(case.source_1c_ref.lower()) or {
                "closed_reason": "inactive_supply_action_or_cancelled",
                "skip_reason": "inactive_supply_action_or_cancelled",
            }
            await self._archive_case(
                case,
                closed_reason=str(reason["closed_reason"]),
                event_type="case_archived_from_source",
                idempotency_key=(
                    f"archive:{case.source_content_hash or case.source_data_version or 'unknown'}:"
                    f"{reason['closed_reason']}"
                )[:255],
                payload={
                    "skip_reason": reason.get("skip_reason"),
                    "closed_reason": reason["closed_reason"],
                    "source_status": reason.get("source_status"),
                },
                source_status=reason.get("source_status"),
                source_data_version=reason.get("source_data_version"),
                source_content_hash=reason.get("source_content_hash"),
            )

    async def _probe_inactive_reasons(
        self,
        *,
        database: str,
        entity_set: str,
        source_type: ProcurementSourceType,
        refs: list[str],
    ) -> dict[str, dict[str, Any]]:
        unique_refs = sorted({value.lower() for value in refs if value})
        if not unique_refs or not entity_set:
            return {}
        reasons: dict[str, dict[str, Any]] = {}
        chunks = [unique_refs[index:index + 50] for index in range(0, len(unique_refs), 50)]
        for chunk in chunks:
            try:
                response = await self.mcp.call_capability(
                    "read_document_get_documents",
                    {
                        "database": database,
                        "entitySet": entity_set,
                        "refs": chunk,
                        "fields": PROCUREMENT_DOCUMENT_FIELDS,
                    },
                )
            except (MCPUnavailableError, MCPCallError):
                for ref in chunk:
                    reasons[ref] = {
                        "closed_reason": "inactive_supply_action_or_cancelled",
                        "skip_reason": "inactive_supply_action_or_cancelled",
                    }
                continue
            found: set[str] = set()
            for raw in response.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                document = normalize_source_document(
                    source_type=source_type,
                    database=database,
                    entity_set=entity_set,
                    raw=raw,
                )
                found.add(document.ref_key.lower())
                closed_reason = self._closed_reason_from_skip(document.skip_reason)
                reasons[document.ref_key.lower()] = {
                    "closed_reason": closed_reason,
                    "skip_reason": document.skip_reason or closed_reason,
                    "source_status": document.status,
                    "source_data_version": document.data_version,
                    "source_content_hash": document.content_hash,
                }
            for ref in chunk:
                if ref not in found:
                    reasons[ref] = {
                        "closed_reason": "deletion_mark",
                        "skip_reason": "missing_in_1c",
                    }
        return reasons

    @staticmethod
    def _closed_reason_from_skip(skip_reason: str | None) -> str:
        if not skip_reason:
            return "inactive_supply_action_or_cancelled"
        if skip_reason.startswith("terminal_status:"):
            return "terminal_status"
        if skip_reason in CLOSED_REASON_LABELS:
            return skip_reason
        return "inactive_supply_action_or_cancelled"

    async def _archive_case(
        self,
        case: ProcurementCase,
        *,
        closed_reason: str,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        source_status: str | None = None,
        source_data_version: str | None = None,
        source_content_hash: str | None = None,
    ) -> None:
        if case.status in TERMINAL_CASE_STATUSES:
            return
        previous = case.status
        case.status = ProcurementCaseStatus.CLOSED.value
        case.closed_at = datetime.now(UTC)
        case.closed_reason = closed_reason
        case.deviation_summary = CLOSED_REASON_LABELS.get(
            closed_reason,
            "Основание больше не актуально в 1С.",
        )
        if source_status is not None:
            case.source_status = source_status
        if source_data_version is not None:
            case.source_data_version = source_data_version
        if source_content_hash is not None:
            case.source_content_hash = source_content_hash
        await self._cancel_current_task(case, reason=case.deviation_summary)
        await self._append_event(
            case,
            event_type=event_type,
            idempotency_key=idempotency_key,
            previous_status=previous,
            new_status=case.status,
            payload=payload or {"closed_reason": closed_reason},
        )
        # Session uses autoflush=False; persist terminal transition before any refresh().
        await self.db.flush()

    async def _cancel_current_task(self, case: ProcurementCase, *, reason: str) -> None:
        if case.current_task_id is None:
            return
        task = await self.db.get(Task, case.current_task_id)
        if task is not None and task.status in {
            TaskStatus.PENDING,
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
        }:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now(UTC)
            task.error_message = reason
        case.current_task_id = None

    async def _reactivate_case(
        self,
        case: ProcurementCase,
        document: NormalizedSourceDocument,
    ) -> None:
        previous = case.status
        case.status = ProcurementCaseStatus.NEW.value
        case.closed_at = None
        case.closed_reason = None
        case.reactivated_at = datetime.now(UTC)
        case.deviation_summary = None
        case.error_message = None
        case.control_point = (
            "KT1" if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED else "basis"
        )
        await self._append_event(
            case,
            event_type="case_reactivated_from_source",
            idempotency_key=f"{document.poll_idempotency_key}:reactivated",
            previous_status=previous,
            new_status=case.status,
            payload={
                "source_data_version": document.data_version,
                "content_hash": document.content_hash,
            },
        )
        # Session uses autoflush=False; persist reactivation before any refresh().
        await self.db.flush()

    async def _enrich_document_presentations(
        self,
        database: str,
        documents: list[NormalizedSourceDocument],
    ) -> None:
        if not documents:
            return

        initiator_refs = {
            document.initiator_1c_ref
            for document in documents
            if self._is_real_1c_ref(document.initiator_1c_ref)
        }
        department_refs = {
            document.department_1c_ref
            for document in documents
            if self._is_real_1c_ref(document.department_1c_ref)
        }
        warehouse_refs = {
            value
            for document in documents
            for value in (
                document.warehouse_1c_ref,
                document.warehouse_from_1c_ref,
                document.warehouse_to_1c_ref,
            )
            if self._is_real_1c_ref(value)
        }
        nomenclature_refs = {
            line.nomenclature_id
            for document in documents
            for line in document.positions
            if self._is_real_1c_ref(line.nomenclature_id)
        }

        results = await asyncio.gather(
            self._read_presentations(
                database,
                "Catalog_Пользователи",
                initiator_refs,
            ),
            self._read_presentations(
                database,
                "Catalog_СтруктураПредприятия",
                department_refs,
            ),
            self._read_presentations(
                database,
                "Catalog_Склады",
                warehouse_refs,
            ),
            self._read_presentations(
                database,
                "Catalog_Номенклатура",
                nomenclature_refs,
                fields=["ЕдиницаИзмерения_Key"],
            ),
            return_exceptions=True,
        )
        maps: list[dict[str, dict[str, Any]]] = [
            result if isinstance(result, dict) else {}
            for result in results
        ]
        initiators, departments, warehouses, nomenclature = maps

        unit_refs = {
            str(item.get("attributes", {}).get("ЕдиницаИзмерения_Key"))
            for item in nomenclature.values()
            if self._is_real_1c_ref(
                str(item.get("attributes", {}).get("ЕдиницаИзмерения_Key") or "")
            )
        }
        try:
            units = await self._read_presentations(
                database,
                "Catalog_УпаковкиЕдиницыИзмерения",
                unit_refs,
            )
        except (MCPUnavailableError, MCPCallError):
            units = {}

        for document in documents:
            document.initiator_name = self._presentation_name(
                initiators,
                document.initiator_1c_ref,
            )
            document.department_name = self._presentation_name(
                departments,
                document.department_1c_ref,
            )
            document.warehouse_name = self._presentation_name(
                warehouses,
                document.warehouse_1c_ref,
            )
            for line in document.positions:
                item = nomenclature.get(line.nomenclature_id.lower(), {})
                line.nomenclature_name = str(item.get("name") or "") or None
                unit_ref = str(
                    item.get("attributes", {}).get("ЕдиницаИзмерения_Key")
                    or line.unit_id
                    or ""
                )
                line.unit_id = unit_ref or None
                line.unit = self._presentation_name(units, unit_ref) or line.unit

    async def _read_presentations(
        self,
        database: str,
        entity_set: str,
        refs: set[str],
        *,
        fields: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        values = sorted(
            {
                value.lower()
                for value in refs
                if self._is_real_1c_ref(value)
            }
        )
        if not values:
            return {}
        chunks = [values[index:index + 20] for index in range(0, len(values), 20)]
        semaphore = asyncio.Semaphore(4)

        async def fetch_chunk(chunk: list[str]) -> Any:
            async with semaphore:
                return await self.mcp.call_capability(
                    "read_reference_get_presentations",
                    {
                        "database": database,
                        "entitySet": entity_set,
                        "refs": chunk,
                        "fields": fields or [],
                    },
                )

        responses = await asyncio.gather(
            *(fetch_chunk(chunk) for chunk in chunks)
        )
        return {
            str(item["ref"]).lower(): item
            for response in responses
            for item in (response.get("items") or [])
            if isinstance(item, dict) and item.get("ref")
        }

    @staticmethod
    def _is_real_1c_ref(value: str | None) -> bool:
        return bool(value and value != ZERO_1C_REF)

    @staticmethod
    def _presentation_name(
        presentations: dict[str, dict[str, Any]],
        ref: str | None,
    ) -> str | None:
        if not ref:
            return None
        item = presentations.get(ref.lower()) or {}
        return str(item.get("name") or "") or None

    async def _search_documents_window(
        self,
        *,
        database: str,
        entity_set: str,
        start: datetime,
        end: datetime,
        page_limit: int,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        response = await self.mcp.call_capability(
            "read_document_search_documents",
            {
                "database": database,
                "entitySet": entity_set,
                "from": start.date().isoformat(),
                "to": end.date().isoformat(),
                "limit": page_limit,
            },
        )
        rows = [
            row
            for row in (response.get("rows") or [])
            if isinstance(row, dict)
        ]
        truncated = bool(response.get("truncated"))
        if not truncated or (end - start) <= timedelta(days=1):
            return rows

        mid = start + (end - start) / 2
        left = await self._search_documents_window(
            database=database,
            entity_set=entity_set,
            start=start,
            end=mid,
            page_limit=page_limit,
        )
        right = await self._search_documents_window(
            database=database,
            entity_set=entity_set,
            start=mid,
            end=end,
            page_limit=page_limit,
        )
        merged: dict[str, dict[str, Any]] = {}
        for row in left + right + rows:
            ref = str(row.get("ref") or row.get("Ref_Key") or "")
            if ref:
                merged[ref] = row
        return list(merged.values())

    async def _active_case_refs(
        self,
        database: str,
        source_type: ProcurementSourceType,
    ) -> set[str]:
        rows = (
            await self.db.execute(
                select(ProcurementCase.source_1c_ref).where(
                    ProcurementCase.source_database == database,
                    ProcurementCase.source_type == source_type.value,
                    ProcurementCase.status.in_(list(ACTIVE_CASE_STATUSES)),
                )
            )
        ).scalars().all()
        return {str(value) for value in rows if value}

    async def _upsert_case_from_document(
        self,
        document: NormalizedSourceDocument,
    ) -> str:
        if document.skip_reason:
            existing = await self.db.scalar(
                select(ProcurementCase).where(
                    ProcurementCase.correlation_id == document.correlation_id
                )
            )
            if existing is not None and existing.status not in TERMINAL_CASE_STATUSES:
                await self._archive_case(
                    existing,
                    closed_reason=self._closed_reason_from_skip(document.skip_reason),
                    event_type="case_archived_from_source",
                    idempotency_key=f"{document.poll_idempotency_key}:closed",
                    payload={"skip_reason": document.skip_reason},
                    source_status=document.status,
                    source_data_version=document.data_version,
                    source_content_hash=document.content_hash,
                )
                return "updated"
            return "skipped"

        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.correlation_id == document.correlation_id)
        )
        if case is None:
            case = await self.db.scalar(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(
                    ProcurementCase.source_database == document.database,
                    ProcurementCase.source_type == document.source_type.value,
                    ProcurementCase.source_1c_ref == document.ref_key,
                )
            )
        if case is None:
            case = await self._create_case(document)
            enqueued = await self._enqueue_kt1(case, document)
            return "enqueued" if enqueued else "created"

        was_terminal = case.status in TERMINAL_CASE_STATUSES
        unchanged = (
            case.source_data_version == document.data_version
            and case.source_content_hash == document.content_hash
            and not was_terminal
        )
        if unchanged:
            presentations_updated = await self._update_case_presentations(case, document)
            return "updated" if presentations_updated else "skipped"

        await self._update_case_card(case, document)
        await self._replace_positions(case, document)
        await self._append_event(
            case,
            event_type="source_document_changed",
            idempotency_key=f"{document.poll_idempotency_key}:changed",
            previous_status=case.status,
            new_status=case.status,
            payload={
                "source_data_version": document.data_version,
                "content_hash": document.content_hash,
                "positions": len(document.positions),
            },
        )
        if was_terminal or case.status == ProcurementCaseStatus.HUMAN_REQUIRED.value:
            await self._reactivate_case(case, document)
        enqueued = await self._enqueue_kt1(case, document)
        return "enqueued" if enqueued else "updated"

    async def _create_case(self, document: NormalizedSourceDocument) -> ProcurementCase:
        case = ProcurementCase(
            correlation_id=document.correlation_id,
            source_type=document.source_type.value,
            source_1c_ref=document.ref_key,
            source_entity_set=document.entity_set,
            source_database=document.database,
            source_number=document.number,
            source_date=document.date,
            source_status=document.status,
            source_data_version=document.data_version,
            source_content_hash=document.content_hash,
            initiator_1c_ref=document.initiator_1c_ref,
            initiator_name=document.initiator_name,
            department_1c_ref=document.department_1c_ref,
            department_name=document.department_name,
            warehouse_1c_ref=document.warehouse_1c_ref,
            warehouse_name=document.warehouse_name,
            warehouse_from_1c_ref=document.warehouse_from_1c_ref,
            warehouse_to_1c_ref=document.warehouse_to_1c_ref,
            organization_1c_ref=document.organization_1c_ref,
            priority_1c_ref=document.priority_1c_ref,
            required_date=document.required_date,
            assigned_agents=(
                [procurement_config.AGENT_ID]
                if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED
                else []
            ),
            current_agent_id=(
                procurement_config.AGENT_ID
                if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED
                else None
            ),
            current_human_role="procurement_orchestrator",
            autonomy_level=0,
            control_point=(
                "KT1" if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED else "basis"
            ),
            requested_operation=(
                "assess_need"
                if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED
                else "monitor_source"
            ),
            status=ProcurementCaseStatus.NEW.value,
            idempotency_key=document.poll_idempotency_key,
            graph_version=procurement_config.GRAPH_VERSION,
            deadline_at=document.required_date,
            closed_reason=None,
            reactivated_at=None,
            case_metadata={
                "source_label": get_source_capability(document.source_type).label_ru,
                "initial_route": (
                    [procurement_config.AGENT_ID]
                    if settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED
                    else []
                ),
                "deadline": document.required_date.isoformat() if document.required_date else None,
                "route_stages": DEFAULT_ROUTE_STAGES,
            },
        )
        self.db.add(case)
        await self.db.flush()
        await self._replace_positions(case, document)
        await self._append_event(
            case,
            event_type="case_created_from_source",
            idempotency_key=f"{document.poll_idempotency_key}:created",
            previous_status=None,
            new_status=case.status,
            payload={
                "source_type": document.source_type.value,
                "source_1c_ref": document.ref_key,
                "source_number": document.number,
                "positions": len(document.positions),
            },
        )
        return case

    async def _update_case_card(
        self,
        case: ProcurementCase,
        document: NormalizedSourceDocument,
    ) -> None:
        case.source_entity_set = document.entity_set
        case.source_database = document.database
        case.source_number = document.number
        case.source_date = document.date
        case.source_status = document.status
        case.source_data_version = document.data_version
        case.source_content_hash = document.content_hash
        case.initiator_1c_ref = document.initiator_1c_ref
        case.initiator_name = document.initiator_name
        case.department_1c_ref = document.department_1c_ref
        case.department_name = document.department_name
        case.warehouse_1c_ref = document.warehouse_1c_ref
        case.warehouse_name = document.warehouse_name
        case.warehouse_from_1c_ref = document.warehouse_from_1c_ref
        case.warehouse_to_1c_ref = document.warehouse_to_1c_ref
        case.organization_1c_ref = document.organization_1c_ref
        case.priority_1c_ref = document.priority_1c_ref
        case.required_date = document.required_date
        case.deadline_at = document.required_date
        metadata = dict(case.case_metadata or {})
        metadata["deadline"] = document.required_date.isoformat() if document.required_date else None
        case.case_metadata = metadata

    async def _update_case_presentations(
        self,
        case: ProcurementCase,
        document: NormalizedSourceDocument,
    ) -> bool:
        changed = False
        for attribute, value in (
            ("initiator_name", document.initiator_name),
            ("department_name", document.department_name),
            ("warehouse_name", document.warehouse_name),
        ):
            if value and getattr(case, attribute) != value:
                setattr(case, attribute, value)
                changed = True

        by_line_id = {position.line_id: position for position in case.positions or []}
        for line in document.positions:
            position = by_line_id.get(line.line_id)
            if position is None:
                continue
            if line.nomenclature_name and position.nomenclature_name != line.nomenclature_name:
                position.nomenclature_name = line.nomenclature_name
                changed = True
            if line.unit and position.unit != line.unit:
                position.unit = line.unit
                changed = True
        if changed:
            await self.db.flush()
        return changed

    async def _replace_positions(
        self,
        case: ProcurementCase,
        document: NormalizedSourceDocument,
    ) -> None:
        existing = (
            await self.db.execute(
                select(ProcurementCasePosition).where(ProcurementCasePosition.case_id == case.id)
            )
        ).scalars().all()
        for row in existing:
            await self.db.delete(row)
        await self.db.flush()
        for line in document.positions:
            self.db.add(
                ProcurementCasePosition(
                    case_id=case.id,
                    line_id=line.line_id,
                    line_number=line.line_number,
                    nomenclature_id=line.nomenclature_id,
                    nomenclature_name=line.nomenclature_name,
                    characteristic_id=line.characteristic_id,
                    unit=line.unit,
                    quantity=line.quantity,
                    required_date=line.required_date,
                    cancelled=line.cancelled,
                    raw_payload=line.raw_payload,
                )
            )
        await self.db.flush()
        await self.db.refresh(case, attribute_names=["positions"])

    async def _enqueue_kt1(
        self,
        case: ProcurementCase,
        document: NormalizedSourceDocument,
    ) -> bool:
        if (
            not self.enqueue_case
            or not settings.PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED
        ):
            return False
        if case.current_task_id is not None:
            current_task = await self.db.get(Task, case.current_task_id)
            if current_task is not None and current_task.status in {
                TaskStatus.PENDING,
                TaskStatus.PLANNING,
                TaskStatus.RUNNING,
            }:
                return False

        run_key = document.poll_idempotency_key
        source_data = positions_to_agent_source_data(document)
        task = Task(
            id=uuid.uuid4(),
            title=f"КТ1: {document.number or document.ref_key}",
            description=get_source_capability(document.source_type).label_ru,
            status=TaskStatus.PENDING,
            task_type="procurement",
            input_payload={
                "correlation_id": case.correlation_id,
                "case_id": str(case.id),
                "source_type": document.source_type.value,
                "source_1c_ref": document.ref_key,
                "human_role": "procurement_orchestrator",
                "autonomy_level": 0,
                "requested_operation": "assess_need",
                "idempotency_key": run_key,
                "source_data": source_data,
            },
            task_metadata={
                "procurement_case_id": str(case.id),
                "source_type": document.source_type.value,
                "agent_slug": procurement_config.AGENT_ID,
            },
        )
        self.db.add(task)
        await self.db.flush()
        case.current_task_id = task.id
        case.current_agent_id = procurement_config.AGENT_ID
        case.assigned_agents = [procurement_config.AGENT_ID]
        await self._append_event(
            case,
            event_type="kt1_task_enqueued",
            idempotency_key=f"{run_key}:enqueued",
            previous_status=case.status,
            new_status=case.status,
            payload={"task_id": str(task.id)},
        )
        await self.db.flush()

        if self.enqueue_case:
            self.pending_dispatches.append((str(case.id), str(task.id)))
        return True

    async def execute_case_task(self, case_id: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        task = await self.db.get(Task, task_id)
        if case is None or task is None:
            return {"status": "failed", "error": "case_or_task_not_found"}

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)
        await self.db.flush()

        payload = dict(task.input_payload or {})
        payload["task_id"] = str(task.id)
        payload["case_id"] = str(case.id)
        payload["db"] = self.db

        from app.agents.procurement_agent.service import ProcurementAgent

        result = await ProcurementAgent().run(payload)
        result_payload = (
            result.model_dump(mode="json")
            if hasattr(result, "model_dump")
            else dict(result)
        )
        case.latest_result = result_payload
        case.status = str(
            result_payload.get("case_status")
            or case.status
            or ProcurementCaseStatus.FAILED.value
        )
        case.control_point = result_payload.get("control_point") or case.control_point
        if case.status in TERMINAL_CASE_STATUSES:
            case.closed_at = datetime.now(UTC)
        if result_payload.get("requires_human_review"):
            case.deviation_summary = result_payload.get("summary")

        task.status = (
            TaskStatus.COMPLETED
            if result_payload.get("status") != "failed"
            else TaskStatus.FAILED
        )
        task.finished_at = datetime.now(UTC)
        task.final_result = result_payload
        task.requires_human_review = bool(result_payload.get("requires_human_review"))
        if task.status is TaskStatus.FAILED:
            task.error_message = result_payload.get("summary")

        await self._append_event(
            case,
            event_type="kt1_task_completed",
            idempotency_key=f"{payload.get('idempotency_key')}:completed",
            previous_status=None,
            new_status=case.status,
            payload={
                "task_id": str(task.id),
                "agent_status": result_payload.get("status"),
                "case_status": case.status,
            },
        )
        await self.db.flush()
        return result_payload

    async def list_dashboard(self, *, view: str = "active") -> dict[str, Any]:
        normalized_view = view if view in {"active", "processing", "archive"} else "active"
        if normalized_view == "archive":
            status_filter = list(TERMINAL_CASE_STATUSES)
        else:
            status_filter = list(ACTIVE_CASE_STATUSES)

        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(ProcurementCase.status.in_(status_filter))
                .order_by(
                    ProcurementCase.source_date.desc().nullslast(),
                    ProcurementCase.source_number.desc().nullslast(),
                    ProcurementCase.updated_at.desc().nullslast(),
                )
            )
        ).scalars().all()
        if normalized_view == "processing":
            # Same active cards, but presented as processing cases.
            cases = [case for case in cases if case.status in ACTIVE_CASE_STATUSES]

        archive_count = await self.db.scalar(
            select(func.count()).select_from(ProcurementCase).where(
                ProcurementCase.status.in_(list(TERMINAL_CASE_STATUSES))
            )
        )
        processing_count = await self.db.scalar(
            select(func.count()).select_from(ProcurementCase).where(
                ProcurementCase.status.in_(list(ACTIVE_CASE_STATUSES))
            )
        )

        sync_states = (
            await self.db.execute(select(ProcurementSourceSyncState))
        ).scalars().all()
        groups = []
        for capability in list_source_capabilities():
            group_cases = [
                self._serialize_case_summary(case)
                for case in cases
                if case.source_type == capability.source_type.value
            ]
            sync = next(
                (
                    item
                    for item in sync_states
                    if item.source_type == capability.source_type.value
                ),
                None,
            )
            groups.append(
                {
                    "source_type": capability.source_type.value,
                    "label_ru": capability.label_ru,
                    "entity_set": capability.entity_set,
                    "available": capability.available,
                    "unavailable_reason": capability.unavailable_reason,
                    "cases": group_cases,
                    "cases_count": len(group_cases),
                    "sync": self._serialize_sync_state(sync, capability),
                }
            )
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "view": normalized_view,
            "groups": groups,
            "total_cases": len(cases),
            "counts": {
                "active": int(processing_count or 0),
                "processing": int(processing_count or 0),
                "archive": int(archive_count or 0),
            },
        }

    async def get_case(self, case_id: uuid.UUID) -> dict[str, Any] | None:
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        if case is None:
            return None
        detail = self._serialize_case_detail(case)
        detail["events"] = await self.list_case_events(case_id)
        detail["route_stages"] = self._serialize_route_stages(case)
        detail["timeline"] = self._serialize_timeline(detail["events"], case)
        detail["current_state"] = {
            "status": case.status,
            "control_point": case.control_point,
            "current_agent_id": case.current_agent_id,
            "current_agent_label": (
                "Агент закупок и логистики"
                if case.current_agent_id == procurement_config.AGENT_ID
                else case.current_agent_id
            ),
            "requires_human_review": bool((case.latest_result or {}).get("requires_human_review")),
            "summary": (case.latest_result or {}).get("summary") or case.deviation_summary,
            "task_id": str(case.current_task_id) if case.current_task_id else None,
            "closed_reason": case.closed_reason,
            "closed_reason_label": CLOSED_REASON_LABELS.get(case.closed_reason or ""),
            "source_active": case.status in ACTIVE_CASE_STATUSES,
        }
        return detail

    async def list_case_events(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        events = (
            await self.db.execute(
                select(ProcurementCaseEvent)
                .where(ProcurementCaseEvent.case_id == case_id)
                .order_by(ProcurementCaseEvent.created_at.asc())
            )
        ).scalars().all()
        return [self._serialize_event(event) for event in events]

    async def list_sync_status(self) -> list[dict[str, Any]]:
        states = (
            await self.db.execute(select(ProcurementSourceSyncState))
        ).scalars().all()
        by_type = {item.source_type: item for item in states}
        return [
            self._serialize_sync_state(by_type.get(capability.source_type.value), capability)
            for capability in list_source_capabilities()
        ]

    async def _get_or_create_sync_state(
        self,
        database: str,
        source_type: ProcurementSourceType,
        entity_set: str | None,
    ) -> ProcurementSourceSyncState:
        state = await self.db.scalar(
            select(ProcurementSourceSyncState).where(
                ProcurementSourceSyncState.database_name == database,
                ProcurementSourceSyncState.source_type == source_type.value,
            )
        )
        if state is not None:
            state.entity_set = entity_set
            return state
        state = ProcurementSourceSyncState(
            database_name=database,
            source_type=source_type.value,
            entity_set=entity_set,
            capability_status="unknown",
            watermark_refs=[],
        )
        self.db.add(state)
        await self.db.flush()
        return state

    async def _advance_watermark(
        self,
        state: ProcurementSourceSyncState,
        documents: list[NormalizedSourceDocument],
    ) -> None:
        dated = [doc for doc in documents if doc.date is not None and not doc.skip_reason]
        if not dated:
            return
        latest = max(doc.date for doc in dated if doc.date is not None)
        state.watermark_date = latest

    async def _append_event(
        self,
        case: ProcurementCase,
        *,
        event_type: str,
        idempotency_key: str,
        previous_status: str | None,
        new_status: str | None,
        payload: dict[str, Any] | None = None,
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
                agent_id=procurement_config.AGENT_ID,
                actor_role="procurement_orchestrator",
                previous_status=previous_status,
                new_status=new_status,
                idempotency_key=idempotency_key,
                payload=payload or {},
            )
        )

    def _serialize_case_summary(self, case: ProcurementCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "correlation_id": case.correlation_id,
            "source_type": case.source_type,
            "source_1c_ref": case.source_1c_ref,
            "source_number": case.source_number,
            "source_date": case.source_date.isoformat() if case.source_date else None,
            "source_status": case.source_status,
            "status": case.status,
            "control_point": case.control_point,
            "current_agent_id": case.current_agent_id,
            "current_agent_name": (
                "Агент закупок и логистики"
                if case.current_agent_id == procurement_config.AGENT_ID
                else case.current_agent_id
            ),
            "current_task_id": str(case.current_task_id) if case.current_task_id else None,
            "required_date": case.required_date.isoformat() if case.required_date else None,
            "deadline_at": case.deadline_at.isoformat() if case.deadline_at else None,
            "positions_count": len(case.positions or []),
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
            "summary": (case.latest_result or {}).get("summary") or case.deviation_summary,
            "requires_human_review": bool((case.latest_result or {}).get("requires_human_review")),
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
            "closed_reason": case.closed_reason,
            "closed_reason_label": CLOSED_REASON_LABELS.get(case.closed_reason or ""),
            "reactivated_at": (
                case.reactivated_at.isoformat() if case.reactivated_at else None
            ),
            "source_active": case.status in ACTIVE_CASE_STATUSES,
        }

    def _serialize_case_detail(self, case: ProcurementCase) -> dict[str, Any]:
        payload = self._serialize_case_summary(case)
        payload.update(
            {
                "source_entity_set": case.source_entity_set,
                "source_database": case.source_database,
                "source_data_version": case.source_data_version,
                "initiator_1c_ref": case.initiator_1c_ref,
                "initiator_name": case.initiator_name,
                "department_1c_ref": case.department_1c_ref,
                "department_name": case.department_name,
                "warehouse_1c_ref": case.warehouse_1c_ref,
                "warehouse_name": case.warehouse_name,
                "warehouse_from_1c_ref": case.warehouse_from_1c_ref,
                "warehouse_to_1c_ref": case.warehouse_to_1c_ref,
                "organization_1c_ref": case.organization_1c_ref,
                "priority_1c_ref": case.priority_1c_ref,
                "assigned_agents": case.assigned_agents or [],
                "deviation_summary": case.deviation_summary,
                "latest_result": case.latest_result,
                "case_metadata": case.case_metadata,
                "positions": [
                    {
                        "id": str(position.id),
                        "line_id": position.line_id,
                        "line_number": position.line_number,
                        "nomenclature_id": position.nomenclature_id,
                        "nomenclature_name": position.nomenclature_name,
                        "characteristic_id": position.characteristic_id,
                        "unit": position.unit,
                        "quantity": str(position.quantity),
                        "required_date": (
                            position.required_date.isoformat()
                            if position.required_date
                            else None
                        ),
                        "supply_action": (
                            (position.raw_payload or {}).get("supply_action")
                            or (position.raw_payload or {}).get("ВариантОбеспечения")
                            or (position.raw_payload or {}).get("Действие")
                            or (position.raw_payload or {}).get(
                                "ОбеспечениеЗаказовПриПоддержанииЗапаса"
                            )
                            or (position.raw_payload or {}).get(
                                "МетодОбеспеченияПотребностей"
                            )
                        ),
                        "cancelled": position.cancelled,
                    }
                    for position in case.positions or []
                ],
                "events": [],
                "route_stages": self._serialize_route_stages(case),
                "timeline": [],
                "current_state": None,
            }
        )
        return payload

    def _serialize_route_stages(self, case: ProcurementCase) -> list[dict[str, Any]]:
        configured = (case.case_metadata or {}).get("route_stages") or DEFAULT_ROUTE_STAGES
        status_map = {
            ProcurementCaseStatus.NEW.value: "basis",
            ProcurementCaseStatus.DATA_CHECK.value: "data",
            ProcurementCaseStatus.COVERAGE_CHECK.value: "coverage",
            ProcurementCaseStatus.HUMAN_REQUIRED.value: "coverage",
            ProcurementCaseStatus.BLOCKED.value: "coverage",
            ProcurementCaseStatus.CLOSED.value: "receipt",
            ProcurementCaseStatus.FAILED.value: "data",
        }
        current_stage = case.control_point or status_map.get(case.status, "basis")
        if current_stage == "KT1":
            current_stage = "basis"
        stages: list[dict[str, Any]] = []
        reached_current = False
        for item in sorted(configured, key=lambda row: int(row.get("order") or 0)):
            stage_id = str(item.get("stage_id") or "")
            if case.status in TERMINAL_CASE_STATUSES and case.closed_reason:
                status = "completed" if stage_id == "basis" else "skipped"
            elif stage_id == current_stage:
                status = "running"
                reached_current = True
            elif not reached_current:
                status = "completed"
            else:
                status = "pending"
            stages.append(
                {
                    "stage_id": stage_id,
                    "label": item.get("label") or stage_id,
                    "order": int(item.get("order") or 0),
                    "status": status,
                    "summary": None,
                }
            )
        return stages

    def _serialize_timeline(
        self,
        events: list[dict[str, Any]],
        case: ProcurementCase,
    ) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("event_type") or "")
            kind = "system"
            if "agent" in event_type or event_type.startswith("kt1_"):
                kind = "agent_run"
            elif event_type.startswith("case_") or event_type.startswith("source_"):
                kind = "status_change"
            title = {
                "case_created_from_source": "Кейс создан по основанию 1С",
                "source_document_changed": "Основание 1С изменено",
                "case_archived_from_source": "Кейс архивирован",
                "case_reactivated_from_source": "Кейс возвращён в работу",
                "kt1_task_enqueued": "Запущен этап агента",
                "kt1_task_completed": "Этап агента завершён",
            }.get(event_type, event_type)
            timeline.append(
                {
                    "id": event.get("id"),
                    "at": event.get("created_at"),
                    "kind": kind,
                    "title": title,
                    "detail": (event.get("payload") or {}).get("skip_reason")
                    or (event.get("payload") or {}).get("closed_reason")
                    or case.deviation_summary,
                    "actor_id": event.get("agent_id"),
                    "actor_label": event.get("actor_role"),
                    "stage_id": case.control_point,
                    "status": event.get("new_status"),
                    "payload": event.get("payload") or {},
                }
            )
        return timeline

    def _serialize_event(self, event: ProcurementCaseEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "agent_id": event.agent_id,
            "actor_role": event.actor_role,
            "previous_status": event.previous_status,
            "new_status": event.new_status,
            "payload": event.payload or {},
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    def _serialize_sync_state(
        self,
        state: ProcurementSourceSyncState | None,
        capability: Any,
    ) -> dict[str, Any]:
        return {
            "source_type": capability.source_type.value,
            "label_ru": capability.label_ru,
            "entity_set": capability.entity_set,
            "available": capability.available,
            "unavailable_reason": capability.unavailable_reason,
            "capability_status": (
                state.capability_status
                if state is not None
                else ("capability_unavailable" if not capability.available else "unknown")
            ),
            "capability_message": (
                state.capability_message
                if state is not None
                else capability.unavailable_reason
            ),
            "database_name": state.database_name if state is not None else None,
            "last_polled_at": state.last_polled_at.isoformat() if state and state.last_polled_at else None,
            "last_success_at": (
                state.last_success_at.isoformat() if state and state.last_success_at else None
            ),
            "watermark_date": (
                state.watermark_date.isoformat() if state and state.watermark_date else None
            ),
            "last_error": state.last_error if state is not None else None,
            "documents_seen": state.documents_seen if state is not None else 0,
            "cases_created": state.cases_created if state is not None else 0,
            "cases_updated": state.cases_updated if state is not None else 0,
            "cases_skipped": state.cases_skipped if state is not None else 0,
        }


def build_poll_lock_key() -> str:
    digest = hashlib.sha1(b"procurement-orchestrator-poll").hexdigest()[:12]
    return f"procurement:orchestrator:poll:{digest}"


__all__ = ["ProcurementOrchestratorService", "build_poll_lock_key"]
