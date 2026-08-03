from __future__ import annotations

import asyncio
import calendar
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
    parse_1c_datetime,
)
from app.agents.procurement_role_agents.config import (
    OMTO_CHIEF_AGENT_ID,
    OMTO_SUPPORT_MANAGER_AGENT_ID,
    PRODUCTION_DISPATCHER_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    PURCHASE_MANAGER_AGENT_ID,
    QUALITY_ENGINEER_AGENT_ID,
    QUALITY_ROLE_AGENT_IDS,
    WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
    agent_id_for_quality_status,
    agent_id_for_source,
    agent_label,
)
from app.agents.procurement_role_agents.warehouse_availability import (
    COMPLEX_CHIEF_SPEC,
    PICKER_SPEC,
    WarehouseAvailabilitySpec,
    clear_workspace_action_keys,
    is_warehouse_availability_case,
    mirror_picker_fields_from_complex,
    warehouse_availability_spec,
)
from app.agents.warehouse_picker_agent.department import is_montage_section_2_department
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
from app.services.procurement_case_statuses import (
    ACTIVE_CASE_STATUSES,
    SOURCE_MONITORED_CASE_STATUSES,
)

logger = get_logger(__name__)

# Оркестратор держит кейсы в закупке (ordered) во «В работе»,
# иначе они пропадают из поиска и рассинхронизируются с ролевыми агентами.
ORCHESTRATOR_PROCESSING_CASE_STATUSES = SOURCE_MONITORED_CASE_STATUSES
BLOCKING_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_HUMAN,
        TaskStatus.WAITING_EXTERNAL,
        TaskStatus.FAILED,
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
    "Основание",
    "Основание_Type",
    "ДокументОснование",
    "ДокументОснование_Type",
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
    "source_too_old": "Документ старше двух календарных месяцев",
    "quality_released": "ОТК завершил проверку, материалы разрешены к использованию",
}
DEFAULT_ROUTE_STAGES = [
    {"stage_id": "basis", "label": "Основание", "order": 1},
    {"stage_id": "data", "label": "Данные", "order": 2},
    {"stage_id": "coverage", "label": "Обеспечение", "order": 3},
    {"stage_id": "purchase", "label": "Закупка", "order": 4},
    {"stage_id": "quality", "label": "ОТК", "order": 5},
    {"stage_id": "payment", "label": "Оплата", "order": 6},
    {"stage_id": "delivery", "label": "Поставка", "order": 7},
    {"stage_id": "receipt", "label": "Оприходование", "order": 8},
]


def _source_date_cutoff(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    target_month = current.month - 2
    target_year = current.year
    if target_month <= 0:
        target_month += 12
        target_year -= 1
    target_day = min(current.day, calendar.monthrange(target_year, target_month)[1])
    return current.replace(
        year=target_year,
        month=target_month,
        day=target_day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _is_recent_source_document(document: NormalizedSourceDocument) -> bool:
    return document.date is not None and document.date >= _source_date_cutoff()


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

    async def poll_once(
        self,
        *,
        source_types: set[ProcurementSourceType] | frozenset[ProcurementSourceType] | None = None,
        run_agent_maintenance: bool = True,
    ) -> dict[str, Any]:
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
            "source_types_filter": (
                sorted(item.value for item in source_types) if source_types else None
            ),
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
                if source_types is not None and capability.source_type not in source_types:
                    continue
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
        if run_agent_maintenance:
            picker_sync = await self.ensure_picker_agent_work()
            summary["picker_status_reported"] = picker_sync["reported"]
            summary["picker_enqueued"] = picker_sync["enqueued"]
            summary["picker_redispatched"] = picker_sync["redispatched"]
            complex_sync = await self.ensure_complex_chief_agent_work()
            summary["complex_status_reported"] = complex_sync["reported"]
            summary["complex_enqueued"] = complex_sync["enqueued"]
            summary["complex_redispatched"] = complex_sync["redispatched"]
            summary["complex_migrated"] = complex_sync.get("migrated", 0)
            purchase_manager_sync = await self.ensure_purchase_manager_work()
            summary["purchase_manager_status_reported"] = purchase_manager_sync[
                "reported"
            ]
            summary["purchase_manager_enqueued"] = purchase_manager_sync["enqueued"]
            summary["purchase_manager_redispatched"] = purchase_manager_sync[
                "redispatched"
            ]
            summary["purchase_manager_assigned"] = purchase_manager_sync["assigned"]
            backfilled = await self.ensure_active_case_assignments()
            summary["backfilled"] = backfilled
            summary["enqueued"] += backfilled
            claimed = await self.claim_engineer_dispatches(limit=5)
            summary["engineer_dispatched"] = claimed
        summary["finished_at"] = datetime.now(UTC).isoformat()
        return summary

    async def mark_material_cases_actualized(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Stamp last_actualized_at for every active material-order case.

        UI field «Обновлён» must move on every successful 30-minute cycle even
        when coverage fingerprint is unchanged.
        """
        stamped_at = (now or datetime.now(UTC)).isoformat()
        cases = (
            await self.db.execute(
                select(ProcurementCase).where(
                    ProcurementCase.source_type
                    == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
                    ProcurementCase.status.notin_(list(TERMINAL_CASE_STATUSES)),
                )
            )
        ).scalars().all()
        stamped = 0
        from sqlalchemy.orm.attributes import flag_modified

        for case in cases:
            metadata = dict(case.case_metadata or {})
            metadata["last_actualized_at"] = stamped_at
            case.case_metadata = metadata
            flag_modified(case, "case_metadata")
            stamped += 1
        return stamped

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
        selection_payload: dict[str, Any] = {
            "database": database,
            "linesEntitySet": capability.lines_entity_set,
        }
        if source_type is ProcurementSourceType.REORDER_POINT:
            existing_active_refs = (
                await self.db.scalars(
                    select(ProcurementCase.source_1c_ref).where(
                        ProcurementCase.source_database == database,
                        ProcurementCase.source_type == source_type.value,
                        ProcurementCase.status.in_(list(SOURCE_MONITORED_CASE_STATUSES)),
                    )
                )
            ).all()
            selection_payload.update(
                {
                    "dateFrom": _source_date_cutoff().date().isoformat(),
                    "includeRefs": list(dict.fromkeys(existing_active_refs)),
                }
            )
        selection = await self.mcp.call_capability(
            "read_procurement_get_active_document_refs",
            selection_payload,
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
                    ProcurementCase.status.in_(list(SOURCE_MONITORED_CASE_STATUSES)),
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
            case.source_synced_at = datetime.now(UTC)
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
        # Ролевые рабочие места должны видеть тот же архив, что и оркестратор.
        metadata = dict(case.case_metadata or {})
        archived_at = datetime.now(UTC).isoformat()
        if (
            case.source_type == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
            and is_montage_section_2_department(case.department_name)
        ) or metadata.get("picker_invoked_at"):
            metadata["picker_workspace_archived_at"] = archived_at
            metadata.setdefault("picker_archived_bucket", "attention")
            metadata["picker_workspace_status"] = "archived"
        if (
            case.source_type == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
            and not is_montage_section_2_department(case.department_name)
        ) or metadata.get("complex_invoked_at"):
            metadata["complex_workspace_archived_at"] = archived_at
            metadata.setdefault("complex_archived_bucket", "attention")
            metadata["complex_workspace_status"] = "archived"
        if (
            metadata.get("engineer_invoked_at")
            and not is_montage_section_2_department(case.department_name)
            and not metadata.get("complex_invoked_at")
        ):
            metadata["engineer_workspace_archived_at"] = archived_at
            metadata.setdefault("engineer_archived_bucket", "attention")
            metadata["engineer_workspace_status"] = "archived"
        if metadata.get("dispatcher_invoked_at") or case.source_type == (
            ProcurementSourceType.REORDER_POINT.value
        ):
            metadata["dispatcher_workspace_archived_at"] = archived_at
            metadata.setdefault("dispatcher_archived_bucket", "attention")
            metadata["dispatcher_workspace_status"] = "archived"
        if metadata.get("purchase_manager_invoked_at") or metadata.get(
            "purchase_manager_output"
        ):
            metadata["purchase_manager_workspace_archived_at"] = archived_at
            metadata["purchase_manager_workspace_status"] = "archived"
        case.case_metadata = metadata
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
            TaskStatus.WAITING_HUMAN,
            TaskStatus.WAITING_EXTERNAL,
            TaskStatus.FAILED,
        }:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now(UTC)
            task.error_message = reason
        case.current_task_id = None
        case.current_agent_id = None

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

        basis_documents = await self._read_source_basis_documents(database, documents)
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
            basis = basis_documents.get(
                (
                    str(document.source_basis_type or ""),
                    str(document.source_basis_1c_ref or "").lower(),
                ),
                {},
            )
            document.source_basis_number = str(basis.get("Number") or "") or None
            document.source_basis_date = parse_1c_datetime(basis.get("Date"))
            document.source_basis_status = str(basis.get("Статус") or "") or None
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

    async def _read_source_basis_documents(
        self,
        database: str,
        documents: list[NormalizedSourceDocument],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        requested: list[tuple[str, str, str]] = []
        for document in documents:
            basis_type = str(document.source_basis_type or "")
            basis_ref = str(document.source_basis_1c_ref or "").lower()
            entity_set = basis_type.removeprefix("StandardODATA.")
            if entity_set.startswith("Document_") and self._is_real_1c_ref(basis_ref):
                requested.append((basis_type, entity_set, basis_ref))
        if not requested:
            return {}

        semaphore = asyncio.Semaphore(4)

        async def fetch_one(
            basis_type: str,
            entity_set: str,
            basis_ref: str,
        ) -> tuple[tuple[str, str], dict[str, Any]]:
            async with semaphore:
                try:
                    payload = await self.mcp.call_capability(
                        "read_document_get_documents",
                        {
                            "database": database,
                            "entitySet": entity_set,
                            "ref": basis_ref,
                        },
                    )
                except (MCPUnavailableError, MCPCallError):
                    payload = {}
            return (basis_type, basis_ref), payload

        return dict(await asyncio.gather(*(fetch_one(*item) for item in requested)))

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
                    ProcurementCase.status.in_(list(SOURCE_MONITORED_CASE_STATUSES)),
                )
            )
        ).scalars().all()
        return {str(value) for value in rows if value}

    async def _upsert_case_from_document(
        self,
        document: NormalizedSourceDocument,
    ) -> str:
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
        if case is not None:
            case.source_synced_at = datetime.now(UTC)

        # New and archived source documents outside the rolling two-calendar-month
        # window must not appear as active cases. An already active case remains
        # tracked until its source closes, even after it crosses the cutoff.
        if not _is_recent_source_document(document) and (
            case is None or case.status in TERMINAL_CASE_STATUSES
        ):
            return "skipped"

        if document.skip_reason:
            if case is not None and case.status not in TERMINAL_CASE_STATUSES:
                await self._archive_case(
                    case,
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

        if case is None:
            case = await self._create_case(document)
            enqueued = await self._enqueue_role_agent(case)
            return "enqueued" if enqueued else "created"

        if (
            case.status == ProcurementCaseStatus.CLOSED.value
            and case.closed_reason == "quality_released"
        ):
            # Fulfilment is complete. A still-active source document must not
            # reopen a case that successfully passed the final OTK stage.
            return "skipped"

        was_terminal = case.status in TERMINAL_CASE_STATUSES
        unchanged = (
            case.source_data_version == document.data_version
            and case.source_content_hash == document.content_hash
            and not was_terminal
        )
        if unchanged:
            presentations_updated = await self._update_case_presentations(case, document)
            enqueued = await self._enqueue_role_agent(case)
            if enqueued:
                return "enqueued"
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
        enqueued = await self._enqueue_role_agent(case)
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
            source_synced_at=datetime.now(UTC),
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
            assigned_agents=[],
            current_agent_id=None,
            current_human_role="procurement_orchestrator",
            autonomy_level=0,
            control_point="basis",
            requested_operation="route_source_role",
            status=ProcurementCaseStatus.NEW.value,
            idempotency_key=document.poll_idempotency_key,
            graph_version=procurement_config.GRAPH_VERSION,
            deadline_at=document.required_date,
            closed_reason=None,
            reactivated_at=None,
            case_metadata={
                "source_label": get_source_capability(document.source_type).label_ru,
                "initial_route": [agent_id_for_source(document.source_type.value)],
                "deadline": document.required_date.isoformat() if document.required_date else None,
                "source_basis_1c_ref": document.source_basis_1c_ref,
                "source_basis_type": document.source_basis_type,
                "source_basis_number": document.source_basis_number,
                "source_basis_date": (
                    document.source_basis_date.isoformat()
                    if document.source_basis_date
                    else None
                ),
                "source_basis_status": document.source_basis_status,
                "production_order_1c_ref": document.production_order_1c_ref,
                "production_order_type": document.production_order_type,
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
        metadata["deadline"] = (
            document.required_date.isoformat() if document.required_date else None
        )
        metadata["source_basis_1c_ref"] = document.source_basis_1c_ref
        metadata["source_basis_type"] = document.source_basis_type
        metadata["source_basis_number"] = document.source_basis_number
        metadata["source_basis_date"] = (
            document.source_basis_date.isoformat()
            if document.source_basis_date
            else None
        )
        metadata["source_basis_status"] = document.source_basis_status
        metadata["production_order_1c_ref"] = document.production_order_1c_ref
        metadata["production_order_type"] = document.production_order_type
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

    @staticmethod
    def _resolve_role_agent_id(case: ProcurementCase) -> str:
        metadata = case.case_metadata or {}
        # Заказы материалов МУ №2 всегда идут кладовщику (не инженеру/диспетчеру),
        # даже если раньше был ошибочный handoff инженера → диспетчер.
        if case.source_type == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value:
            if (
                metadata.get("purchase_manager_workspace_status") == "awaiting_action"
                and (
                    (
                        metadata.get("supplier_order_coverage")
                        if isinstance(metadata.get("supplier_order_coverage"), dict)
                        else {}
                    ).get("coverage_status")
                    == "full"
                )
            ):
                return PURCHASE_MANAGER_AGENT_ID
            if (
                metadata.get("picker_handoff_agent_id") == OMTO_CHIEF_AGENT_ID
                or metadata.get("complex_handoff_agent_id") == OMTO_CHIEF_AGENT_ID
                or (
                    case.current_agent_id == OMTO_CHIEF_AGENT_ID
                    and case.control_point == "omto"
                )
            ):
                return OMTO_CHIEF_AGENT_ID
            if is_montage_section_2_department(case.department_name):
                return WAREHOUSE_PICKER_AGENT_ID
            # Legacy engineer → dispatcher handoff remains for already decided engineer cases.
            if (
                metadata.get("engineer_workspace_archived_at")
                or metadata.get("engineer_action_at")
                or (
                    metadata.get("engineer_workspace_status") == "awaiting_action"
                    and metadata.get("engineer_decision_kind")
                )
            ) and (
                (
                    metadata.get("engineer_handoff_agent_id")
                    == PRODUCTION_DISPATCHER_AGENT_ID
                    and case.control_point == "chief_dispatcher"
                )
                or (
                    case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
                    and case.control_point == "chief_dispatcher"
                )
            ):
                return PRODUCTION_DISPATCHER_AGENT_ID
            return WAREHOUSE_COMPLEX_CHIEF_AGENT_ID
        if (
            metadata.get("engineer_handoff_agent_id") == PRODUCTION_DISPATCHER_AGENT_ID
            and case.control_point == "chief_dispatcher"
        ):
            return PRODUCTION_DISPATCHER_AGENT_ID
        if (
            case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
            and case.control_point == "chief_dispatcher"
        ):
            return PRODUCTION_DISPATCHER_AGENT_ID
        return agent_id_for_source(case.source_type)

    @staticmethod
    def _role_completion_key(case: ProcurementCase, agent_id: str) -> str:
        source_revision = (
            case.source_content_hash
            or case.source_data_version
            or case.updated_at.isoformat()
        )
        if (
            agent_id
            in {
                PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
                WAREHOUSE_PICKER_AGENT_ID,
                WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
            }
            and case.source_synced_at is not None
        ):
            source_revision = f"{source_revision}:{case.source_synced_at.isoformat()}"
        if agent_id == PRODUCTION_DISPATCHER_AGENT_ID:
            metadata = case.case_metadata or {}
            engineer_fp = metadata.get("engineer_evidence_fingerprint")
            synced = (
                case.source_synced_at.isoformat() if case.source_synced_at else None
            )
            suffix = engineer_fp or synced
            if suffix:
                source_revision = f"{source_revision}:{suffix}"
        if agent_id == PURCHASE_MANAGER_AGENT_ID:
            coverage_fp = (case.case_metadata or {}).get(
                "supplier_order_coverage_fingerprint"
            )
            if coverage_fp:
                source_revision = f"{source_revision}:{coverage_fp}"
        return f"{agent_id}:{source_revision}"

    @staticmethod
    def _role_source_data(case: ProcurementCase) -> dict[str, Any]:
        metadata = case.case_metadata or {}
        warehouse_ids = [
            value
            for value in (
                case.warehouse_1c_ref,
                case.warehouse_from_1c_ref,
                case.warehouse_to_1c_ref,
            )
            if value
        ]
        return {
            "case_number": case.source_number or str(case.id),
            "source_database": case.source_database,
            "source_number": case.source_number,
            "source_date": case.source_date.isoformat() if case.source_date else None,
            "source_status": case.source_status,
            "source_data_version": case.source_data_version,
            "source_synced_at": (
                case.source_synced_at.isoformat() if case.source_synced_at else None
            ),
            "positions": [
                {
                    "line_id": position.line_id,
                    "line_number": position.line_number,
                    "nomenclature_id": position.nomenclature_id,
                    "nomenclature_name": position.nomenclature_name,
                    "characteristic_id": position.characteristic_id,
                    "unit": position.unit,
                    "quantity": str(position.quantity),
                    "direct_quantity": str(position.quantity),
                    "gross_quantity": str(position.quantity),
                    "required_date": (
                        position.required_date.isoformat()
                        if position.required_date
                        else None
                    ),
                    "raw_payload": position.raw_payload or {},
                    "project_id": (position.raw_payload or {}).get("Назначение_Key"),
                    "production_stage_id": (position.raw_payload or {}).get("Этап_Key"),
                    "minimum_stock": (position.raw_payload or {}).get(
                        "МинимальноеКоличествоЗапаса_После"
                    )
                    or (position.raw_payload or {}).get("МинимальноеКоличествоЗапаса_До"),
                    "maximum_stock": (position.raw_payload or {}).get(
                        "МаксимальноеКоличествоЗапаса_После"
                    )
                    or (position.raw_payload or {}).get("МаксимальноеКоличествоЗапаса_До"),
                }
                for position in case.positions or []
                if not position.cancelled
            ],
            "required_date": (
                case.required_date.isoformat() if case.required_date else None
            ),
            "requested_date": (
                case.required_date.isoformat() if case.required_date else None
            ),
            "warehouse_ids": list(dict.fromkeys(warehouse_ids)),
            "organization_id": case.organization_1c_ref,
            "source_basis_1c_ref": metadata.get("source_basis_1c_ref"),
            "source_basis_type": metadata.get("source_basis_type"),
            "source_basis_number": metadata.get("source_basis_number"),
            "source_basis_date": metadata.get("source_basis_date"),
            "source_basis_status": metadata.get("source_basis_status"),
            "production_order_1c_ref": metadata.get("production_order_1c_ref"),
            "production_order_type": metadata.get("production_order_type"),
            "production_preparation_engineer_output": metadata.get(
                "production_preparation_engineer_output"
            ),
            "stock_growth_coefficient": metadata.get("stock_growth_coefficient"),
            "supplier_order_coverage": metadata.get("supplier_order_coverage"),
            "material_order_coverage": metadata.get("material_order_coverage"),
        }

    async def _enqueue_role_agent(self, case: ProcurementCase) -> bool:
        if not self.enqueue_case or case.status in TERMINAL_CASE_STATUSES:
            return False

        metadata = dict(case.case_metadata or {})
        quality_agent = metadata.get("next_quality_agent") or agent_id_for_quality_status(
            case.status
        )
        if quality_agent:
            agent_id = str(quality_agent)
        else:
            agent_id = self._resolve_role_agent_id(case)
        completion_key = self._role_completion_key(case, agent_id)
        if (case.case_metadata or {}).get("role_agent_completion_key") == completion_key:
            return False

        if case.current_task_id is not None:
            current_task = await self.db.get(Task, case.current_task_id)
            if current_task is not None and current_task.status in BLOCKING_TASK_STATUSES:
                task_key = (current_task.task_metadata or {}).get("role_completion_key")
                if (
                    current_task.status
                    in {TaskStatus.WAITING_HUMAN, TaskStatus.WAITING_EXTERNAL}
                    and task_key != completion_key
                ):
                    current_task.status = TaskStatus.CANCELLED
                    current_task.finished_at = datetime.now(UTC)
                    current_task.error_message = "Назначен повторный расчёт по свежему снимку 1С."
                    case.current_task_id = None
                    case.current_agent_id = None
                else:
                    return False
            case.current_task_id = None

        run_key = f"role:{case.id}:{completion_key}"[:255]
        if metadata.get("quality_stage"):
            quality_stage = str(metadata.get("quality_stage"))
        elif case.status.startswith("quality_") or case.status in {
            "nonconformity",
            "isolated",
            "rework",
            "reinspection",
        }:
            quality_stage = case.status
        else:
            quality_stage = None
        role_context = {
            "case_number": case.source_number or str(case.id),
            "source_database": case.source_database,
            "source_date": case.source_date.isoformat() if case.source_date else None,
            "source_status": case.source_status,
            "source_data_version": case.source_data_version,
            "source_synced_at": (
                case.source_synced_at.isoformat() if case.source_synced_at else None
            ),
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
            "required_date": (
                case.required_date.isoformat() if case.required_date else None
            ),
            "source_basis_1c_ref": metadata.get("source_basis_1c_ref"),
            "source_basis_type": metadata.get("source_basis_type"),
            "source_basis_number": metadata.get("source_basis_number"),
            "source_basis_date": metadata.get("source_basis_date"),
            "source_basis_status": metadata.get("source_basis_status"),
            "production_order_1c_ref": metadata.get("production_order_1c_ref"),
            "production_order_type": metadata.get("production_order_type"),
        }
        if quality_stage:
            role_context["quality_stage"] = quality_stage
            role_context.update(dict(metadata.get("quality_context") or {}))

        task = Task(
            id=uuid.uuid4(),
            title=f"{agent_label(agent_id)}: {case.source_number or case.source_1c_ref}",
            description=f"Ролевая обработка: {case.source_type}",
            status=TaskStatus.PENDING,
            task_type="procurement_role_agent",
            input_payload={
                "correlation_id": case.correlation_id,
                "case_id": str(case.id),
                "source_type": case.source_type,
                "source_1c_ref": case.source_1c_ref,
                "source_number": case.source_number,
                "caller_agent_id": "procurement_orchestrator",
                "idempotency_key": run_key,
                "source_data": self._role_source_data(case),
                "role_context": role_context,
            },
            task_metadata={
                "procurement_case_id": str(case.id),
                "source_type": case.source_type,
                "agent_slug": agent_id,
                "role_completion_key": completion_key,
                "dispatch_requested_at": datetime.now(UTC).isoformat(),
            },
        )
        self.db.add(task)
        await self.db.flush()
        case.current_task_id = task.id
        case.current_agent_id = agent_id
        coverage = (
            (case.case_metadata or {}).get("supplier_order_coverage")
            if isinstance(
                (case.case_metadata or {}).get("supplier_order_coverage"), dict
            )
            else {}
        )
        if (
            agent_id
            in {WAREHOUSE_PICKER_AGENT_ID, WAREHOUSE_COMPLEX_CHIEF_AGENT_ID}
            and coverage.get("coverage_status") == "partial"
        ):
            case.assigned_agents = [agent_id, PURCHASE_MANAGER_AGENT_ID]
        else:
            case.assigned_agents = [agent_id]
        if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
            metadata = dict(case.case_metadata or {})
            metadata.setdefault("engineer_invoked_at", datetime.now(UTC).isoformat())
            metadata["engineer_workspace_status"] = "processing"
            for key in (
                "engineer_workspace_archived_at",
                "engineer_archived_bucket",
                "engineer_decision_kind",
                "engineer_action_at",
                "engineer_action_by",
                "engineer_critical_acknowledged_at",
                "engineer_critical_acknowledged_by",
            ):
                metadata.pop(key, None)
            case.case_metadata = metadata
        if agent_id == PRODUCTION_DISPATCHER_AGENT_ID:
            metadata = dict(case.case_metadata or {})
            metadata.setdefault("dispatcher_invoked_at", datetime.now(UTC).isoformat())
            metadata["dispatcher_workspace_status"] = "processing"
            for key in (
                "dispatcher_workspace_archived_at",
                "dispatcher_archived_bucket",
                "dispatcher_decision_kind",
                "dispatcher_action_at",
                "dispatcher_action_by",
                "dispatcher_critical_acknowledged_at",
                "dispatcher_critical_acknowledged_by",
                "dispatcher_confirmed_method",
            ):
                metadata.pop(key, None)
            case.case_metadata = metadata
        availability_spec = warehouse_availability_spec(agent_id)
        if availability_spec is not None:
            metadata = dict(case.case_metadata or {})
            metadata.setdefault(
                availability_spec.key("invoked_at"), datetime.now(UTC).isoformat()
            )
            metadata[availability_spec.key("workspace_status")] = "processing"
            clear_workspace_action_keys(metadata, availability_spec)
            case.case_metadata = metadata
            # Маршрут оркестратора: проверка наличия на этапе обеспечения.
            if case.control_point in {None, "", "basis", "data"}:
                case.control_point = "coverage"
        if agent_id == PURCHASE_MANAGER_AGENT_ID:
            metadata = dict(case.case_metadata or {})
            metadata.setdefault("purchase_manager_invoked_at", datetime.now(UTC).isoformat())
            metadata["purchase_manager_workspace_status"] = "processing"
            metadata.pop("purchase_manager_workspace_archived_at", None)
            case.case_metadata = metadata
            if case.control_point in {None, "", "basis", "data", "coverage"}:
                case.control_point = "purchase"
        await self._append_event(
            case,
            event_type="role_agent_task_enqueued",
            idempotency_key=f"{run_key}:enqueued"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={
                "task_id": str(task.id),
                "agent_id": agent_id,
                "agent_label": agent_label(agent_id),
                "source_type": case.source_type,
            },
        )
        await self.db.flush()

        if agent_id != PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
            self.pending_dispatches.append((str(case.id), str(task.id)))
        return True

    async def claim_engineer_dispatches(self, *, limit: int = 1) -> int:
        """Claim queued engineer calculations without exceeding five active slots."""
        tasks = (
            await self.db.execute(
                select(Task)
                .where(
                    Task.task_type == "procurement_role_agent",
                    Task.status.in_(
                        [TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.RUNNING]
                    ),
                )
                .order_by(Task.created_at, Task.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        engineer_tasks = [
            task
            for task in tasks
            if (task.task_metadata or {}).get("agent_slug")
            == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
        ]
        active = sum(
            1
            for task in engineer_tasks
            if task.status in {TaskStatus.PLANNING, TaskStatus.RUNNING}
            or bool((task.task_metadata or {}).get("dispatch_claimed"))
        )
        available = max(0, min(limit, 5 - active))
        claimed = 0
        for task in engineer_tasks:
            if claimed >= available:
                break
            metadata = dict(task.task_metadata or {})
            if task.status != TaskStatus.PENDING or metadata.get("dispatch_claimed"):
                continue
            metadata["dispatch_claimed"] = True
            metadata["dispatch_claimed_at"] = datetime.now(UTC).isoformat()
            task.task_metadata = metadata
            case_id = str(metadata.get("procurement_case_id") or "")
            if case_id:
                self.pending_dispatches.append((case_id, str(task.id)))
                claimed += 1
        if claimed:
            await self.db.flush()
        return claimed

    async def ensure_active_case_assignments(self) -> int:
        if not self.enqueue_case:
            return 0
        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(ProcurementCase.status.in_(list(ACTIVE_CASE_STATUSES)))
            )
        ).scalars().all()
        enqueued = 0
        for case in cases:
            if await self._enqueue_role_agent(case):
                enqueued += 1
        return enqueued

    async def ensure_picker_agent_work(self) -> dict[str, int]:
        """Синхронизировать статус комплектовщика и восстановить потерянный запуск."""
        return await self._ensure_warehouse_availability_work(PICKER_SPEC)

    async def ensure_complex_chief_agent_work(self) -> dict[str, int]:
        """Синхронизировать начальника складского комплекса и перенести legacy-кейсы."""
        result = await self._ensure_warehouse_availability_work(COMPLEX_CHIEF_SPEC)
        migrated = await self._migrate_undecided_engineer_cases_to_complex_chief()
        result["migrated"] = migrated
        return result

    async def ensure_purchase_manager_work(self) -> dict[str, int]:
        """Каждые ~30 мин: вернуть менеджеру кейсы, уже покрытые заказами поставщику.

        partial — менеджер в assigned_agents параллельно с picker/chief;
        full — current_agent = purchase_manager, при необходимости перезапуск задачи.
        """
        result = {"reported": 0, "enqueued": 0, "redispatched": 0, "assigned": 0}
        if not self.enqueue_case:
            return result

        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(
                    ProcurementCase.status.in_(
                        list(ORCHESTRATOR_PROCESSING_CASE_STATUSES)
                    ),
                    ProcurementCase.source_type
                    == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
                    ProcurementCase.closed_at.is_(None),
                )
            )
        ).scalars().all()
        now = datetime.now(UTC)

        for case in cases:
            metadata = dict(case.case_metadata or {})
            coverage = (
                metadata.get("supplier_order_coverage")
                if isinstance(metadata.get("supplier_order_coverage"), dict)
                else {}
            )
            coverage_status = str(coverage.get("coverage_status") or "")
            if coverage_status not in {"partial", "full"}:
                continue
            if metadata.get("purchase_manager_workspace_archived_at"):
                continue

            assigned = list(dict.fromkeys(case.assigned_agents or []))
            if PURCHASE_MANAGER_AGENT_ID not in assigned:
                assigned.append(PURCHASE_MANAGER_AGENT_ID)
                result["assigned"] += 1
            case.assigned_agents = assigned

            metadata.setdefault("purchase_manager_invoked_at", now.isoformat())
            if metadata.get("purchase_manager_workspace_status") != "processing":
                metadata["purchase_manager_workspace_status"] = "awaiting_action"
            metadata.pop("purchase_manager_workspace_archived_at", None)
            metadata["purchase_manager_output"] = coverage
            manager_ws = dict(metadata.get("procurement_manager") or {})
            manager_ws.setdefault("lifecycle_state", "handoff_received")
            manager_ws.setdefault("handoff_received_at", now.isoformat())
            manager_ws.setdefault("payment_document_draft", None)
            manager_ws.setdefault("recommendation_audit", [])
            manager_ws.setdefault("purchase_order_drafts", [])
            metadata["procurement_manager"] = manager_ws
            metadata["purchase_manager_last_status_reported_at"] = now.isoformat()
            metadata["purchase_manager_last_reported_status"] = coverage_status
            case.case_metadata = metadata
            result["reported"] += 1

            # Partial: picker/chief remain current — do not steal the task.
            if coverage_status != "full":
                continue

            if case.current_agent_id != PURCHASE_MANAGER_AGENT_ID:
                case.current_agent_id = PURCHASE_MANAGER_AGENT_ID
                if case.control_point in {None, "", "basis", "data", "coverage"}:
                    case.control_point = "purchase"

            current_task = (
                await self.db.get(Task, case.current_task_id)
                if case.current_task_id
                else None
            )
            pm_task = (
                current_task
                if current_task is not None
                and (current_task.task_metadata or {}).get("agent_slug")
                == PURCHASE_MANAGER_AGENT_ID
                else None
            )
            if (
                current_task is not None
                and pm_task is None
                and current_task.status
                in {
                    TaskStatus.PENDING,
                    TaskStatus.WAITING_HUMAN,
                    TaskStatus.WAITING_EXTERNAL,
                    TaskStatus.FAILED,
                }
            ):
                current_task.status = TaskStatus.CANCELLED
                current_task.finished_at = now
                current_task.error_message = (
                    "Кейс передан ИИ-агенту менеджера по закупкам: "
                    "все позиции уже в заказах поставщику."
                )
                case.current_task_id = None
                current_task = None
                metadata.pop("role_agent_completion_key", None)
                case.case_metadata = metadata

            if pm_task is not None and pm_task.status == TaskStatus.PENDING:
                task_metadata = dict(pm_task.task_metadata or {})
                dispatch_requested_at = task_metadata.get("dispatch_requested_at")
                try:
                    last_dispatch = (
                        datetime.fromisoformat(str(dispatch_requested_at))
                        if dispatch_requested_at
                        else None
                    )
                except ValueError:
                    last_dispatch = None
                stale_before = now - timedelta(
                    seconds=settings.PROCUREMENT_SUPPLIER_RECONCILIATION_INTERVAL_SECONDS
                )
                if last_dispatch is None or last_dispatch <= stale_before:
                    dispatch = (str(case.id), str(pm_task.id))
                    if dispatch not in self.pending_dispatches:
                        self.pending_dispatches.append(dispatch)
                        result["redispatched"] += 1
                    task_metadata["dispatch_requested_at"] = now.isoformat()
                    pm_task.task_metadata = task_metadata
                    metadata["purchase_manager_workspace_status"] = "processing"
                    case.case_metadata = metadata
                continue

            if pm_task is not None and pm_task.status in {
                TaskStatus.PLANNING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_HUMAN,
                TaskStatus.WAITING_EXTERNAL,
            }:
                continue

            can_enqueue = current_task is None or (
                pm_task is not None
                and pm_task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}
            )
            if can_enqueue and await self._enqueue_role_agent(case):
                result["enqueued"] += 1

        await self.db.flush()
        return result

    async def _ensure_procurement_manager_agent_running(
        self,
        case: ProcurementCase,
    ) -> None:
        """Start rich manager agent_run when handoff already pointed at purchase manager."""
        metadata = dict(case.case_metadata or {})
        manager_ws = dict(metadata.get("procurement_manager") or {})
        manager_ws["lifecycle_state"] = manager_ws.get("lifecycle_state") or "agent_running"
        metadata["procurement_manager"] = manager_ws
        case.case_metadata = metadata

        if manager_ws.get("agent_stage") or manager_ws.get("agent_run_idempotency_key"):
            return

        from app.agents.procurement_manager_agent.schemas import AgentRunRequest
        from app.agents.procurement_manager_agent.service import ProcurementManagerService

        try:
            await ProcurementManagerService(self.db).agent_run(
                case.id,
                AgentRunRequest(
                    idempotency_key=f"orchestrator-agent-run:{case.id}"[:255],
                    allow_web_fallback=True,
                ),
            )
        except Exception:  # noqa: BLE001 — handoff must remain successful
            manager_ws = dict((case.case_metadata or {}).get("procurement_manager") or {})
            manager_ws["lifecycle_state"] = "agent_running"
            metadata = dict(case.case_metadata or {})
            metadata["procurement_manager"] = manager_ws
            case.case_metadata = metadata

    async def _migrate_undecided_engineer_cases_to_complex_chief(self) -> int:
        """Перевести активные кейсы инженера без принятого решения на нового агента."""
        if not self.enqueue_case:
            return 0
        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(
                    ProcurementCase.status.in_(
                        list(ORCHESTRATOR_PROCESSING_CASE_STATUSES)
                    ),
                    ProcurementCase.source_type
                    == ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
                )
            )
        ).scalars().all()
        migrated = 0
        now = datetime.now(UTC)
        for case in cases:
            if is_montage_section_2_department(case.department_name):
                continue
            metadata = dict(case.case_metadata or {})
            if metadata.get("complex_invoked_at") or metadata.get(
                "complex_workspace_archived_at"
            ):
                continue
            engineer_assigned = (
                case.current_agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
                or PRODUCTION_PREPARATION_ENGINEER_AGENT_ID in (case.assigned_agents or [])
                or bool(metadata.get("engineer_invoked_at"))
            )
            if not engineer_assigned:
                continue
            # Не трогаем завершённые/ожидающие подтверждения результаты инженера.
            if (
                metadata.get("engineer_workspace_archived_at")
                or metadata.get("engineer_action_at")
                or metadata.get("engineer_critical_acknowledged_at")
                or (
                    metadata.get("engineer_workspace_status") == "awaiting_action"
                    and metadata.get("engineer_decision_kind")
                )
            ):
                continue
            current_task = (
                await self.db.get(Task, case.current_task_id)
                if case.current_task_id
                else None
            )
            if current_task is not None and (
                current_task.task_metadata or {}
            ).get("agent_slug") == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
                if current_task.status in {
                    TaskStatus.WAITING_HUMAN,
                    TaskStatus.WAITING_EXTERNAL,
                    TaskStatus.PLANNING,
                    TaskStatus.RUNNING,
                    TaskStatus.PENDING,
                    TaskStatus.FAILED,
                }:
                    current_task.status = TaskStatus.CANCELLED
                    current_task.finished_at = now
                    current_task.error_message = (
                        "Кейс передан ИИ-агенту начальника складского комплекса."
                    )
                case.current_task_id = None
            case.current_agent_id = None
            assigned = [
                agent
                for agent in (case.assigned_agents or [])
                if agent != PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
            ]
            case.assigned_agents = assigned
            metadata["complex_migrated_from_engineer_at"] = now.isoformat()
            metadata.pop("role_agent_completion_key", None)
            case.case_metadata = metadata
            await self._append_event(
                case,
                event_type="complex_migrated_from_engineer",
                idempotency_key=f"complex-migrated-from-engineer:{case.id}"[:255],
                previous_status=case.status,
                new_status=case.status,
                payload={"migrated_at": now.isoformat()},
                agent_id=WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
            )
            if await self._enqueue_role_agent(case):
                migrated += 1
        await self.db.flush()
        return migrated

    async def _ensure_warehouse_availability_work(
        self,
        spec: WarehouseAvailabilitySpec,
    ) -> dict[str, int]:
        """Синхронизировать статус агента наличия и восстановить потерянный запуск."""
        result = {"reported": 0, "enqueued": 0, "redispatched": 0}
        if not self.enqueue_case:
            return result

        cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(
                    ProcurementCase.status.in_(
                        list(ORCHESTRATOR_PROCESSING_CASE_STATUSES)
                    )
                )
            )
        ).scalars().all()
        now = datetime.now(UTC)

        for case in cases:
            metadata = dict(case.case_metadata or {})
            assigned = (
                case.current_agent_id == spec.agent_id
                or spec.agent_id in (case.assigned_agents or [])
            )
            if (
                not assigned
                or metadata.get(spec.key("workspace_archived_at"))
                or metadata.get(spec.key("workspace_status")) == "archived"
            ):
                continue

            agent_output = metadata.get(spec.output_key)
            current_task = (
                await self.db.get(Task, case.current_task_id)
                if case.current_task_id
                else None
            )
            agent_task = (
                current_task
                if current_task is not None
                and (current_task.task_metadata or {}).get("agent_slug")
                == spec.agent_id
                else None
            )

            reported_status = str(
                metadata.get(spec.key("workspace_status"))
                or (
                    agent_task.status.value
                    if agent_task is not None
                    else "assigned"
                )
            )
            metadata[spec.key("last_status_reported_at")] = now.isoformat()
            metadata[spec.key("last_reported_status")] = reported_status
            case.case_metadata = metadata
            result["reported"] += 1

            if agent_task is not None and agent_task.status == TaskStatus.PENDING:
                task_metadata = dict(agent_task.task_metadata or {})
                dispatch_requested_at = task_metadata.get("dispatch_requested_at")
                try:
                    last_dispatch = (
                        datetime.fromisoformat(str(dispatch_requested_at))
                        if dispatch_requested_at
                        else None
                    )
                except ValueError:
                    last_dispatch = None
                stale_before = now - timedelta(
                    seconds=settings.PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS
                )
                if last_dispatch is None or last_dispatch <= stale_before:
                    dispatch = (str(case.id), str(agent_task.id))
                    if dispatch not in self.pending_dispatches:
                        self.pending_dispatches.append(dispatch)
                        result["redispatched"] += 1
                    task_metadata["dispatch_requested_at"] = now.isoformat()
                    agent_task.task_metadata = task_metadata
                    metadata[spec.key("workspace_status")] = "processing"
                    case.case_metadata = metadata
                continue

            if agent_task is not None and agent_task.status in {
                TaskStatus.PLANNING,
                TaskStatus.RUNNING,
            }:
                continue

            if agent_output:
                continue

            if agent_task is not None and agent_task.status in {
                TaskStatus.WAITING_HUMAN,
                TaskStatus.WAITING_EXTERNAL,
                TaskStatus.FAILED,
            }:
                agent_task.status = TaskStatus.CANCELLED
                agent_task.finished_at = now
                agent_task.error_message = (
                    f"Оркестратор повторно запустил {agent_label(spec.agent_id)}: "
                    "предыдущая задача не передала результат."
                )
                case.current_task_id = None
                case.current_agent_id = None
                metadata.pop("role_agent_completion_key", None)
                case.case_metadata = metadata

            can_enqueue = current_task is None or (
                agent_task is not None
                and agent_task.status
                in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            )
            if can_enqueue and await self._enqueue_role_agent(case):
                result["enqueued"] += 1

        await self.db.flush()
        return result

    async def _apply_role_agent_result(
        self,
        case: ProcurementCase,
        task: Task,
        result_payload: dict[str, Any],
        *,
        event_type: str,
    ) -> dict[str, Any]:
        role_status = str(
            result_payload.get("role_status")
            or result_payload.get("status")
            or "failed"
        )
        if role_status not in {
            "waiting_human",
            "waiting_external",
            "completed",
            "failed",
        }:
            raise ValueError(f"Неизвестный статус ролевого агента: {role_status}")

        result_payload["role_status"] = role_status
        result_payload.setdefault("agent_id", case.current_agent_id)
        result_payload.setdefault("case_id", str(case.id))
        result_payload.setdefault("correlation_id", case.correlation_id)
        case.latest_result = result_payload
        task_metadata = dict(task.task_metadata or {})
        task_metadata["dispatch_claimed"] = False
        task.task_metadata = task_metadata
        output_data = result_payload.get("output_data") or {}
        if (
            result_payload.get("agent_id")
            == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
            and isinstance(output_data, dict)
        ):
            metadata = dict(case.case_metadata or {})
            metadata["production_preparation_engineer_output"] = output_data
            metadata["engineer_evidence_fingerprint"] = output_data.get(
                "evidence_fingerprint"
            )
            metadata["engineer_calculated_at"] = output_data.get("calculated_at")
            metadata["engineer_decision_kind"] = output_data.get("decision_kind")
            case.case_metadata = metadata
        if (
            result_payload.get("agent_id") == PRODUCTION_DISPATCHER_AGENT_ID
            and isinstance(output_data, dict)
        ):
            metadata = dict(case.case_metadata or {})
            metadata["production_dispatcher_output"] = output_data
            metadata["dispatcher_evidence_fingerprint"] = output_data.get(
                "evidence_fingerprint"
            )
            metadata["dispatcher_calculated_at"] = output_data.get("calculated_at")
            metadata["dispatcher_decision_kind"] = output_data.get("decision_kind")
            case.case_metadata = metadata
        availability_result_spec = warehouse_availability_spec(
            str(result_payload.get("agent_id") or "")
        )
        if availability_result_spec is not None and isinstance(output_data, dict):
            metadata = dict(case.case_metadata or {})
            metadata[availability_result_spec.output_key] = output_data
            metadata[availability_result_spec.key("evidence_fingerprint")] = (
                output_data.get("evidence_fingerprint")
            )
            metadata[availability_result_spec.key("calculated_at")] = output_data.get(
                "calculated_at"
            )
            metadata[availability_result_spec.key("decision_kind")] = output_data.get(
                "decision_kind"
            )
            case.case_metadata = metadata
        if (
            result_payload.get("agent_id") == PURCHASE_MANAGER_AGENT_ID
            and isinstance(output_data, dict)
        ):
            metadata = dict(case.case_metadata or {})
            metadata["purchase_manager_output"] = output_data
            metadata["purchase_manager_workspace_status"] = "awaiting_action"
            case.case_metadata = metadata
        result_agent_id = str(result_payload.get("agent_id") or case.current_agent_id or "")
        if result_agent_id in QUALITY_ROLE_AGENT_IDS and isinstance(output_data, dict):
            metadata = dict(case.case_metadata or {})
            metadata[f"{result_agent_id}_output"] = output_data
            metadata["quality_calculated_at"] = output_data.get("calculated_at")
            if output_data.get("next_agent"):
                metadata["next_quality_agent"] = output_data.get("next_agent")
            elif role_status == "completed":
                metadata.pop("next_quality_agent", None)
            if output_data.get("next_status"):
                metadata["quality_stage"] = output_data.get("next_status")
            case.case_metadata = metadata
        if (
            result_agent_id == OMTO_SUPPORT_MANAGER_AGENT_ID
            and isinstance(output_data, dict)
        ):
            metadata = dict(case.case_metadata or {})
            metadata["omto_support_manager_output"] = output_data
            metadata["omto_calculated_at"] = output_data.get("calculated_at")
            case.case_metadata = metadata
        task.final_result = result_payload
        task.requires_human_review = role_status == "waiting_human"
        wait_reason = str(
            result_payload.get("wait_reason")
            or result_payload.get("summary")
            or ""
        ) or None

        previous_status = case.status
        if role_status == "waiting_human":
            task.status = TaskStatus.WAITING_HUMAN
            task.finished_at = None
            next_quality_status = (
                output_data.get("next_status")
                if isinstance(output_data, dict)
                else None
            )
            if (
                result_agent_id in QUALITY_ROLE_AGENT_IDS
                and next_quality_status
                and str(next_quality_status)
                in {
                    s.value for s in ProcurementCaseStatus
                }
            ):
                case.status = str(next_quality_status)
            else:
                case.status = ProcurementCaseStatus.AGENT_WAITING.value
            case.deviation_summary = wait_reason
            if result_payload.get("agent_id") == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
                metadata = dict(case.case_metadata or {})
                metadata["engineer_workspace_status"] = "awaiting_action"
                case.case_metadata = metadata
            if result_payload.get("agent_id") == PRODUCTION_DISPATCHER_AGENT_ID:
                metadata = dict(case.case_metadata or {})
                metadata["dispatcher_workspace_status"] = "awaiting_action"
                case.case_metadata = metadata
            if warehouse_availability_spec(
                str(result_payload.get("agent_id") or "")
            ) is not None:
                waiting_spec = warehouse_availability_spec(
                    str(result_payload.get("agent_id") or "")
                )
                assert waiting_spec is not None
                metadata = dict(case.case_metadata or {})
                metadata[waiting_spec.key("workspace_status")] = "awaiting_action"
                case.case_metadata = metadata
        elif role_status == "waiting_external":
            task.status = TaskStatus.WAITING_EXTERNAL
            task.finished_at = None
            case.status = (
                ProcurementCaseStatus.ORDERED.value
                if result_payload.get("agent_id") == PURCHASE_MANAGER_AGENT_ID
                else ProcurementCaseStatus.AGENT_WAITING.value
            )
            case.deviation_summary = wait_reason
        elif role_status == "completed":
            task.status = TaskStatus.COMPLETED
            task.finished_at = datetime.now(UTC)
            next_quality_status = (
                output_data.get("next_status")
                if isinstance(output_data, dict)
                else None
            )
            if (
                result_agent_id in QUALITY_ROLE_AGENT_IDS
                and next_quality_status
                and str(next_quality_status)
                in {s.value for s in ProcurementCaseStatus}
            ):
                case.status = str(next_quality_status)
            else:
                case.status = ProcurementCaseStatus.NEW.value
            if (
                result_agent_id in QUALITY_ROLE_AGENT_IDS
                and str(next_quality_status or "")
                == ProcurementCaseStatus.QUALITY_RELEASED.value
            ):
                case.status = ProcurementCaseStatus.CLOSED.value
                case.closed_at = datetime.now(UTC)
                case.closed_reason = "quality_released"
            case.deviation_summary = None
            metadata = dict(case.case_metadata or {})
            metadata["role_agent_completion_key"] = (task.task_metadata or {}).get(
                "role_completion_key"
            )
            metadata["role_agent_output"] = result_payload.get("output_data") or {}
            case.case_metadata = metadata
            if result_payload.get("agent_id") == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
                case.control_point = "coverage"
                positions = output_data.get("positions") if isinstance(output_data, dict) else []
                has_deficit = any(
                    float(item.get("net_requirement") or 0) > 0
                    for item in positions or []
                    if isinstance(item, dict)
                )
                case.requested_operation = (
                    "route_confirmed_deficit" if has_deficit else "coverage_confirmed"
                )
            if result_payload.get("agent_id") == PRODUCTION_DISPATCHER_AGENT_ID:
                case.control_point = "coverage"
                case.requested_operation = "dispatcher_confirmed"
                metadata = dict(case.case_metadata or {})
                archived_at = datetime.now(UTC)
                decision = str(output_data.get("decision_kind") or "none")
                archived_bucket = (
                    "critical"
                    if decision == "critical_acknowledgement"
                    else "attention"
                    if decision == "supply_confirmation"
                    else "success"
                )
                metadata["dispatcher_workspace_status"] = "archived"
                metadata["dispatcher_workspace_archived_at"] = archived_at.isoformat()
                metadata["dispatcher_archived_bucket"] = archived_bucket
                case.case_metadata = metadata
            case.current_task_id = None
            case.current_agent_id = None
            if result_payload.get("agent_id") == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID:
                metadata = dict(case.case_metadata or {})
                archived_bucket = "attention" if has_deficit else "success"
                archived_at = datetime.now(UTC)
                metadata["engineer_workspace_status"] = "archived"
                metadata["engineer_workspace_archived_at"] = archived_at.isoformat()
                metadata["engineer_archived_bucket"] = archived_bucket
                metadata["engineer_handoff_agent_id"] = PRODUCTION_DISPATCHER_AGENT_ID
                case.case_metadata = metadata
                case.control_point = "chief_dispatcher"
                case.requested_operation = "route_to_chief_dispatcher"
                case.current_agent_id = PRODUCTION_DISPATCHER_AGENT_ID
                case.assigned_agents = [PRODUCTION_DISPATCHER_AGENT_ID]
                await self._append_event(
                    case,
                    event_type="engineer_handoff_to_chief_dispatcher",
                    idempotency_key=f"engineer-handoff:{case.id}:{task.id}"[:255],
                    previous_status=previous_status,
                    new_status=case.status,
                    payload={
                        "engineer_bucket": archived_bucket,
                        "next_agent_id": PRODUCTION_DISPATCHER_AGENT_ID,
                        "archived_at": archived_at.isoformat(),
                    },
                    agent_id=PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
                )
                previous_enqueue = self.enqueue_case
                self.enqueue_case = True
                try:
                    await self._enqueue_role_agent(case)
                finally:
                    self.enqueue_case = previous_enqueue
            completed_availability_spec = warehouse_availability_spec(
                str(result_payload.get("agent_id") or "")
            )
            if completed_availability_spec is not None:
                metadata = dict(case.case_metadata or {})
                decision = str(
                    (output_data.get("decision_kind") if isinstance(output_data, dict) else None)
                    or metadata.get(completed_availability_spec.key("decision_kind"))
                    or "none"
                )
                archived_bucket = (
                    "critical"
                    if decision == "critical_acknowledgement"
                    else "attention"
                    if decision
                    in {
                        "stock_confirmation",
                        "deficit_confirmation",
                        "discrepancy_return",
                    }
                    else "success"
                )
                archived_at = datetime.now(UTC)
                metadata[completed_availability_spec.key("workspace_status")] = "archived"
                metadata[completed_availability_spec.key("workspace_archived_at")] = (
                    archived_at.isoformat()
                )
                metadata[completed_availability_spec.key("archived_bucket")] = (
                    archived_bucket
                )
                metadata[completed_availability_spec.key("handoff_agent_id")] = (
                    completed_availability_spec.handoff_agent_id
                )
                case.case_metadata = metadata
                case.control_point = "omto"
                case.requested_operation = "route_to_omto_chief"
                case.current_agent_id = completed_availability_spec.handoff_agent_id
                case.assigned_agents = [completed_availability_spec.handoff_agent_id]
                await self._append_event(
                    case,
                    event_type=completed_availability_spec.handoff_event,
                    idempotency_key=(
                        f"{completed_availability_spec.prefix}-handoff:"
                        f"{case.id}:{task.id}"
                    )[:255],
                    previous_status=previous_status,
                    new_status=case.status,
                    payload={
                        f"{completed_availability_spec.prefix}_bucket": archived_bucket,
                        "next_agent_id": completed_availability_spec.handoff_agent_id,
                        "archived_at": archived_at.isoformat(),
                        "conclusion": (
                            output_data.get("conclusion")
                            if isinstance(output_data, dict)
                            else {}
                        ),
                    },
                    agent_id=completed_availability_spec.agent_id,
                )
                previous_enqueue = self.enqueue_case
                self.enqueue_case = True
                try:
                    await self._enqueue_role_agent(case)
                finally:
                    self.enqueue_case = previous_enqueue
        else:
            task.status = TaskStatus.FAILED
            task.finished_at = datetime.now(UTC)
            task.error_message = wait_reason or "Ролевой агент завершился с ошибкой."
            case.status = ProcurementCaseStatus.BLOCKED.value
            case.deviation_summary = task.error_message

        event_hash = hashlib.sha256(
            repr(sorted(result_payload.items())).encode("utf-8")
        ).hexdigest()[:20]
        await self._append_event(
            case,
            event_type=event_type,
            idempotency_key=f"role-result:{task.id}:{role_status}:{event_hash}",
            previous_status=previous_status,
            new_status=case.status,
            agent_id=str(result_payload.get("agent_id") or "") or None,
            payload={
                "task_id": str(task.id),
                "agent_id": result_payload.get("agent_id"),
                "role_status": role_status,
                "wait_reason": wait_reason,
                "output_data": result_payload.get("output_data") or {},
            },
        )
        await self.db.flush()
        return result_payload

    async def execute_case_task(self, case_id: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        task = await self.db.get(Task, task_id)
        if case is None or task is None:
            return {"status": "failed", "error": "case_or_task_not_found"}
        if case.current_task_id != task.id:
            return {"status": "failed", "error": "task_is_not_current_for_case"}

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)
        await self.db.flush()

        payload = dict(task.input_payload or {})
        payload["task_id"] = str(task.id)
        payload["case_id"] = str(case.id)
        payload["db"] = self.db

        from app.agents import agent_registry

        agent_id = str((task.task_metadata or {}).get("agent_slug") or "")
        agent_cls = agent_registry.get(agent_id)
        if agent_cls is None:
            raise ValueError(f"Ролевой агент {agent_id!r} не зарегистрирован")
        result = await agent_cls().run(payload)
        result_payload = (
            result.model_dump(mode="json")
            if hasattr(result, "model_dump")
            else dict(result)
        )
        return await self._apply_role_agent_result(
            case,
            task,
            result_payload,
            event_type="role_agent_result_received",
        )

    async def resume_case_agent(
        self,
        case_id: uuid.UUID,
        result_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        case = await self.db.get(ProcurementCase, case_id)
        if case is None or case.current_task_id is None:
            return None
        task = await self.db.get(Task, case.current_task_id)
        if task is None or task.status not in {
            TaskStatus.WAITING_HUMAN,
            TaskStatus.WAITING_EXTERNAL,
            TaskStatus.FAILED,
        }:
            return None
        payload = dict(result_payload)
        payload.setdefault("agent_id", case.current_agent_id)
        return await self._apply_role_agent_result(
            case,
            task,
            payload,
            event_type="role_agent_resumed",
        )

    async def confirm_engineer_purchase(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if (
            metadata.get("engineer_workspace_status") == "archived"
            and metadata.get("engineer_archived_bucket") == "attention"
        ):
            return {
                "status": "completed",
                "action": "purchase_confirmed",
                "case_id": str(case.id),
            }
        if (
            metadata.get("engineer_decision_kind") != "purchase_confirmation"
            or case.current_task_id is None
        ):
            return None
        task = await self.db.get(Task, case.current_task_id)
        if task is None or task.status != TaskStatus.WAITING_HUMAN:
            return None
        action_at = datetime.now(UTC)
        metadata["engineer_action_at"] = action_at.isoformat()
        metadata["engineer_action_by"] = user_id
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type="engineer_purchase_confirmed",
            idempotency_key=f"engineer-purchase-confirmed:{task.id}"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={"user_id": user_id, "confirmed_at": action_at.isoformat()},
            agent_id=PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
        )
        payload = dict(task.final_result or {})
        payload.update(
            {
                "role_status": "completed",
                "status": "completed",
                "summary": "Закупка рассчитанного дефицита подтверждена инженером.",
                "requires_human_review": False,
            }
        )
        payload.setdefault(
            "output_data",
            metadata.get("production_preparation_engineer_output") or {},
        )
        await self._apply_role_agent_result(
            case,
            task,
            payload,
            event_type="role_agent_resumed",
        )
        return {
            "status": "completed",
            "action": "purchase_confirmed",
            "case_id": str(case.id),
        }

    async def acknowledge_engineer_critical(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        case = await self.db.get(ProcurementCase, case_id)
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if metadata.get("engineer_decision_kind") != "critical_acknowledgement":
            return None
        if metadata.get("engineer_critical_acknowledged_at"):
            return {
                "status": "waiting_for_source_update",
                "action": "critical_acknowledged",
                "case_id": str(case.id),
            }
        acknowledged_at = datetime.now(UTC)
        metadata["engineer_critical_acknowledged_at"] = acknowledged_at.isoformat()
        metadata["engineer_critical_acknowledged_by"] = user_id
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type="engineer_critical_acknowledged",
            idempotency_key=f"engineer-critical-ack:{case.current_task_id or case.id}"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={"user_id": user_id, "acknowledged_at": acknowledged_at.isoformat()},
            agent_id=PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
        )
        await self.db.flush()
        return {
            "status": "waiting_for_source_update",
            "action": "critical_acknowledged",
            "case_id": str(case.id),
        }

    async def confirm_dispatcher_supply(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
        method: str | None = None,
    ) -> dict[str, Any] | None:
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if (
            metadata.get("dispatcher_workspace_status") == "archived"
            and metadata.get("dispatcher_archived_bucket") in {"attention", "success"}
        ):
            return {
                "status": "completed",
                "action": "supply_confirmed",
                "case_id": str(case.id),
            }
        if (
            metadata.get("dispatcher_decision_kind") != "supply_confirmation"
            or case.current_task_id is None
        ):
            return None
        task = await self.db.get(Task, case.current_task_id)
        if task is None or task.status != TaskStatus.WAITING_HUMAN:
            return None
        action_at = datetime.now(UTC)
        confirmed_method = method or "procurement"
        metadata["dispatcher_action_at"] = action_at.isoformat()
        metadata["dispatcher_action_by"] = user_id
        metadata["dispatcher_confirmed_method"] = confirmed_method
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type="dispatcher_supply_confirmed",
            idempotency_key=f"dispatcher-supply-confirmed:{task.id}"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={
                "user_id": user_id,
                "confirmed_at": action_at.isoformat(),
                "method": confirmed_method,
            },
            agent_id=PRODUCTION_DISPATCHER_AGENT_ID,
        )
        payload = dict(task.final_result or {})
        payload.update(
            {
                "role_status": "completed",
                "status": "completed",
                "summary": "Способ обеспечения подтверждён диспетчером производства.",
                "requires_human_review": False,
            }
        )
        payload.setdefault(
            "output_data",
            metadata.get("production_dispatcher_output") or {},
        )
        await self._apply_role_agent_result(
            case,
            task,
            payload,
            event_type="role_agent_resumed",
        )
        return {
            "status": "completed",
            "action": "supply_confirmed",
            "case_id": str(case.id),
        }

    async def acknowledge_dispatcher_critical(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        case = await self.db.get(ProcurementCase, case_id)
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if metadata.get("dispatcher_decision_kind") != "critical_acknowledgement":
            return None
        if metadata.get("dispatcher_critical_acknowledged_at"):
            return {
                "status": "waiting_for_source_update",
                "action": "critical_acknowledged",
                "case_id": str(case.id),
            }
        acknowledged_at = datetime.now(UTC)
        metadata["dispatcher_critical_acknowledged_at"] = acknowledged_at.isoformat()
        metadata["dispatcher_critical_acknowledged_by"] = user_id
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type="dispatcher_critical_acknowledged",
            idempotency_key=f"dispatcher-critical-ack:{case.current_task_id or case.id}"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={"user_id": user_id, "acknowledged_at": acknowledged_at.isoformat()},
            agent_id=PRODUCTION_DISPATCHER_AGENT_ID,
        )
        await self.db.flush()
        return {
            "status": "waiting_for_source_update",
            "action": "critical_acknowledged",
            "case_id": str(case.id),
        }

    async def confirm_picker_conclusion(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
        action: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.confirm_warehouse_availability_conclusion(
            case_id,
            user_id=user_id,
            action=action,
            agent_id=WAREHOUSE_PICKER_AGENT_ID,
        )

    async def confirm_complex_chief_conclusion(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
        action: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.confirm_warehouse_availability_conclusion(
            case_id,
            user_id=user_id,
            action=action,
            agent_id=WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
        )

    async def confirm_warehouse_availability_conclusion(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
        action: str | None = None,
        agent_id: str,
    ) -> dict[str, Any] | None:
        spec = warehouse_availability_spec(agent_id)
        if spec is None:
            return None
        case = await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if (
            metadata.get(spec.key("workspace_status")) == "archived"
            and metadata.get(spec.key("archived_bucket")) in {"attention", "success"}
        ):
            return {
                "status": "completed",
                "action": f"{spec.prefix}_confirmed",
                "case_id": str(case.id),
            }
        decision = metadata.get(spec.key("decision_kind"))
        if (
            decision
            not in {
                "stock_confirmation",
                "deficit_confirmation",
                "discrepancy_return",
            }
            or case.current_task_id is None
        ):
            return None
        task = await self.db.get(Task, case.current_task_id)
        if task is None or task.status != TaskStatus.WAITING_HUMAN:
            return None
        action_at = datetime.now(UTC)
        confirmed_action = action or {
            "stock_confirmation": "issue_from_stock",
            "deficit_confirmation": "confirm_deficit",
            "discrepancy_return": "return_discrepancy",
        }.get(str(decision), "confirm")
        metadata[spec.key("action_at")] = action_at.isoformat()
        metadata[spec.key("action_by")] = user_id
        metadata[spec.key("confirmed_action")] = confirmed_action
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type=spec.conclusion_event,
            idempotency_key=f"{spec.prefix}-conclusion-confirmed:{task.id}"[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={
                "user_id": user_id,
                "confirmed_at": action_at.isoformat(),
                "action": confirmed_action,
            },
            agent_id=spec.agent_id,
        )
        summary_by_action = {
            "issue_from_stock": f"Выдача из кладовой подтверждена {spec.actor_label}.",
            "partial_issue": (
                f"Частичная выдача и дефицит согласованы {spec.actor_label}."
            ),
            "confirm_deficit": f"Дефицит подтверждён {spec.actor_label}.",
            "return_discrepancy": "Кейс возвращён из-за расхождений учёта и факта.",
        }
        payload = dict(task.final_result or {})
        payload.update(
            {
                "role_status": "completed",
                "status": "completed",
                "summary": summary_by_action.get(
                    confirmed_action,
                    "Заключение по складскому наличию подтверждено.",
                ),
                "requires_human_review": False,
                "agent_id": spec.agent_id,
            }
        )
        payload.setdefault(
            "output_data",
            metadata.get(spec.output_key) or {},
        )
        await self._apply_role_agent_result(
            case,
            task,
            payload,
            event_type="role_agent_resumed",
        )
        return {
            "status": "completed",
            "action": f"{spec.prefix}_confirmed",
            "case_id": str(case.id),
        }

    async def acknowledge_picker_critical(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        return await self.acknowledge_warehouse_availability_critical(
            case_id,
            user_id=user_id,
            agent_id=WAREHOUSE_PICKER_AGENT_ID,
        )

    async def acknowledge_complex_chief_critical(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        return await self.acknowledge_warehouse_availability_critical(
            case_id,
            user_id=user_id,
            agent_id=WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
        )

    async def acknowledge_warehouse_availability_critical(
        self,
        case_id: uuid.UUID,
        *,
        user_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        spec = warehouse_availability_spec(agent_id)
        if spec is None:
            return None
        case = await self.db.get(ProcurementCase, case_id)
        if case is None:
            return None
        metadata = dict(case.case_metadata or {})
        if metadata.get(spec.key("decision_kind")) != "critical_acknowledgement":
            return None
        if metadata.get(spec.key("critical_acknowledged_at")):
            return {
                "status": "waiting_for_source_update",
                "action": "critical_acknowledged",
                "case_id": str(case.id),
            }
        acknowledged_at = datetime.now(UTC)
        metadata[spec.key("critical_acknowledged_at")] = acknowledged_at.isoformat()
        metadata[spec.key("critical_acknowledged_by")] = user_id
        case.case_metadata = metadata
        await self._append_event(
            case,
            event_type=spec.critical_event,
            idempotency_key=(
                f"{spec.prefix}-critical-ack:{case.current_task_id or case.id}"
            )[:255],
            previous_status=case.status,
            new_status=case.status,
            payload={"user_id": user_id, "acknowledged_at": acknowledged_at.isoformat()},
            agent_id=spec.agent_id,
        )
        await self.db.flush()
        return {
            "status": "waiting_for_source_update",
            "action": "critical_acknowledged",
            "case_id": str(case.id),
        }

    async def list_dashboard(
        self,
        *,
        view: str = "active",
        source_type: str | None = None,
        engineer_workspace: bool = False,
        dispatcher_workspace: bool = False,
        picker_workspace: bool = False,
        complex_workspace: bool = False,
        purchase_manager_workspace: bool = False,
    ) -> dict[str, Any]:
        normalized_view = view if view in {"active", "processing", "archive"} else "active"
        if (
            engineer_workspace
            or dispatcher_workspace
            or picker_workspace
            or complex_workspace
            or purchase_manager_workspace
        ):
            status_filter = None
        elif normalized_view == "archive":
            status_filter = list(TERMINAL_CASE_STATUSES)
        else:
            status_filter = list(ORCHESTRATOR_PROCESSING_CASE_STATUSES)

        case_filters = []
        if status_filter is not None:
            case_filters.append(ProcurementCase.status.in_(status_filter))
        if source_type:
            case_filters.append(ProcurementCase.source_type == source_type)
        loaded_cases = (
            await self.db.execute(
                select(ProcurementCase)
                .options(selectinload(ProcurementCase.positions))
                .where(*case_filters)
                .order_by(
                    ProcurementCase.source_date.desc().nullslast(),
                    ProcurementCase.source_number.desc().nullslast(),
                    ProcurementCase.updated_at.desc().nullslast(),
                )
            )
        ).scalars().all()
        if engineer_workspace:
            engineer_cases = [
                case
                for case in loaded_cases
                if not is_montage_section_2_department(case.department_name)
                and not (case.case_metadata or {}).get("complex_invoked_at")
                and (
                    (case.case_metadata or {}).get("engineer_invoked_at")
                    or (case.case_metadata or {}).get(
                        "production_preparation_engineer_output"
                    )
                )
            ]

            def is_engineer_archived(case: ProcurementCase) -> bool:
                metadata = case.case_metadata or {}
                return bool(metadata.get("engineer_workspace_archived_at")) or (
                    case.status in TERMINAL_CASE_STATUSES
                )

            active_engineer_cases = [
                case for case in engineer_cases if not is_engineer_archived(case)
            ]
            archived_engineer_cases = [
                case for case in engineer_cases if is_engineer_archived(case)
            ]
            cases = (
                archived_engineer_cases
                if normalized_view == "archive"
                else active_engineer_cases
            )
        elif dispatcher_workspace:
            dispatcher_cases = [
                case
                for case in loaded_cases
                if (case.case_metadata or {}).get("dispatcher_invoked_at")
                or (case.case_metadata or {}).get("production_dispatcher_output")
                or (
                    case.source_type == ProcurementSourceType.REORDER_POINT.value
                    and (
                        case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
                        or (case.case_metadata or {}).get("engineer_handoff_agent_id")
                        == PRODUCTION_DISPATCHER_AGENT_ID
                    )
                )
                or (
                    (case.case_metadata or {}).get("engineer_handoff_agent_id")
                    == PRODUCTION_DISPATCHER_AGENT_ID
                )
            ]

            def is_dispatcher_archived(case: ProcurementCase) -> bool:
                metadata = case.case_metadata or {}
                return bool(metadata.get("dispatcher_workspace_archived_at")) or (
                    case.status in TERMINAL_CASE_STATUSES
                )

            active_dispatcher_cases = [
                case for case in dispatcher_cases if not is_dispatcher_archived(case)
            ]
            archived_dispatcher_cases = [
                case for case in dispatcher_cases if is_dispatcher_archived(case)
            ]
            cases = (
                archived_dispatcher_cases
                if normalized_view == "archive"
                else active_dispatcher_cases
            )
        elif picker_workspace:
            picker_cases = [
                case
                for case in loaded_cases
                if is_warehouse_availability_case(case, PICKER_SPEC)
            ]

            def is_picker_archived(case: ProcurementCase) -> bool:
                metadata = case.case_metadata or {}
                return bool(metadata.get("picker_workspace_archived_at")) or (
                    case.status in TERMINAL_CASE_STATUSES
                )

            active_picker_cases = [
                case for case in picker_cases if not is_picker_archived(case)
            ]
            archived_picker_cases = [
                case for case in picker_cases if is_picker_archived(case)
            ]
            cases = (
                archived_picker_cases
                if normalized_view == "archive"
                else active_picker_cases
            )
        elif complex_workspace:
            complex_cases = [
                case
                for case in loaded_cases
                if is_warehouse_availability_case(case, COMPLEX_CHIEF_SPEC)
            ]

            def is_complex_archived(case: ProcurementCase) -> bool:
                metadata = case.case_metadata or {}
                return bool(metadata.get("complex_workspace_archived_at")) or (
                    case.status in TERMINAL_CASE_STATUSES
                )

            active_complex_cases = [
                case for case in complex_cases if not is_complex_archived(case)
            ]
            archived_complex_cases = [
                case for case in complex_cases if is_complex_archived(case)
            ]
            cases = (
                archived_complex_cases
                if normalized_view == "archive"
                else active_complex_cases
            )
        elif purchase_manager_workspace:
            def _coverage_status(case: ProcurementCase) -> str:
                coverage = (case.case_metadata or {}).get("supplier_order_coverage")
                if not isinstance(coverage, dict):
                    return ""
                return str(coverage.get("coverage_status") or "")

            manager_cases = [
                case
                for case in loaded_cases
                if _coverage_status(case) in {"partial", "full"}
                and (
                    (case.case_metadata or {}).get("purchase_manager_invoked_at")
                    or (case.case_metadata or {}).get("purchase_manager_output")
                    or case.current_agent_id == PURCHASE_MANAGER_AGENT_ID
                )
            ]

            def is_purchase_manager_archived(case: ProcurementCase) -> bool:
                metadata = case.case_metadata or {}
                return bool(metadata.get("purchase_manager_workspace_archived_at")) or (
                    case.status in TERMINAL_CASE_STATUSES
                )

            active_manager_cases = [
                case for case in manager_cases if not is_purchase_manager_archived(case)
            ]
            archived_manager_cases = [
                case for case in manager_cases if is_purchase_manager_archived(case)
            ]
            cases = (
                archived_manager_cases
                if normalized_view == "archive"
                else active_manager_cases
            )
        else:
            cases = loaded_cases
        if normalized_view == "processing":
            # Same active cards, but presented as processing cases.
            # Include ordered: закупка ещё идёт, кейс не должен пропадать из оркестратора.
            cases = [
                case
                for case in cases
                if case.status in ORCHESTRATOR_PROCESSING_CASE_STATUSES
            ]

        if engineer_workspace:
            archive_count = len(archived_engineer_cases)
            processing_count = len(active_engineer_cases)
        elif dispatcher_workspace:
            archive_count = len(archived_dispatcher_cases)
            processing_count = len(active_dispatcher_cases)
        elif picker_workspace:
            archive_count = len(archived_picker_cases)
            processing_count = len(active_picker_cases)
        elif complex_workspace:
            archive_count = len(archived_complex_cases)
            processing_count = len(active_complex_cases)
        elif purchase_manager_workspace:
            archive_count = len(archived_manager_cases)
            processing_count = len(active_manager_cases)
        else:
            archive_filters = [ProcurementCase.status.in_(list(TERMINAL_CASE_STATUSES))]
            processing_filters = [
                ProcurementCase.status.in_(list(ORCHESTRATOR_PROCESSING_CASE_STATUSES))
            ]
            if source_type:
                archive_filters.append(ProcurementCase.source_type == source_type)
                processing_filters.append(ProcurementCase.source_type == source_type)
            archive_count = await self.db.scalar(
                select(func.count()).select_from(ProcurementCase).where(*archive_filters)
            )
            processing_count = await self.db.scalar(
                select(func.count()).select_from(ProcurementCase).where(*processing_filters)
            )

        sync_states = (
            await self.db.execute(select(ProcurementSourceSyncState))
        ).scalars().all()
        groups = []
        for capability in list_source_capabilities():
            if source_type and capability.source_type.value != source_type:
                continue
            group_cases = [
                (
                    mirror_picker_fields_from_complex(self._serialize_case_summary(case))
                    if complex_workspace
                    else self._serialize_case_summary(case)
                )
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
        current_task = (
            await self.db.get(Task, case.current_task_id)
            if case.current_task_id
            else None
        )
        detail = self._serialize_case_detail(case)
        detail["events"] = await self.list_case_events(case_id)
        detail["route_stages"] = self._serialize_route_stages(case)
        detail["timeline"] = self._serialize_timeline(detail["events"], case)
        detail["current_state"] = {
            "status": case.status,
            "control_point": case.control_point,
            "current_agent_id": case.current_agent_id,
            "current_agent_label": (
                procurement_config.AGENT_NAME
                if case.current_agent_id == procurement_config.AGENT_ID
                else agent_label(case.current_agent_id)
            ),
            "requires_human_review": bool((case.latest_result or {}).get("requires_human_review")),
            "summary": (case.latest_result or {}).get("summary") or case.deviation_summary,
            "task_id": str(case.current_task_id) if case.current_task_id else None,
            "task_status": (
                current_task.status.value if current_task is not None else None
            ),
            "wait_status": (case.latest_result or {}).get("role_status"),
            "wait_reason": (case.latest_result or {}).get("wait_reason")
            or case.deviation_summary,
            "closed_reason": case.closed_reason,
            "closed_reason_label": CLOSED_REASON_LABELS.get(case.closed_reason or ""),
            "source_active": case.status in SOURCE_MONITORED_CASE_STATUSES,
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
        agent_id: str | None = None,
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
                agent_id=agent_id or case.current_agent_id or procurement_config.AGENT_ID,
                actor_role="procurement_orchestrator",
                previous_status=previous_status,
                new_status=new_status,
                idempotency_key=idempotency_key,
                payload=payload or {},
            )
        )

    @staticmethod
    def _engineer_bucket(case: ProcurementCase) -> tuple[str | None, str | None]:
        if case.source_type != ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value:
            return None, None
        if is_montage_section_2_department(case.department_name):
            return None, None
        result = case.latest_result or {}
        metadata = case.case_metadata or {}
        archived_bucket = metadata.get("engineer_archived_bucket")
        if archived_bucket in {"success", "attention", "critical"}:
            return str(archived_bucket), "Состояние сохранено при передаче оркестратору."
        output = result.get("output_data") or metadata.get(
            "production_preparation_engineer_output"
        )
        output = output if isinstance(output, dict) else {}
        role_status = str(result.get("role_status") or "")
        validation_issues = output.get("validation_issues") or []
        missing_data = output.get("missing_data") or []
        unavailable = output.get("excluded_capabilities") or []
        decision_kind = str(
            metadata.get("engineer_decision_kind") or output.get("decision_kind") or ""
        )
        if decision_kind == "purchase_confirmation":
            return "attention", "Расчёт завершён: требуется подтвердить закупку дефицита."
        if (
            role_status in {"failed", "waiting_human", "waiting_external"}
            or validation_issues
            or missing_data
            or case.status in {
                ProcurementCaseStatus.BLOCKED.value,
                ProcurementCaseStatus.FAILED.value,
            }
        ):
            reason = (
                (validation_issues[0] or {}).get("message")
                if validation_issues and isinstance(validation_issues[0], dict)
                else None
            )
            return "critical", str(
                reason
                or (missing_data[0] if missing_data else None)
                or (unavailable[0] if unavailable else None)
                or case.error_message
                or case.deviation_summary
                or "ИИ-агент завершил обработку с ошибкой."
            )
        positions = output.get("positions")
        if role_status == "completed" and isinstance(positions, list):
            has_shortage = any(
                float(position.get("net_requirement") or 0) > 0
                for position in positions
                if isinstance(position, dict)
            )
            if has_shortage:
                return "attention", "Расчёт завершён: подтверждённого остатка недостаточно."
            return "success", "Данные прочитаны, потребность рассчитана и полностью обеспечена."
        if unavailable:
            return "critical", str(
                unavailable[0] or "Обязательный источник данных 1С недоступен."
            )
        return "attention", "ИИ-агент выполняет расчёт или кейс ожидает своей очереди."

    @staticmethod
    def _dispatcher_bucket(case: ProcurementCase) -> tuple[str | None, str | None]:
        metadata = case.case_metadata or {}
        is_dispatcher_case = (
            case.source_type == ProcurementSourceType.REORDER_POINT.value
            or metadata.get("engineer_handoff_agent_id") == PRODUCTION_DISPATCHER_AGENT_ID
            or metadata.get("dispatcher_invoked_at")
            or metadata.get("production_dispatcher_output")
        )
        if not is_dispatcher_case:
            return None, None
        archived_bucket = metadata.get("dispatcher_archived_bucket")
        if archived_bucket in {"success", "attention", "critical"}:
            return str(archived_bucket), "Состояние сохранено после подтверждения диспетчера."
        result = case.latest_result or {}
        output = result.get("output_data") or metadata.get("production_dispatcher_output")
        output = output if isinstance(output, dict) else {}
        role_status = str(result.get("role_status") or "")
        decision_kind = str(
            metadata.get("dispatcher_decision_kind") or output.get("decision_kind") or ""
        )
        if decision_kind == "supply_confirmation":
            return "attention", "Требуется подтвердить способ обеспечения."
        if decision_kind == "critical_acknowledgement" or role_status == "failed":
            missing = output.get("missing_data") or []
            return "critical", str(
                (missing[0] if missing else None)
                or case.deviation_summary
                or "Недостаточно данных для расчёта диспетчера."
            )
        if decision_kind == "none" and role_status == "completed":
            return "success", "Запас покрывает потребность, закупка не требуется."
        if case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID or case.current_task_id:
            return "attention", "Диспетчер выполняет расчёт или кейс ожидает решения."
        return "attention", "Кейс ожидает обработки диспетчером производства."

    @staticmethod
    def _warehouse_availability_bucket(
        case: ProcurementCase,
        spec: WarehouseAvailabilitySpec,
    ) -> tuple[str | None, str | None]:
        metadata = case.case_metadata or {}
        if not is_warehouse_availability_case(case, spec):
            return None, None
        supplier_coverage = (
            metadata.get("material_order_coverage")
            if isinstance(metadata.get("material_order_coverage"), dict)
            else metadata.get("supplier_order_coverage")
            if isinstance(metadata.get("supplier_order_coverage"), dict)
            else {}
        )
        coverage_status = str(supplier_coverage.get("coverage_status") or "")
        actor = (
            "комплектовщика"
            if spec.agent_id == WAREHOUSE_PICKER_AGENT_ID
            else "начальника складского комплекса"
        )
        if metadata.get(spec.key("auto_archived_reason")) == (
            "tmc_presentation_journal_full"
        ) or (
            metadata.get(spec.key("workspace_archived_at"))
            and metadata.get("otk_handed_off_at")
        ):
            return (
                "success",
                "Передано ОТК по журналу предъявления ТМЦ.",
            )
        if (
            coverage_status == "full"
            or metadata.get(spec.key("auto_archived_reason"))
            in {"all_positions_in_supplier_orders", "all_positions_covered"}
            or metadata.get(spec.key("procurement_status")) == "covered"
        ):
            return (
                "success",
                f"Работа {actor} завершена: все позиции перекрыты; "
                "контроль исполнения закупок и перемещений у менеджера по закупкам.",
            )
        if (
            coverage_status == "partial"
            or metadata.get(spec.key("procurement_status")) == "partial"
        ):
            covered = supplier_coverage.get("covered_positions")
            total = supplier_coverage.get("positions_count")
            covered_label = (
                f"{covered} из {total}"
                if covered is not None and total is not None
                else "часть"
            )
            return (
                "attention",
                f"Ведется закупка по части позиций ({covered_label}); "
                f"непокрытый дефицит остаётся у {actor}.",
            )
        if case.status in TERMINAL_CASE_STATUSES:
            closed_label = CLOSED_REASON_LABELS.get(case.closed_reason or "") or (
                case.deviation_summary or "Кейс закрыт оркестратором."
            )
            archived_bucket = metadata.get(spec.key("archived_bucket"))
            if archived_bucket not in {"success", "attention", "critical"}:
                archived_bucket = "attention"
            return str(archived_bucket), str(closed_label)
        archived_bucket = metadata.get(spec.key("archived_bucket"))
        if archived_bucket in {"success", "attention", "critical"}:
            if metadata.get(spec.key("auto_archived_reason")) == (
                "tmc_presentation_journal_full"
            ):
                return (
                    "success",
                    "Передано ОТК по журналу предъявления ТМЦ.",
                )
            if (
                metadata.get(spec.key("auto_archived_reason"))
                in {"all_positions_in_supplier_orders", "all_positions_covered"}
            ):
                return (
                    "success",
                    f"Работа {actor} завершена: все позиции перекрыты; "
                    "контроль исполнения закупок и перемещений у менеджера по закупкам.",
                )
            return str(archived_bucket), "Состояние сохранено при передаче начальнику ОМТО."
        result = case.latest_result or {}
        output = result.get("output_data") or metadata.get(spec.output_key)
        output = output if isinstance(output, dict) else {}
        role_status = str(result.get("role_status") or "")
        decision_kind = str(
            metadata.get(spec.key("decision_kind")) or output.get("decision_kind") or ""
        )
        if decision_kind in {
            "stock_confirmation",
            "deficit_confirmation",
            "discrepancy_return",
        }:
            return "attention", "Требуется подтверждение заключения по кладовой."
        if decision_kind == "critical_acknowledgement" or role_status == "failed":
            missing = output.get("missing_data") or []
            return "critical", str(
                (missing[0] if missing else None)
                or case.deviation_summary
                or "Недостаточно данных для проверки кладовой."
            )
        if decision_kind == "none" and role_status == "completed":
            return "success", "Наличие подтверждено, кейс передан начальнику ОМТО."
        if case.current_agent_id == spec.agent_id or case.current_task_id:
            return "attention", "Выполняется проверка наличия или кейс ожидает решения."
        return "attention", f"Кейс ожидает обработки {spec.actor_label}."

    @classmethod
    def _picker_bucket(cls, case: ProcurementCase) -> tuple[str | None, str | None]:
        return cls._warehouse_availability_bucket(case, PICKER_SPEC)

    @classmethod
    def _complex_bucket(cls, case: ProcurementCase) -> tuple[str | None, str | None]:
        return cls._warehouse_availability_bucket(case, COMPLEX_CHIEF_SPEC)

    def _serialize_case_summary(self, case: ProcurementCase) -> dict[str, Any]:
        engineer_bucket, engineer_bucket_reason = self._engineer_bucket(case)
        dispatcher_bucket, dispatcher_bucket_reason = self._dispatcher_bucket(case)
        picker_bucket, picker_bucket_reason = self._picker_bucket(case)
        complex_bucket, complex_bucket_reason = self._complex_bucket(case)
        role_status = str((case.latest_result or {}).get("role_status") or "")
        metadata = case.case_metadata or {}
        supplier_coverage = (
            metadata.get("material_order_coverage")
            if isinstance(metadata.get("material_order_coverage"), dict)
            else metadata.get("supplier_order_coverage")
            if isinstance(metadata.get("supplier_order_coverage"), dict)
            else {}
        )
        supplier_coverage_status = supplier_coverage.get("coverage_status")
        purchase_manager_bucket = (
            "success"
            if supplier_coverage_status == "full"
            else "attention"
            if supplier_coverage_status == "partial"
            else "critical"
            if metadata.get("purchase_manager_invoked_at")
            else None
        )
        if engineer_bucket is None:
            engineer_work_status = None
        elif metadata.get("engineer_workspace_archived_at") or (
            case.status in TERMINAL_CASE_STATUSES
        ):
            engineer_work_status = "archived"
        elif role_status in {"waiting_human", "waiting_external", "failed"}:
            engineer_work_status = "awaiting_action"
        elif case.current_task_id and role_status != "completed":
            engineer_work_status = "processing"
        elif engineer_bucket == "success":
            engineer_work_status = "completed"
        else:
            engineer_work_status = "awaiting_action"
        if dispatcher_bucket is None:
            dispatcher_work_status = None
        elif metadata.get("dispatcher_workspace_archived_at") or (
            case.status in TERMINAL_CASE_STATUSES
        ):
            dispatcher_work_status = "archived"
        elif (
            case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
            and role_status in {"waiting_human", "waiting_external", "failed"}
        ):
            dispatcher_work_status = "awaiting_action"
        elif (
            case.current_agent_id == PRODUCTION_DISPATCHER_AGENT_ID
            and case.current_task_id
            and role_status != "completed"
        ):
            dispatcher_work_status = "processing"
        elif dispatcher_bucket == "success":
            dispatcher_work_status = "completed"
        else:
            dispatcher_work_status = "awaiting_action"
        supplier_coverage_for_picker = supplier_coverage
        if picker_bucket is None:
            picker_work_status = None
        elif metadata.get("picker_workspace_archived_at") or (
            case.status in TERMINAL_CASE_STATUSES
        ):
            picker_work_status = "archived"
        elif (
            metadata.get("picker_workspace_status") == "completed"
            or (
                supplier_coverage_for_picker.get("coverage_status") == "full"
                and not metadata.get("picker_workspace_archived_at")
            )
        ):
            picker_work_status = "completed"
        elif (
            case.current_agent_id == WAREHOUSE_PICKER_AGENT_ID
            and role_status in {"waiting_human", "waiting_external", "failed"}
        ):
            picker_work_status = "awaiting_action"
        elif (
            case.current_agent_id == WAREHOUSE_PICKER_AGENT_ID
            and case.current_task_id
            and role_status != "completed"
        ):
            picker_work_status = "processing"
        elif picker_bucket == "success":
            picker_work_status = "completed"
        else:
            picker_work_status = "awaiting_action"
        if complex_bucket is None:
            complex_work_status = None
        elif metadata.get("complex_workspace_archived_at") or (
            case.status in TERMINAL_CASE_STATUSES
        ):
            complex_work_status = "archived"
        elif (
            metadata.get("complex_workspace_status") == "completed"
            or (
                supplier_coverage_for_picker.get("coverage_status") == "full"
                and not metadata.get("complex_workspace_archived_at")
            )
        ):
            complex_work_status = "completed"
        elif (
            case.current_agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_ID
            and role_status in {"waiting_human", "waiting_external", "failed"}
        ):
            complex_work_status = "awaiting_action"
        elif (
            case.current_agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_ID
            and case.current_task_id
            and role_status != "completed"
        ):
            complex_work_status = "processing"
        elif complex_bucket == "success":
            complex_work_status = "completed"
        else:
            complex_work_status = "awaiting_action"
        return {
            "id": str(case.id),
            "correlation_id": case.correlation_id,
            "source_type": case.source_type,
            "source_1c_ref": case.source_1c_ref,
            "source_number": case.source_number,
            "source_date": case.source_date.isoformat() if case.source_date else None,
            "source_status": case.source_status,
            "source_synced_at": (
                case.source_synced_at.isoformat() if case.source_synced_at else None
            ),
            "source_basis_1c_ref": metadata.get("source_basis_1c_ref"),
            "source_basis_type": metadata.get("source_basis_type"),
            "source_basis_number": metadata.get("source_basis_number"),
            "source_basis_date": metadata.get("source_basis_date"),
            "source_basis_status": metadata.get("source_basis_status"),
            "status": case.status,
            "control_point": case.control_point,
            "current_agent_id": case.current_agent_id,
            "current_agent_name": (
                procurement_config.AGENT_NAME
                if case.current_agent_id == procurement_config.AGENT_ID
                else agent_label(case.current_agent_id)
            ),
            "current_task_id": str(case.current_task_id) if case.current_task_id else None,
            "required_date": case.required_date.isoformat() if case.required_date else None,
            "deadline_at": case.deadline_at.isoformat() if case.deadline_at else None,
            "positions_count": len(case.positions or []),
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
            "coverage_checked_at": supplier_coverage.get("checked_at"),
            "last_actualized_at": _latest_iso_timestamp(
                metadata.get("last_actualized_at"),
                supplier_coverage.get("checked_at"),
                case.source_synced_at.isoformat() if case.source_synced_at else None,
                case.updated_at.isoformat() if case.updated_at else None,
            ),
            "summary": (case.latest_result or {}).get("summary") or case.deviation_summary,
            "requires_human_review": bool((case.latest_result or {}).get("requires_human_review")),
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
            "closed_reason": case.closed_reason,
            "closed_reason_label": CLOSED_REASON_LABELS.get(case.closed_reason or ""),
            "reactivated_at": (
                case.reactivated_at.isoformat() if case.reactivated_at else None
            ),
            "source_active": case.status in SOURCE_MONITORED_CASE_STATUSES,
            "engineer_bucket": engineer_bucket,
            "engineer_bucket_reason": engineer_bucket_reason,
            "engineer_work_status": engineer_work_status,
            "engineer_decision_kind": metadata.get("engineer_decision_kind"),
            "engineer_invoked_at": metadata.get("engineer_invoked_at"),
            "engineer_workspace_archived_at": metadata.get(
                "engineer_workspace_archived_at"
            ),
            "engineer_action_at": metadata.get("engineer_action_at"),
            "engineer_critical_acknowledged_at": metadata.get(
                "engineer_critical_acknowledged_at"
            ),
            "dispatcher_bucket": dispatcher_bucket,
            "dispatcher_bucket_reason": dispatcher_bucket_reason,
            "dispatcher_work_status": dispatcher_work_status,
            "dispatcher_decision_kind": metadata.get("dispatcher_decision_kind"),
            "dispatcher_invoked_at": metadata.get("dispatcher_invoked_at"),
            "dispatcher_workspace_archived_at": metadata.get(
                "dispatcher_workspace_archived_at"
            ),
            "dispatcher_action_at": metadata.get("dispatcher_action_at"),
            "dispatcher_critical_acknowledged_at": metadata.get(
                "dispatcher_critical_acknowledged_at"
            ),
            "dispatcher_stream": (
                "reorder_point"
                if case.source_type == ProcurementSourceType.REORDER_POINT.value
                else "after_engineer"
                if metadata.get("engineer_handoff_agent_id")
                == PRODUCTION_DISPATCHER_AGENT_ID
                or metadata.get("production_preparation_engineer_output")
                else None
            ),
            "department_name": case.department_name,
            "picker_bucket": picker_bucket,
            "picker_bucket_reason": picker_bucket_reason,
            "picker_work_status": picker_work_status,
            "picker_decision_kind": metadata.get("picker_decision_kind"),
            "picker_invoked_at": metadata.get("picker_invoked_at"),
            "picker_workspace_archived_at": metadata.get("picker_workspace_archived_at"),
            "picker_action_at": metadata.get("picker_action_at"),
            "picker_critical_acknowledged_at": metadata.get(
                "picker_critical_acknowledged_at"
            ),
            "complex_bucket": complex_bucket,
            "complex_bucket_reason": complex_bucket_reason,
            "complex_work_status": complex_work_status,
            "complex_decision_kind": metadata.get("complex_decision_kind"),
            "complex_invoked_at": metadata.get("complex_invoked_at"),
            "complex_workspace_archived_at": metadata.get(
                "complex_workspace_archived_at"
            ),
            "complex_action_at": metadata.get("complex_action_at"),
            "complex_critical_acknowledged_at": metadata.get(
                "complex_critical_acknowledged_at"
            ),
            "purchase_manager_work_status": metadata.get(
                "purchase_manager_workspace_status"
            ),
            "purchase_manager_bucket": purchase_manager_bucket,
            "purchase_manager_bucket_reason": (
                "Все позиции перекрыты заказами поставщику и/или перемещениями."
                if supplier_coverage_status == "full"
                else "Часть позиций ещё не покрыта закупками или перемещениями."
                if supplier_coverage_status == "partial"
                else "Связанные активные заказы поставщику не найдены."
                if purchase_manager_bucket
                else None
            ),
            "purchase_manager_invoked_at": metadata.get("purchase_manager_invoked_at"),
            "purchase_manager_workspace_archived_at": metadata.get(
                "purchase_manager_workspace_archived_at"
            ),
            "supplier_coverage_status": supplier_coverage_status,
            "coverage_sources": sorted(
                {
                    str(position.get("coverage_source"))
                    for position in (supplier_coverage.get("positions") or [])
                    if isinstance(position, dict)
                    and position.get("coverage_source")
                    and position.get("coverage_source") != "none"
                }
            ),
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
        configured = list(
            (case.case_metadata or {}).get("route_stages") or DEFAULT_ROUTE_STAGES
        )
        # Старые кейсы могли сохранить маршрут без этапа ОТК — дополняем из DEFAULT.
        configured_ids = {
            str(item.get("stage_id") or "")
            for item in configured
            if isinstance(item, dict)
        }
        for item in DEFAULT_ROUTE_STAGES:
            stage_id = str(item.get("stage_id") or "")
            if stage_id and stage_id not in configured_ids:
                configured.append(dict(item))
                configured_ids.add(stage_id)
        status_map = {
            ProcurementCaseStatus.NEW.value: "basis",
            ProcurementCaseStatus.AGENT_WAITING.value: "basis",
            ProcurementCaseStatus.DATA_CHECK.value: "data",
            ProcurementCaseStatus.COVERAGE_CHECK.value: "coverage",
            ProcurementCaseStatus.HUMAN_REQUIRED.value: "coverage",
            ProcurementCaseStatus.BLOCKED.value: "coverage",
            ProcurementCaseStatus.ORDERED.value: "purchase",
            ProcurementCaseStatus.QUALITY_QUEUED.value: "quality",
            ProcurementCaseStatus.QUALITY_ASSIGNED.value: "quality",
            ProcurementCaseStatus.QUALITY_DOC_CHECK.value: "quality",
            ProcurementCaseStatus.QUALITY_INSPECTION.value: "quality",
            ProcurementCaseStatus.QUALITY_DECISION.value: "quality",
            ProcurementCaseStatus.QUALITY_RELEASED.value: "quality",
            ProcurementCaseStatus.CLOSED.value: "receipt",
            ProcurementCaseStatus.FAILED.value: "data",
        }
        current_stage = case.control_point or status_map.get(case.status, "basis")
        metadata = case.case_metadata or {}
        picker_active = (
            case.current_agent_id
            in {WAREHOUSE_PICKER_AGENT_ID, WAREHOUSE_COMPLEX_CHIEF_AGENT_ID}
            or (
                WAREHOUSE_PICKER_AGENT_ID in (case.assigned_agents or [])
                and metadata.get("picker_invoked_at")
                and not metadata.get("picker_workspace_archived_at")
                and metadata.get("picker_workspace_status") != "archived"
            )
            or (
                WAREHOUSE_COMPLEX_CHIEF_AGENT_ID in (case.assigned_agents or [])
                and metadata.get("complex_invoked_at")
                and not metadata.get("complex_workspace_archived_at")
                and metadata.get("complex_workspace_status") != "archived"
            )
        )
        purchase_manager_active = (
            case.current_agent_id == PURCHASE_MANAGER_AGENT_ID
            or (
                PURCHASE_MANAGER_AGENT_ID in (case.assigned_agents or [])
                and metadata.get("purchase_manager_invoked_at")
                and not metadata.get("purchase_manager_workspace_archived_at")
            )
        )
        otk_active = (
            case.current_agent_id == QUALITY_ENGINEER_AGENT_ID
            or bool(metadata.get("otk_handed_off_at"))
            or bool(metadata.get("otk_started_at"))
            or case.status.startswith("quality_")
            or (
                isinstance(metadata.get("tmc_presentation_coverage"), dict)
                and str(
                    (metadata.get("tmc_presentation_coverage") or {}).get("status") or ""
                )
                in {"partial", "full"}
            )
        )
        # Ролевой агент важнее пустого/устаревшего control_point: иначе маршрут
        # залипает на «Основание», хотя уже работает комплектовщик/менеджер.
        # ОТК в параллели с PM/picker: ствол остаётся на coverage/purchase,
        # этап quality рисуется отдельной веткой на фронте.
        if picker_active and current_stage in {"basis", "data", "KT1", None, ""}:
            current_stage = "coverage"
        elif (
            purchase_manager_active
            and not picker_active
            and current_stage in {"basis", "data", "coverage", "KT1", None, ""}
        ):
            current_stage = "purchase"
        elif (
            otk_active
            and not picker_active
            and not purchase_manager_active
            and current_stage
            in {
                "basis",
                "data",
                "coverage",
                "purchase",
                "KT1",
                None,
                "",
            }
        ):
            current_stage = "quality"
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
                "role_agent_task_enqueued": "Назначен ролевой агент",
                "role_agent_result_received": "Получен результат ролевого агента",
                "role_agent_resumed": "Состояние ролевого агента обновлено",
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
            "last_polled_at": (
                state.last_polled_at.isoformat() if state and state.last_polled_at else None
            ),
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


def build_poll_lock_key(scope: str = "all") -> str:
    digest = hashlib.sha1(f"procurement-orchestrator-poll:{scope}".encode()).hexdigest()[
        :12
    ]
    return f"procurement:orchestrator:poll:{scope}:{digest}"


def _latest_iso_timestamp(*values: Any) -> str | None:
    latest: datetime | None = None
    latest_raw: str | None = None
    for value in values:
        if value is None or value == "":
            continue
        raw = value.isoformat() if isinstance(value, datetime) else str(value)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            if latest_raw is None:
                latest_raw = raw
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if latest is None or parsed > latest:
            latest = parsed
            latest_raw = raw
    return latest_raw


__all__ = ["ProcurementOrchestratorService", "build_poll_lock_key"]
