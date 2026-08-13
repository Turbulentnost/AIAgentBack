from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, DocumentAnalysisUser
from app.agents.document_analysis_agent.excel_service import (
    UploadedWorkbook,
    analyze_aveon_excel_files,
    classify_aveon_excel_files,
)
from app.agents.document_analysis_agent.dashboard_snapshot import (
    clear_dashboard_snapshot,
    derive_dashboard_date_msk,
    is_dashboard_stale_for_today,
    load_dashboard_snapshot,
    save_dashboard_snapshot,
    snapshot_had_valid_shift_today,
    today_msk_iso,
    update_dashboard_refresh_state,
    update_merged_shipment_snapshot,
    update_task_progress,
)
from app.agents.document_analysis_agent.shift_assignment import (
    SHIFT_MANAGER_EMAILS,
    SHIFT_MANAGER_REGIONS,
    SHIFT_MANAGER_ROSTER,
    resolve_shift_manager_name,
)
from app.agents.document_analysis_agent.shift_live_progress import (
    has_any_live_shift_for_today,
    resolve_manager_live_report,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.agents.document_analysis_agent.reveal_in_explorer import reveal_bytes_in_explorer
from app.integrations.minio import MinioObjectError
from app.models.shift_completion import ShiftCompletionReport
from app.schemas.agent import (
    AgentAccessManagementRead,
    AgentAccessRead,
    AgentAccessUpdate,
    AgentCreate,
    AgentDepartmentGrantRead,
    AgentRead,
    AgentUpdate,
    AgentUserGrantRead,
)
from app.schemas.developer_feedback import (
    DeveloperFeedbackAttachmentRead,
    DeveloperFeedbackMessageRead,
    DeveloperFeedbackMessagesResponse,
    DeveloperFeedbackSendResponse,
    DeveloperFeedbackThreadRead,
    DeveloperFeedbackThreadsResponse,
)
from app.services.agent_access_service import AgentAccessService, AgentAccessServiceError
from app.services.agent_icon_service import AgentIconService
from app.services.agent_service import AgentService
from app.services.aveon_shipment_schedule_schema import ensure_aveon_shipment_schedule_tables
from app.services.aveon_shipment_schedule_service import (
    AveonShipmentScheduleError,
    AveonShipmentScheduleService,
    shipment_change_idempotency_key,
)
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.developer_feedback_email import FeedbackEmailError
from app.services.developer_feedback_chat_service import (
    DeveloperFeedbackAccessError,
    DeveloperFeedbackChatService,
    DeveloperFeedbackUpload,
    DeveloperFeedbackValidationError,
    is_developer_feedback_admin,
    is_developer_feedback_participant,
)
from app.services.shift_completion_email import (
    ShiftCompletionAttachment,
    ShiftCompletionStats,
    ShiftCompletionTaskView,
    send_shift_completion_email,
)
from app.services.shift_start_email import ShiftStartSummary, send_shift_start_email
from app.services.shift_completion_schema import ensure_shift_completion_tables
from app.services.meeting_permission import append_meeting_agent_for_office_management
from app.services.nd_control_permission import append_nd_control_agent_for_quality_deputy
from app.services.permission_service import PermissionService
from app.services.document_analysis_permission import filter_available_agents_for_avion_only_user
from app.services.procurement_permission import (
    append_production_preparation_engineer_agent,
)
from app.services.profile_image_service import AvatarValidationError

router = APIRouter(prefix="/agents", tags=["agents"])
logger = get_logger(__name__)

_dashboard_refresh_locks: dict[str, asyncio.Lock] = {}


def _is_shipment_schedule_filename(filename: str) -> bool:
    name_lower = filename.lower()
    return (
        "merged_schedule" in name_lower
        or "график отгруз" in name_lower
        or "отгруз" in name_lower
    )


def _build_dashboard_refresh_inputs(uploaded: list[UploadedWorkbook]) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for item in uploaded:
        if _is_shipment_schedule_filename(item.filename):
            continue
        files.append(
            {
                "file_name": item.filename,
                "file_base64": base64.b64encode(item.content).decode("ascii"),
                "file_sha256": hashlib.sha256(item.content).hexdigest(),
                "size": len(item.content),
            }
        )
    return {"version": 1, "files": files}


def _restore_dashboard_refresh_inputs_from_schedule_snapshot(
    user_id: object | None,
) -> list[UploadedWorkbook] | None:
    from app.agents.document_analysis_agent.schedule_snapshot import (
        get_saved_detailed_file,
        get_saved_production_file,
        list_saved_detailed_schedules,
    )

    restored: list[UploadedWorkbook] = []
    production = get_saved_production_file(user_id)
    if production is not None:
        restored.append(UploadedWorkbook(filename=production[0], content=production[1]))
    for entry in list_saved_detailed_schedules(user_id):
        if not entry.get("has_file"):
            continue
        year = int(entry.get("year") or 0)
        month = int(entry.get("month_num") or 0)
        detailed = get_saved_detailed_file(user_id, year, month)
        if detailed is None:
            continue
        restored.append(UploadedWorkbook(filename=detailed[0], content=detailed[1]))
    return restored or None


def _restore_dashboard_refresh_inputs(
    snapshot: dict,
    user_id: object | None = None,
) -> list[UploadedWorkbook] | None:
    merged: dict[str, UploadedWorkbook] = {}

    def _add(workbook: UploadedWorkbook) -> None:
        key = workbook.filename.strip().lower()
        if key and key not in merged:
            merged[key] = workbook

    payload = snapshot.get("refresh_inputs")
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                filename = str(entry.get("file_name") or "workbook.xlsx")
                file_base64 = entry.get("file_base64")
                if not isinstance(file_base64, str) or not file_base64:
                    continue
                try:
                    raw = base64.b64decode(file_base64)
                except Exception:
                    continue
                _add(UploadedWorkbook(filename=filename, content=raw))

    for workbook in _restore_dashboard_refresh_inputs_from_schedule_snapshot(user_id) or []:
        _add(workbook)

    return list(merged.values()) or None


async def _auto_refresh_inputs_error(
    uploaded: list[UploadedWorkbook],
    db: DbSession,
) -> str | None:
    from app.agents.document_analysis_agent.excel_service import (
        ROLE_DETAILED_PRODUCTION_SCHEDULE,
        classify_aveon_excel_files,
    )
    from app.agents.document_analysis_agent.onec_db_sources import (
        load_latest_detailed_production_schedule_from_db,
    )

    try:
        role_map, _ = await classify_aveon_excel_files(uploaded)
    except Exception as exc:
        return f"Не удалось определить роли файлов для автопересчёта: {exc}"
    if any(
        role_map.get(workbook.filename) == ROLE_DETAILED_PRODUCTION_SCHEDULE
        for workbook in uploaded
    ):
        return None

    db_detailed = await load_latest_detailed_production_schedule_from_db(db)
    if db_detailed.plans:
        return None

    return (
        "Для автопересчёта дашборда «За день» нужен план производства по дням "
        "(Excel или синхронизация из 1С в БД). Запустите анализ вручную или выгрузите план из 1С."
    )


def _coverage_day_plan_count(coverage: dict[str, Any] | None) -> int:
    if not isinstance(coverage, dict):
        return 0
    periods = coverage.get("periods")
    if not isinstance(periods, dict):
        return 0
    day = periods.get("day")
    if not isinstance(day, dict):
        return 0
    products = day.get("products")
    if not isinstance(products, dict):
        return 0
    tiles = products.get("tiles")
    if not isinstance(tiles, dict):
        return 0
    return int(tiles.get("all") or 0)


def _refresh_status_allows_retry(status: object | None) -> bool:
    return status in {"missing_inputs", "missing_detailed_schedule"}


def _dashboard_auto_refresh_incomplete(snapshot: dict[str, Any]) -> bool:
    """Сегодняшний snapshot без дневного плана — вероятно неполный автопересчёт."""
    if derive_dashboard_date_msk(snapshot) != today_msk_iso():
        return False
    return _coverage_day_plan_count(snapshot.get("coverage_dashboard")) == 0


def _dashboard_refresh_lock_key(user_id: object | None, day: str) -> str:
    return f"{user_id or 'anonymous'}:{day}"


def _dashboard_refresh_lock(user_id: object | None, day: str) -> asyncio.Lock:
    key = _dashboard_refresh_lock_key(user_id, day)
    lock = _dashboard_refresh_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _dashboard_refresh_locks[key] = lock
    return lock


async def _prepare_aveon_uploaded_with_server_shipment(
    uploaded: list[UploadedWorkbook],
    db: DbSession,
) -> tuple[list[UploadedWorkbook], dict]:
    await ensure_aveon_shipment_schedule_tables()
    shipment_service = AveonShipmentScheduleService(db)
    active_russia_schedule = await shipment_service.get_active_russia()
    if active_russia_schedule is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Российский график отгрузок не загружен в БД. Выполните служебную загрузку графика.",
        )

    from app.agents.document_analysis_agent.temp_schedule_merge import merge_schedule_files

    shipment_merge = await merge_schedule_files(
        [(active_russia_schedule.version.file_name, active_russia_schedule.raw)],
        include_google_sheets=True,
        include_merged_inputs=True,
    )
    if not shipment_merge.get("ok") or not shipment_merge.get("file_base64"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(
                shipment_merge.get("message")
                or "Не удалось собрать график отгрузок из БД и Google Sheets"
            ),
        )
    google_meta = ((shipment_merge.get("stats") or {}).get("google_sheets") or {})
    if not google_meta.get("included"):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось получить актуальный график по Китаю из Google Sheets: "
            + str(google_meta.get("error") or "Google Sheets не вернул данные"),
        )

    shipment_raw = base64.b64decode(str(shipment_merge["file_base64"]))
    filtered = [item for item in uploaded if not _is_shipment_schedule_filename(item.filename)]
    filtered.append(UploadedWorkbook(filename="merged_schedule.xlsx", content=shipment_raw))
    return filtered, shipment_merge


def _serialize_logistics_risks(result: Any) -> dict[str, Any]:
    return {
        "as_of": result.logistics_risks.as_of if result.logistics_risks else None,
        "stages": [
            {
                "key": stage.key,
                "label": stage.label,
                "items": [
                    {
                        "nomenclature": item.nomenclature,
                        "supplier": item.supplier,
                        "quantity": item.quantity,
                        "moscow_date": item.moscow_date,
                        "milestone_date": item.milestone_date,
                        "sheet": item.sheet,
                        "window_start": item.window_start,
                        "window_end": item.window_end,
                        "days_remaining": item.days_remaining,
                        "risk_ratio": item.risk_ratio,
                        "risk_level": item.risk_level,
                    }
                    for item in stage.items
                ],
            }
            for stage in (result.logistics_risks.stages if result.logistics_risks else [])
        ],
    }


def _build_merged_shipment_payload(
    uploaded: list[UploadedWorkbook],
    shipment_merge: dict,
) -> dict[str, Any] | None:
    for wb in uploaded:
        name_lower = wb.filename.lower()
        if "merged_schedule" in name_lower or name_lower == "merged_schedule.xlsx":
            shipment_b64 = base64.b64encode(wb.content).decode("ascii")
            from app.agents.document_analysis_agent.temp_schedule_merge import (
                build_merged_schedule_preview_values,
            )

            preview_values = build_merged_schedule_preview_values(wb.content)
            header_len = len(preview_values[0]) if preview_values else 0
            return {
                "file_name": wb.filename,
                "file_base64": shipment_b64,
                "values": shipment_merge.get("preview_values") or preview_values,
                "stats": shipment_merge.get("stats") or {
                    "nomenclature_total": max(len(preview_values) - 1, 0),
                    "date_columns": max(header_len - 12, 0),
                },
                "source_count": 2,
            }
    return None


async def _auto_refresh_dashboard_snapshot(
    *,
    user_id: object | None,
    db: DbSession,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    today = today_msk_iso()
    restored = _restore_dashboard_refresh_inputs(snapshot, user_id)
    if not restored:
        snapshot = update_dashboard_refresh_state(
            user_id,
            refresh_status="missing_inputs",
            refresh_error="Нет сохранённых входных файлов для автопересчёта.",
            refresh_attempted_date_msk=today,
        ) or snapshot
        snapshot["shift_today_msk"] = today
        if not snapshot.get("dashboard_date_msk"):
            snapshot["dashboard_date_msk"] = derive_dashboard_date_msk(snapshot)
        return snapshot

    inputs_error = await _auto_refresh_inputs_error(restored, db)
    if inputs_error:
        snapshot = update_dashboard_refresh_state(
            user_id,
            refresh_status="missing_detailed_schedule",
            refresh_error=inputs_error,
            refresh_attempted_date_msk=today,
        ) or snapshot
        snapshot["shift_today_msk"] = today
        if not snapshot.get("dashboard_date_msk"):
            snapshot["dashboard_date_msk"] = derive_dashboard_date_msk(snapshot)
        return snapshot

    refresh_inputs = _build_dashboard_refresh_inputs(restored)

    lock = _dashboard_refresh_lock(user_id, today)
    async with lock:
        current = load_dashboard_snapshot(user_id)
        if isinstance(current, dict) and derive_dashboard_date_msk(current) == today:
            if _coverage_day_plan_count(current.get("coverage_dashboard")) > 0:
                return current
        if isinstance(current, dict) and current.get("refresh_attempted_date_msk") == today:
            if (
                not _refresh_status_allows_retry(current.get("refresh_status"))
                and not _dashboard_auto_refresh_incomplete(current)
            ):
                return current

        analyzed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            uploaded, shipment_merge = await _prepare_aveon_uploaded_with_server_shipment(
                restored,
                db,
            )
            result = await analyze_aveon_excel_files(uploaded, db=db, user_id=user_id)
            previous_day_plans = _coverage_day_plan_count(snapshot.get("coverage_dashboard"))
            new_day_plans = _coverage_day_plan_count(result.coverage_dashboard)
            if new_day_plans == 0 and previous_day_plans > 0:
                raise ValueError(
                    "Автопересчёт не нашёл дневной план. "
                    "Проверьте синхронизацию плана из 1С или загрузите детальный график производства."
                )
        except Exception as exc:
            snapshot = update_dashboard_refresh_state(
                user_id,
                refresh_status="error",
                refresh_error=str(exc),
                refresh_attempted_date_msk=today,
            ) or snapshot
            snapshot["shift_today_msk"] = today
            return snapshot

        shift_b64 = (
            base64.b64encode(result.shift_assignment_xlsx_bytes).decode("ascii")
            if result.shift_assignment_xlsx_bytes
            else None
        )
        task_dashboard_payload = None
        shift_assignment_payload = None
        if result.shift_assignment_values:
            task_dashboard_payload = {
                "values": result.shift_assignment_values,
                "row_priorities": result.shift_assignment_row_priorities,
                "row_kinds": result.shift_assignment_row_kinds,
                "meta": result.shift_assignment_meta,
                "result_texts": {},
                "result_evals": {},
            }
            if shift_b64:
                shift_assignment_payload = {
                    "valid_date": today,
                    "file_name": result.shift_assignment_file_name,
                    "file_base64": shift_b64,
                }

        refreshed = save_dashboard_snapshot(
            user_id,
            logistics_risks=_serialize_logistics_risks(result),
            analyzed_at=analyzed_at,
            task_dashboard=task_dashboard_payload,
            shift_assignment=shift_assignment_payload,
            merged_shipment_schedule=_build_merged_shipment_payload(uploaded, shipment_merge),
            coverage_dashboard=result.coverage_dashboard,
            meta={
                "source": result.source,
                "stock_files": result.stock_files,
                "shipment_files": result.shipment_files,
                "merged_nomenclatures_count": len(result.merged_nomenclatures),
                "forecast_deficit_count": sum(
                    1
                    for row in result.merged_nomenclatures
                    if any(value < 0 for value in row.monthly_forecast.values())
                ),
                "auto_refresh": True,
            },
            refresh_inputs=refresh_inputs,
            dashboard_date_msk=today,
            refresh_status="auto_refreshed",
            auto_refreshed_at=datetime.now(timezone.utc).isoformat(),
            refresh_source_analyzed_at=str(snapshot.get("analyzed_at") or ""),
        )
        refreshed["shift_today_msk"] = today
        return refreshed


async def _agent_read(db: DbSession, agent) -> AgentRead:
    data = AgentRead.model_validate(agent).model_dump()
    data["icon_url"] = AgentIconService(db).build_icon_url(agent)
    return AgentRead(**data)


async def _agent_access_read(db: DbSession, agent, current_user) -> AgentAccessRead:
    data = (await _agent_read(db, agent)).model_dump()
    data.update(
        {
            "access_level": "full" if current_user.is_superuser else "granted",
            "can_run": True,
            "can_view_results": True,
            "can_approve": current_user.is_superuser,
            "can_configure": current_user.is_superuser,
        }
    )
    return AgentAccessRead(**data)


@router.get("/available", response_model=list[AgentAccessRead])
async def list_available_agents(db: DbSession, current_user: CurrentUser):
    agents = await PermissionService(db).list_available_agents(current_user)
    agents = await append_nd_control_agent_for_quality_deputy(db, current_user, agents)
    agents = await append_meeting_agent_for_office_management(db, current_user, agents)
    agents = await append_production_preparation_engineer_agent(db, current_user, agents)
    agents = await filter_available_agents_for_avion_only_user(db, current_user, agents)
    return [await _agent_access_read(db, agent, current_user) for agent in agents]


@router.get("", response_model=list[AgentRead])
async def list_agents(db: DbSession, limit: int = 50, offset: int = 0):
    agents = await AgentService(db).list(limit, offset)
    return [await _agent_read(db, agent) for agent in agents]


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(db: DbSession, data: AgentCreate):
    agent = await AgentService(db).create(data)
    return await _agent_read(db, agent)


@router.post("/document-analysis/reveal-in-explorer")
async def reveal_document_analysis_file_in_explorer(
    _user: DocumentAnalysisUser,
    file: Annotated[UploadFile, File(...)],
):
    """Показать файл в проводнике Windows (копия во временной папке — браузер не отдаёт исходный путь)."""
    filename = file.filename or "workbook.xlsx"
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Пустой файл")
    try:
        path = reveal_bytes_in_explorer(filename, content)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": True, "path": path}


def _require_feedback_user(user: User | None) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Для обратной связи нужно войти в систему.")
    if not is_developer_feedback_admin(user) and not is_developer_feedback_participant(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Обратная связь доступна только в агенте «Авион».",
        )
    return user


async def _feedback_uploads_from_files(
    files: list[UploadFile] | None,
) -> list[DeveloperFeedbackUpload]:
    chat_uploads: list[DeveloperFeedbackUpload] = []
    for upload in files or []:
        content = await upload.read()
        if not content:
            continue
        filename = upload.filename or "attachment"
        chat_uploads.append(
            DeveloperFeedbackUpload(
                filename=filename,
                content=content,
                content_type=upload.content_type,
            )
        )
    return chat_uploads


def _feedback_attachment_read(attachment) -> DeveloperFeedbackAttachmentRead:
    return DeveloperFeedbackAttachmentRead(
        id=attachment.id,
        original_filename=attachment.original_filename,
        content_type=attachment.content_type,
        file_size=attachment.file_size,
        checksum=attachment.checksum,
        download_url=(
            f"{settings.API_V1_PREFIX}/agents/document-analysis/developer-feedback/"
            f"attachments/{attachment.id}"
        ),
        created_at=attachment.created_at,
    )


def _feedback_message_read(message) -> DeveloperFeedbackMessageRead:
    return DeveloperFeedbackMessageRead(
        id=message.id,
        thread_id=message.thread_id,
        author_user_id=message.author_user_id,
        author_role=message.author_role,
        author_name=message.author_name,
        author_email=message.author_email,
        body=message.body,
        created_at=message.created_at,
        attachments=[_feedback_attachment_read(item) for item in (message.attachments or [])],
    )


def _feedback_thread_read(
    service: DeveloperFeedbackChatService,
    user: User,
    thread,
) -> DeveloperFeedbackThreadRead:
    return DeveloperFeedbackThreadRead(
        id=thread.id,
        participant_user_id=thread.participant_user_id,
        participant_name=thread.participant_name,
        participant_email=thread.participant_email,
        status=thread.status,
        last_message_at=thread.last_message_at,
        last_message_preview=service.last_message_preview(thread),
        unread_count=service.unread_count(user, thread),
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _feedback_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, DeveloperFeedbackAccessError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, DeveloperFeedbackValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.get(
    "/document-analysis/developer-feedback/threads",
    response_model=DeveloperFeedbackThreadsResponse,
)
async def list_document_analysis_developer_feedback_threads(
    db: DbSession,
    _user: DocumentAnalysisUser,
):
    """Список диалогов обратной связи: свой для пользователя, все разрешённые для разработчика."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    try:
        threads = await service.list_threads(user)
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc
    return DeveloperFeedbackThreadsResponse(
        mode=service.user_mode(user),
        threads=[_feedback_thread_read(service, user, thread) for thread in threads],
    )


@router.get(
    "/document-analysis/developer-feedback/threads/{thread_id}/messages",
    response_model=DeveloperFeedbackMessagesResponse,
)
async def get_document_analysis_developer_feedback_messages(
    thread_id: uuid.UUID,
    db: DbSession,
    _user: DocumentAnalysisUser,
):
    """История сообщений конкретного диалога."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    try:
        thread = await service.get_thread_for_user(user, thread_id)
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc
    return DeveloperFeedbackMessagesResponse(
        mode=service.user_mode(user),
        thread=_feedback_thread_read(service, user, thread),
        messages=[_feedback_message_read(message) for message in thread.messages],
    )


@router.post(
    "/document-analysis/developer-feedback/threads/{thread_id}/messages",
    response_model=DeveloperFeedbackSendResponse,
)
async def send_document_analysis_developer_feedback_thread_message(
    thread_id: uuid.UUID,
    db: DbSession,
    _user: DocumentAnalysisUser,
    message: Annotated[str, Form(min_length=1)],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    """Новое сообщение в существующем диалоге."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    chat_uploads = await _feedback_uploads_from_files(files)
    try:
        thread, saved_message = await service.add_message(
            user,
            thread_id=thread_id,
            body=message,
            attachments=chat_uploads,
        )
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc
    return DeveloperFeedbackSendResponse(
        mode=service.user_mode(user),
        thread=_feedback_thread_read(service, user, thread),
        message=_feedback_message_read(saved_message),
    )


@router.post(
    "/document-analysis/developer-feedback/threads/{thread_id}/read",
    response_model=DeveloperFeedbackThreadRead,
)
async def mark_document_analysis_developer_feedback_thread_read(
    thread_id: uuid.UUID,
    db: DbSession,
    _user: DocumentAnalysisUser,
):
    """Отметить диалог прочитанным для текущего пользователя."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    try:
        thread = await service.mark_thread_read(user, thread_id)
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc
    return _feedback_thread_read(service, user, thread)


@router.get("/document-analysis/developer-feedback/attachments/{attachment_id}")
async def download_document_analysis_developer_feedback_attachment(
    attachment_id: uuid.UUID,
    db: DbSession,
    _user: DocumentAnalysisUser,
):
    """Скачать вложение диалога, если текущий пользователь является участником."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    try:
        attachment = await service.get_attachment_for_user(user, attachment_id)
        path = service.attachment_path(attachment)
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл вложения не найден.")
    filename = quote(attachment.original_filename)
    return Response(
        content=path.read_bytes(),
        media_type=attachment.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/document-analysis/developer-feedback", response_model=DeveloperFeedbackSendResponse)
async def send_document_analysis_developer_feedback(
    db: DbSession,
    _user: DocumentAnalysisUser,
    message: Annotated[str, Form(min_length=3)],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    """Создать/обновить пользовательский диалог обратной связи внутри агента Авион."""
    user = _require_feedback_user(_user)
    service = DeveloperFeedbackChatService(db)
    chat_uploads = await _feedback_uploads_from_files(files)
    try:
        thread, saved_message = await service.add_message(
            user,
            body=message,
            attachments=chat_uploads,
        )
    except (DeveloperFeedbackAccessError, DeveloperFeedbackValidationError) as exc:
        raise _feedback_exception(exc) from exc

    return DeveloperFeedbackSendResponse(
        mode=service.user_mode(user),
        thread=_feedback_thread_read(service, user, thread),
        message=_feedback_message_read(saved_message),
    )


@router.post("/document-analysis/classify-excel")
async def classify_document_excel_files(
    _user: DocumentAnalysisUser,
    files: Annotated[list[UploadFile], File(...)],
):
    """Быстрое определение ролей загруженных Excel до полного запуска агента."""
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один Excel-файл")

    uploaded: list[UploadedWorkbook] = []
    for file in files:
        filename = file.filename or "workbook.xlsx"
        if not filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Файл {filename} должен быть в формате .xlsx, .xlsm или .xls",
            )
        uploaded.append(UploadedWorkbook(filename=filename, content=await file.read()))

    try:
        roles, source = await classify_aveon_excel_files(uploaded)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Не удалось определить роли файлов: {exc}",
        ) from exc

    return {
        "source": source,
        "roles": [
            {"filename": filename, "role": role}
            for filename, role in sorted(roles.items())
        ],
    }


@router.get("/document-analysis/dashboard-latest")
async def get_document_analysis_dashboard_latest(_user: DocumentAnalysisUser, db: DbSession):
    """Последний сохранённый дашборд (контрольные точки) после анализа Авион."""
    user_id = getattr(_user, "id", None) if _user is not None else None
    snapshot = load_dashboard_snapshot(user_id)
    if snapshot is None:
        return {"ok": False, "snapshot": None}
    needs_refresh = is_dashboard_stale_for_today(snapshot) or _dashboard_auto_refresh_incomplete(
        snapshot
    )
    if needs_refresh:
        attempted_today = snapshot.get("refresh_attempted_date_msk") == today_msk_iso()
        refresh_status = snapshot.get("refresh_status")
        if (
            not attempted_today
            or _refresh_status_allows_retry(refresh_status)
            or _dashboard_auto_refresh_incomplete(snapshot)
        ):
            snapshot = await _auto_refresh_dashboard_snapshot(
                user_id=user_id,
                db=db,
                snapshot=snapshot,
            )
    if isinstance(snapshot, dict) and not snapshot.get("dashboard_date_msk"):
        snapshot["dashboard_date_msk"] = derive_dashboard_date_msk(snapshot)
    return {"ok": True, "snapshot": snapshot}


@router.delete("/document-analysis/dashboard-latest")
async def delete_document_analysis_dashboard_latest(_user: DocumentAnalysisUser):
    """Сбросить сохранённый дашборд текущего пользователя."""
    user_id = getattr(_user, "id", None) if _user is not None else None
    removed = clear_dashboard_snapshot(user_id)
    return {"ok": True, "removed": removed}


# TEMP(Aveon OData ping) — удалить целиком без последствий
@router.post("/document-analysis/temp-odata-ping")
async def temp_aveon_odata_ping(_user: DocumentAnalysisUser, db: DbSession):
    """TEMP: все остатки из 1С → PostgreSQL (без агентов). Удалить вместе с кнопкой на фронте."""
    import asyncio

    from app.services.onec_stock_sync import fetch_stock_items_from_onec, replace_stock_in_db

    payload = await asyncio.to_thread(fetch_stock_items_from_onec)
    if not payload.get("ok"):
        return payload

    try:
        saved = await replace_stock_in_db(db, payload.get("items") or [])
    except Exception as exc:
        return {
            **payload,
            "ok": False,
            "message": f"Остатки из 1С получены ({payload.get('count')}), но запись в БД не удалась: {exc}",
            "saved_count": 0,
            "db_count": 0,
        }

    match = saved["db_count"] == payload["count"]
    payload["saved_count"] = saved["saved_count"]
    payload["db_count"] = saved["db_count"]
    payload["sync_run_id"] = saved["sync_run_id"]
    payload["db_match"] = match
    payload["message"] = (
        f"{payload['message']}; записано в БД: {saved['saved_count']} "
        f"(проверка count 1С={payload['count']} vs БД={saved['db_count']}"
        f"{' — OK' if match else ' — РАСХОЖДЕНИЕ'})"
    )
    return payload


# TEMP(Aveon resource specs) — удалить целиком без последствий
@router.post("/document-analysis/temp-resource-specs-sync")
async def temp_aveon_resource_specs_sync(_user: DocumentAnalysisUser, db: DbSession):
    """TEMP: ресурсные спецификации из 1С → PostgreSQL. Удалить вместе с кнопкой на фронте."""
    import asyncio

    from app.services.onec_resource_spec_sync import (
        fetch_resource_specs_from_onec,
        replace_resource_specs_in_db,
    )

    payload = await asyncio.to_thread(fetch_resource_specs_from_onec)
    if not payload.get("ok"):
        return payload

    specs = payload.pop("specs", [])
    nomenclature_items = payload.pop("nomenclature_items", [])
    try:
        saved = await replace_resource_specs_in_db(
            db, specs, nomenclature_items=nomenclature_items
        )
    except Exception as exc:
        return {
            **payload,
            "ok": False,
            "message": (
                f"Спецификации из 1С получены ({payload.get('count')}), "
                f"но запись в БД не удалась: {exc}"
            ),
            "saved_specs": 0,
            "db_specs": 0,
            "db_match": False,
        }

    match = (
        saved["db_specs"] == payload["count"]
        and saved["db_materials"] == payload["materials_count"]
        and saved["db_outputs"] == payload["outputs_count"]
    )
    payload.update(
        {
            "saved_specs": saved["saved_specs"],
            "saved_materials": saved["saved_materials"],
            "saved_outputs": saved["saved_outputs"],
            "db_specs": saved["db_specs"],
            "db_materials": saved["db_materials"],
            "db_outputs": saved["db_outputs"],
            "sync_run_id": saved["sync_run_id"],
            "db_match": match,
            "message": (
                f"{payload['message']}; в БД: specs={saved['db_specs']}, "
                f"materials={saved['db_materials']}, outputs={saved['db_outputs']}"
                f"{' — OK' if match else ' — РАСХОЖДЕНИЕ'}"
            ),
        }
    )
    return payload


@router.get("/document-analysis/stock-balances")
async def list_aveon_stock_balances(
    _user: DocumentAnalysisUser,
    db: DbSession,
    q: str | None = None,
    warehouse: str | None = None,
    limit: int = 5000,
    offset: int = 0,
    spec_materials_only: bool = True,
):
    """Остатки товаров на складах из БД (после sync из 1С)."""
    from app.services.onec_stock_sync import list_stock_balances_from_db

    return await list_stock_balances_from_db(
        db,
        query=q,
        warehouse=warehouse,
        limit=min(max(limit, 1), 10000),
        offset=max(offset, 0),
        spec_materials_only=spec_materials_only,
    )


@router.get("/document-analysis/resource-specs")
async def list_aveon_resource_specs(
    _user: DocumentAnalysisUser,
    db: DbSession,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    """Список ресурсных спецификаций из БД (после sync)."""
    from app.services.onec_resource_spec_sync import list_resource_specs_from_db

    return await list_resource_specs_from_db(
        db,
        status=status,
        query=q,
        limit=min(max(limit, 1), 1000),
        offset=max(offset, 0),
    )


@router.get("/document-analysis/resource-specs/{ref_key}")
async def get_aveon_resource_spec(
    ref_key: str,
    _user: DocumentAnalysisUser,
    db: DbSession,
):
    """Одна ресурсная спецификация с материалами/выходными изделиями из БД."""
    from app.services.onec_resource_spec_sync import get_resource_spec_from_db

    spec = await get_resource_spec_from_db(db, ref_key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Спецификация не найдена в БД")
    return {"ok": True, "spec": spec}


@router.get("/document-analysis/schedule-snapshot-status")
async def get_aveon_schedule_snapshot_status(_user: DocumentAnalysisUser):
    """Сохранённые базовые версии графиков производства для сравнения при анализе."""
    from app.agents.document_analysis_agent.schedule_snapshot import schedule_snapshot_status

    user_id = getattr(_user, "id", None) if _user is not None else None
    status = schedule_snapshot_status(user_id)
    return {"ok": True, **status}


@router.get("/document-analysis/onec-sync-status")
async def get_aveon_onec_sync_status(_user: DocumentAnalysisUser, db: DbSession):
    """Когда последний раз остатки и спецификации были загружены из 1С в БД."""
    from app.services.onec_db_schema import ensure_onec_agent_tables
    from app.services.onec_production_plan_sync import get_production_plan_sync_status
    from app.services.onec_resource_spec_sync import get_resource_spec_sync_status
    from app.services.onec_stock_sync import get_stock_sync_status

    try:
        await ensure_onec_agent_tables()
        stock = await get_stock_sync_status(db, ensure=False)
        resource_specs = await get_resource_spec_sync_status(db, ensure=False)
        production_plan = await get_production_plan_sync_status(db, ensure=False)
        return {
            "ok": True,
            "stock": stock,
            "resource_specs": resource_specs,
            "production_plan": production_plan,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Не удалось прочитать статус синхронизации 1С: {exc}",
            "stock": {
                "last_sync_at": None,
                "status": "error",
                "saved_count": 0,
                "db_count": 0,
                "error_message": str(exc),
            },
            "resource_specs": {
                "last_sync_at": None,
                "status": "error",
                "specs_count": 0,
                "materials_count": 0,
                "outputs_count": 0,
                "db_specs": 0,
                "db_materials": 0,
                "db_outputs": 0,
                "error_message": str(exc),
            },
            "production_plan": {
                "last_sync_at": None,
                "status": "error",
                "saved_count": 0,
                "db_count": 0,
                "plan_number": "",
                "plan_date": None,
                "error_message": str(exc),
            },
        }


@router.post("/document-analysis/onec-sync-now")
async def run_aveon_onec_sync_now(_user: DocumentAnalysisUser):
    """Ручная синхронизация остатков и спецификаций из 1С (как по расписанию Celery)."""
    from app.services.onec_sync_scheduler import run_onec_sync_with_lock

    result = await run_onec_sync_with_lock(owner="manual_api")
    return {"ok": bool(result.get("ok")), **result}


# TEMP(Aveon Google Sheets probe) — удалить целиком без последствий
@router.post("/document-analysis/temp-google-sheets-probe")
async def temp_aveon_google_sheets_probe(_user: DocumentAnalysisUser):
    """TEMP: проверка доступа к Google Sheets форме. Удалить вместе с кнопкой на фронте."""
    from app.agents.document_analysis_agent.google_sheets_probe import probe_google_sheets

    return await asyncio.to_thread(probe_google_sheets)


# TEMP(Aveon production plan OData probe) — удалить целиком без последствий
@router.post("/document-analysis/temp-production-plan")
async def temp_aveon_production_plan(_user: DocumentAnalysisUser, db: DbSession):
    """TEMP: актуальный план производства из БД после синхронизации 1С."""
    from app.services.onec_production_plan_sync import list_latest_production_plan_from_db

    return await list_latest_production_plan_from_db(db)


@router.get("/document-analysis/google-sheets/status")
async def get_aveon_google_sheets_status(_user: DocumentAnalysisUser):
    """Статус конфигурации Google Sheets Service Account."""
    from app.services.google_sheets_client import (
        get_default_spreadsheet_target,
        get_service_account_email,
        is_configured,
    )

    spreadsheet_id = ""
    sheet_gid = ""
    try:
        spreadsheet_id, sheet_gid = get_default_spreadsheet_target()
    except Exception:
        pass

    return {
        "ok": True,
        "configured": is_configured(),
        "service_account_email": get_service_account_email(),
        "spreadsheet_id": spreadsheet_id,
        "sheet_gid": sheet_gid,
    }


@router.post("/document-analysis/google-sheets/fetch")
async def fetch_aveon_google_sheets(_user: DocumentAnalysisUser):
    """Чтение листа «ИТЦ В РАБОТЕ» через Service Account (полная таблица)."""
    from app.services.google_sheets_client import (
        DEFAULT_SHEET_TITLE,
        fetch_sheet_via_api,
        get_default_spreadsheet_target,
    )

    spreadsheet_id, sheet_gid = get_default_spreadsheet_target()
    result = await asyncio.to_thread(
        fetch_sheet_via_api,
        spreadsheet_id,
        sheet_gid or None,
        sheet_title=DEFAULT_SHEET_TITLE,
        include_values=True,
    )
    if not result.get("ok"):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Не удалось прочитать Google Sheets",
        )
    return result


@router.get("/document-analysis/shipment-schedule/current")
async def get_current_aveon_shipment_schedule(_user: DocumentAnalysisUser, db: DbSession):
    """Текущая активная российская версия графика отгрузок из БД."""
    await ensure_aveon_shipment_schedule_tables()
    active = await AveonShipmentScheduleService(db).get_active_russia()
    return {
        "ok": True,
        "schedule": AveonShipmentScheduleService.serialize_version(active.version if active else None),
    }


@router.get("/document-analysis/shipment-schedule/versions")
async def list_aveon_shipment_schedule_versions(current_user: CurrentUser, db: DbSession):
    """История служебных загрузок и обновлений российского графика отгрузок."""
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    await ensure_aveon_shipment_schedule_tables()
    versions = await AveonShipmentScheduleService(db).list_russia_versions()
    return {
        "ok": True,
        "versions": [AveonShipmentScheduleService.serialize_version(version) for version in versions],
    }


@router.post("/document-analysis/shipment-schedule/russia/upload")
async def upload_aveon_russia_shipment_schedule(
    current_user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
):
    """Служебная загрузка master-графика отгрузок по России."""
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    await ensure_aveon_shipment_schedule_tables()
    filename = file.filename or "russia_shipment_schedule.xlsx"
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Файл должен быть Excel .xlsx/.xlsm")
    raw = await file.read()
    try:
        version = await AveonShipmentScheduleService(db).save_russia_upload(
            filename=filename,
            raw=raw,
            created_by_user_id=current_user.id,
            reason="admin_upload",
        )
        await db.commit()
    except AveonShipmentScheduleError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "ok": True,
        "schedule": AveonShipmentScheduleService.serialize_version(version),
    }


@router.post("/document-analysis/merge-shipment-schedules")
async def merge_shipment_schedule_files(
    _user: DocumentAnalysisUser,
    files: Annotated[list[UploadFile], File(...)],
):
    """Объединение нескольких файлов графика отгрузок (в т.ч. ТАМОЖНЯ/ИТЦ) в один."""
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один файл")

    from app.agents.document_analysis_agent.temp_schedule_merge import merge_schedule_files

    payload: list[tuple[str, bytes]] = []
    for file in files:
        raw = await file.read()
        payload.append((file.filename or "unnamed.xlsx", raw))

    return await merge_schedule_files(payload)


@router.post("/document-analysis/shipment-schedule/preview")
async def preview_shipment_schedule_file(
    _user: DocumentAnalysisUser,
    file: Annotated[UploadFile, File(...)],
):
    """Preview таблицы объединённого графика отгрузок (лист «График»)."""
    from app.agents.document_analysis_agent.temp_schedule_merge import (
        build_merged_schedule_preview_values,
    )

    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Файл пуст")
    preview_values = build_merged_schedule_preview_values(raw)
    return {
        "ok": True,
        "file_name": file.filename or "merged_schedule.xlsx",
        "preview_values": preview_values,
        "row_count": max(len(preview_values) - 1, 0),
    }


class MergedShipmentSnapshotRequest(BaseModel):
    file_name: str = "merged_schedule.xlsx"
    file_base64: str = Field(min_length=1)
    preview_values: list[list[str]] = Field(default_factory=list)
    stats: dict[str, object] | None = None
    source_count: int = 0
    changed_cells: list[dict[str, int]] = Field(default_factory=list)


@router.post("/document-analysis/shipment-schedule/snapshot")
async def save_merged_shipment_snapshot(
    _user: DocumentAnalysisUser,
    payload: MergedShipmentSnapshotRequest,
):
    """Сохраняет объединённый график отгрузок для просмотра после перезагрузки."""
    user_id = getattr(_user, "id", None) if _user is not None else None
    updated = update_merged_shipment_snapshot(
        user_id,
        merged_shipment_schedule={
            "file_name": payload.file_name,
            "file_base64": payload.file_base64,
            "values": payload.preview_values,
            "stats": payload.stats or {},
            "source_count": payload.source_count,
            "changed_cells": payload.changed_cells,
        },
    )
    if updated is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Не удалось сохранить график")
    return {"ok": True}


class ShipmentDateChangeRequest(BaseModel):
    file_name: str = "merged_schedule.xlsx"
    file_base64: str = Field(min_length=1)
    task_key: str | None = None
    manager_name: str | None = None
    task_type: str = ""
    problem: str = ""
    solution: str = ""
    nomenclature: str = ""
    manager_result: str = Field(min_length=1, max_length=4000)


@router.post("/document-analysis/shipment-schedule/apply-manager-date-change")
async def apply_manager_date_change(
    _user: DocumentAnalysisUser,
    db: DbSession,
    payload: ShipmentDateChangeRequest,
):
    """Применяет изменение даты из результата менеджера к объединённому графику отгрузок."""
    await ensure_aveon_shipment_schedule_tables()
    from app.agents.document_analysis_agent.temp_schedule_merge import (
        SUPPLIER_COUNTRY_CHINA,
        SUPPLIER_COUNTRY_RUSSIA,
        apply_manager_date_change_to_schedule,
        merge_schedule_files,
    )

    try:
        raw = base64.b64decode(payload.file_base64)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Некорректный file_base64") from exc

    user_id = getattr(_user, "id", None) if _user is not None else None
    service = AveonShipmentScheduleService(db)
    active_russia = await service.get_active_russia()
    active_version_id = active_russia.version.id if active_russia else None
    idempotency_key = shipment_change_idempotency_key(
        task_key=payload.task_key,
        manager_result=payload.manager_result,
        active_version_id=active_version_id,
        nomenclature=payload.nomenclature,
    )
    existing_event = await service.get_change_event_by_key(idempotency_key)
    if existing_event is not None:
        return {
            "ok": True,
            "applied": False,
            "already_processed": True,
            "persisted": existing_event.status == "persisted_russia",
            "manual_action_required": existing_event.status == "manual_google_required_china",
            "message": existing_event.message or "Изменение уже обработано.",
            "country": existing_event.country,
        }

    result = await apply_manager_date_change_to_schedule(
        raw=raw,
        task_type=payload.task_type,
        problem=payload.problem,
        solution=payload.solution,
        nomenclature=payload.nomenclature,
        manager_result=payload.manager_result,
    )

    if not result.get("applied"):
        return result

    country = str(result.get("country") or "").strip()
    change = result.get("change") or {}
    original_dates = list(change.get("remove_dates") or [])
    add_batches = list(change.get("add_batches") or [])
    quantity = sum(
        float(batch.get("quantity") or 0)
        for batch in add_batches
        if isinstance(batch, dict)
    ) or None
    matched_nomenclature = str(change.get("nomenclature") or payload.nomenclature)
    supplier = str(result.get("supplier") or "") or None

    if country.casefold() == SUPPLIER_COUNTRY_CHINA.casefold():
        new_dates = ", ".join(str(batch.get("date")) for batch in add_batches if isinstance(batch, dict))
        old_dates = ", ".join(original_dates)
        notice = (
            f"После выполнения задания изменилась дата по номенклатуре "
            f"«{matched_nomenclature}» с поставщиком Китай. "
            f"Измените в Google форме: старая дата {old_dates or 'не определена'}, "
            f"новая дата {new_dates or 'не определена'}."
        )
        await service.record_change_event(
            idempotency_key=idempotency_key,
            status="manual_google_required_china",
            schedule_version_id=active_version_id,
            manager_user_id=user_id,
            manager_name=payload.manager_name,
            task_key=payload.task_key,
            task_type=payload.task_type,
            nomenclature=matched_nomenclature,
            country=country,
            supplier=supplier,
            original_dates=original_dates,
            add_batches=add_batches,
            quantity=quantity,
            manager_result=payload.manager_result,
            message=notice,
            metadata={"local_only": True, "changed_cells": result.get("changed_cells") or []},
        )
        await db.commit()
        return {
            **result,
            "persisted": False,
            "manual_action_required": True,
            "message": notice,
        }

    if not country or country.casefold() != SUPPLIER_COUNTRY_RUSSIA.casefold():
        message = (
            f"Страна поставщика «{country}» не поддерживает автоматическое сохранение в БД."
            if country
            else "Страна поставщика не определена, график не сохранён в БД."
        )
        await service.record_change_event(
            idempotency_key=idempotency_key,
            status="country_unknown",
            schedule_version_id=active_version_id,
            manager_user_id=user_id,
            manager_name=payload.manager_name,
            task_key=payload.task_key,
            task_type=payload.task_type,
            nomenclature=matched_nomenclature,
            country=country,
            supplier=supplier,
            original_dates=original_dates,
            add_batches=add_batches,
            quantity=quantity,
            manager_result=payload.manager_result,
            message=message,
            metadata={"changed_cells": result.get("changed_cells") or []},
        )
        await db.commit()
        return {**result, "persisted": False, "manual_action_required": True, "message": message}

    if active_russia is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Российский график отгрузок не загружен в БД.",
        )

    russia_result = await apply_manager_date_change_to_schedule(
        raw=active_russia.raw,
        task_type=payload.task_type,
        problem=payload.problem,
        solution=payload.solution,
        nomenclature=payload.nomenclature,
        manager_result=payload.manager_result,
    )
    if not russia_result.get("applied") or not russia_result.get("file_base64"):
        return {
            **result,
            "persisted": False,
            "message": russia_result.get("message") or result.get("message"),
        }

    updated_russia_raw = base64.b64decode(str(russia_result["file_base64"]))
    next_version = await service.save_russia_version(
        filename=str(russia_result.get("file_name") or active_russia.version.file_name),
        raw=updated_russia_raw,
        created_by_user_id=user_id,
        reason="manager_result",
        preview_values=russia_result.get("preview_values") or [],
        stats={"source": "manager_result"},
        changed_cells=russia_result.get("changed_cells") or [],
        source_type="manager_result",
    )
    merged_result = await merge_schedule_files(
        [(next_version.file_name, updated_russia_raw)],
        include_google_sheets=True,
        include_merged_inputs=True,
    )
    if not merged_result.get("ok") or not merged_result.get("file_base64"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(merged_result.get("message") or "Не удалось пересобрать объединённый график"),
        )

    await service.record_change_event(
        idempotency_key=idempotency_key,
        status="persisted_russia",
        schedule_version_id=active_russia.version.id,
        next_schedule_version_id=next_version.id,
        manager_user_id=user_id,
        manager_name=payload.manager_name,
        task_key=payload.task_key,
        task_type=payload.task_type,
        nomenclature=matched_nomenclature,
        country=country or SUPPLIER_COUNTRY_RUSSIA,
        supplier=supplier,
        original_dates=original_dates,
        add_batches=add_batches,
        quantity=quantity,
        manager_result=payload.manager_result,
        message=str(result.get("message") or "График России обновлён."),
        metadata={"changed_cells": russia_result.get("changed_cells") or []},
    )
    update_merged_shipment_snapshot(
        user_id,
        merged_shipment_schedule={
            "file_name": merged_result.get("file_name") or payload.file_name,
            "file_base64": merged_result.get("file_base64"),
            "values": merged_result.get("preview_values") or [],
            "stats": merged_result.get("stats") or {},
            "source_count": 2,
            "changed_cells": russia_result.get("changed_cells") or [],
        },
    )
    await db.commit()
    return {
        **merged_result,
        "applied": True,
        "persisted": True,
        "manual_action_required": False,
        "country": country or SUPPLIER_COUNTRY_RUSSIA,
        "change": russia_result.get("change") or change,
        "changed_cells": russia_result.get("changed_cells") or [],
    }


@router.post("/document-analysis/prune-production-schedules")
async def prune_production_schedule_files(
    _user: DocumentAnalysisUser,
    files: Annotated[list[UploadFile], File(...)],
):
    """Оставить последнюю версию графика производства, остальные пометить к удалению."""
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один файл")

    from app.agents.document_analysis_agent.production_schedule_diff import (
        select_latest_production_schedules,
    )

    payload: list[tuple[str, bytes]] = []
    for file in files:
        raw = await file.read()
        payload.append((file.filename or "unnamed.xlsx", raw))

    return select_latest_production_schedules(payload, keep=1)


@router.post("/document-analysis/prune-detailed-schedules")
async def prune_detailed_schedule_files(
    _user: DocumentAnalysisUser,
    files: Annotated[list[UploadFile], File(...)],
):
    """Оставить последнюю версию детального графика производства."""
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один файл")

    from app.agents.document_analysis_agent.detailed_schedule_diff import (
        select_latest_detailed_schedules,
    )

    payload: list[tuple[str, bytes]] = []
    for file in files:
        raw = await file.read()
        payload.append((file.filename or "unnamed.xlsx", raw))

    return select_latest_detailed_schedules(payload, keep=1)


@router.post("/document-analysis/analyze-excel")
async def analyze_document_excel_files(
    _user: DocumentAnalysisUser,
    db: DbSession,
    files: Annotated[list[UploadFile], File(...)],
    compact: bool = False,
    response_format: str = "json",
):
    """Анализ Excel.

    response_format=json (web) — meta + file_base64 в JSON.
    response_format=zip (mobile) — ZIP: meta.json + result.xlsx [+ shift], без base64.
    """
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один Excel-файл")

    uploaded: list[UploadedWorkbook] = []
    for file in files:
        filename = file.filename or "workbook.xlsx"
        if not filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Файл {filename} должен быть в формате .xlsx, .xlsm или .xls",
            )
        uploaded.append(UploadedWorkbook(filename=filename, content=await file.read()))

    user_id = getattr(_user, "id", None) if _user is not None else None
    analyzed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    refresh_inputs = _build_dashboard_refresh_inputs(uploaded)

    uploaded, shipment_merge = await _prepare_aveon_uploaded_with_server_shipment(uploaded, db)

    try:
        result = await analyze_aveon_excel_files(uploaded, db=db, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Не удалось проанализировать Excel: {exc}",
        ) from exc

    shift_name = (
        result.shift_assignment_file_name
        or "сменное_задание_закупки.xlsx"
    )
    logistics_payload = {
        "as_of": result.logistics_risks.as_of if result.logistics_risks else None,
        "stages": [
            {
                "key": stage.key,
                "label": stage.label,
                "items": [
                    {
                        "nomenclature": item.nomenclature,
                        "supplier": item.supplier,
                        "quantity": item.quantity,
                        "moscow_date": item.moscow_date,
                        "milestone_date": item.milestone_date,
                        "sheet": item.sheet,
                        "window_start": item.window_start,
                        "window_end": item.window_end,
                        "days_remaining": item.days_remaining,
                        "risk_ratio": item.risk_ratio,
                        "risk_level": item.risk_level,
                    }
                    for item in stage.items
                ],
            }
            for stage in (result.logistics_risks.stages if result.logistics_risks else [])
        ],
    }
    meta: dict = {
        "source": result.source,
        "roles": [
            {"filename": filename, "role": role}
            for filename, role in sorted(result.roles.items())
        ],
        "production_schedule_files": result.production_schedule_files,
        "production_schedule_products": result.production_schedule_products,
        "detailed_production_schedule_files": result.detailed_production_schedule_files,
        "detailed_schedule_month": result.detailed_schedule_month,
        "daily_demand_nonzero_count": sum(
            1
            for row in result.merged_nomenclatures
            if any(value > 0 for value in row.daily_demand.values())
        ),
        "daily_demand_fact_nonzero_count": sum(
            1
            for row in result.merged_nomenclatures
            if any(value > 0 for value in row.daily_demand_fact.values())
        ),
        "material_usages_count": len(result.material_usages),
        "merged_nomenclatures_count": len(result.merged_nomenclatures),
        "price_matched_count": sum(
            1
            for row in result.merged_nomenclatures
            if row.price_match not in ("", "unmatched")
        ),
        "stock_files": result.stock_files,
        "stock_matched_count": sum(
            1
            for row in result.merged_nomenclatures
            if row.stock_match not in ("", "unmatched")
        ),
        "shipment_files": result.shipment_files,
        "receipts_nonzero_count": sum(
            1
            for row in result.merged_nomenclatures
            if any(value > 0 for value in row.monthly_receipts.values())
        ),
        "forecast_deficit_count": sum(
            1
            for row in result.merged_nomenclatures
            if any(value < 0 for value in row.monthly_forecast.values())
        ),
        "logistics_risks": logistics_payload,
        "file_name": "result.xlsx",
        "shift_assignment_file_name": shift_name,
        "shift_assignment_values": result.shift_assignment_values,
        "shift_assignment_row_priorities": result.shift_assignment_row_priorities,
        "shift_assignment_row_kinds": result.shift_assignment_row_kinds,
        "shift_assignment_meta": result.shift_assignment_meta,
        "schedule_diff_has_changes": result.schedule_diff_has_changes,
        "schedule_diff_changed_months": result.schedule_diff_changed_months,
        "schedule_diff_changed_cells": result.schedule_diff_changed_cells,
        "schedule_diff_file_name": result.schedule_diff_file_name,
        "schedule_diff_old_version": result.schedule_diff_old_version,
        "schedule_diff_new_version": result.schedule_diff_new_version,
        "schedule_diff_message": result.schedule_diff_message,
        "schedule_baseline_saved": result.schedule_baseline_saved,
        "schedule_compared_with_saved": result.schedule_compared_with_saved,
        "detailed_diff_has_changes": result.detailed_diff_has_changes,
        "detailed_diff_changed_dates": result.detailed_diff_changed_dates,
        "detailed_diff_changed_cells": result.detailed_diff_changed_cells,
        "detailed_diff_file_name": result.detailed_diff_file_name,
        "detailed_diff_old_version": result.detailed_diff_old_version,
        "detailed_diff_new_version": result.detailed_diff_new_version,
        "detailed_diff_message": result.detailed_diff_message,
        "detailed_baseline_saved": result.detailed_baseline_saved,
        "detailed_compared_with_saved": result.detailed_compared_with_saved,
        "coverage_dashboard": result.coverage_dashboard,
    }
    # compact / zip: без тяжёлых массивов, которые мобилка не использует.
    if not compact and response_format != "zip":
        meta["production_schedule_plans"] = [
            {
                "product": plan.product,
                "monthly_qty": plan.monthly_qty,
            }
            for plan in result.production_schedule_plans
        ]
        meta["product_spec_links"] = [
            {
                "schedule_product": link.schedule_product,
                "nomenclature": link.nomenclature,
                "spec_sheet": link.spec_sheet,
                "status": link.status,
                "reason": link.reason,
            }
            for link in result.product_spec_links
        ]

    shift_b64 = (
        base64.b64encode(result.shift_assignment_xlsx_bytes).decode("ascii")
        if result.shift_assignment_xlsx_bytes
        else None
    )
    task_dashboard_payload: dict | None = None
    shift_assignment_payload: dict | None = None
    merged_shipment_payload: dict | None = None
    for wb in uploaded:
        name_lower = wb.filename.lower()
        if "merged_schedule" in name_lower or name_lower == "merged_schedule.xlsx":
            shipment_b64 = base64.b64encode(wb.content).decode("ascii")
            from app.agents.document_analysis_agent.temp_schedule_merge import (
                build_merged_schedule_preview_values,
            )

            preview_values = build_merged_schedule_preview_values(wb.content)
            header_len = len(preview_values[0]) if preview_values else 0
            merged_shipment_payload = {
                "file_name": wb.filename,
                "file_base64": shipment_b64,
                "values": shipment_merge.get("preview_values") or preview_values,
                "stats": shipment_merge.get("stats") or {
                    "nomenclature_total": max(len(preview_values) - 1, 0),
                    "date_columns": max(header_len - 12, 0),
                },
                "source_count": 2,
            }
            break
    if result.shift_assignment_values:
        task_dashboard_payload = {
            "values": result.shift_assignment_values,
            "row_priorities": result.shift_assignment_row_priorities,
            "row_kinds": result.shift_assignment_row_kinds,
            "meta": result.shift_assignment_meta,
            "result_texts": {},
            "result_evals": {},
        }
        if shift_b64:
            shift_assignment_payload = {
                "valid_date": today_msk_iso(),
                "file_name": result.shift_assignment_file_name,
                "file_base64": shift_b64,
            }
    had_valid_shift_today = False
    if result.shift_assignment_values and user_id is not None:
        had_valid_shift_today = snapshot_had_valid_shift_today(user_id)
    try:
        save_dashboard_snapshot(
            user_id,
            logistics_risks=logistics_payload,
            analyzed_at=analyzed_at,
            task_dashboard=task_dashboard_payload,
            shift_assignment=shift_assignment_payload,
            merged_shipment_schedule=merged_shipment_payload,
            coverage_dashboard=result.coverage_dashboard,
            meta={
                "source": result.source,
                "stock_files": result.stock_files,
                "shipment_files": result.shipment_files,
                "merged_nomenclatures_count": len(result.merged_nomenclatures),
                "forecast_deficit_count": meta["forecast_deficit_count"],
            },
            refresh_inputs=refresh_inputs,
            dashboard_date_msk=today_msk_iso(),
            refresh_status="manual",
        )
    except OSError:
        # дашборд не критичен для ответа анализа
        pass
    else:
        if result.shift_assignment_values and not had_valid_shift_today:
            manager_name = resolve_shift_manager_name(
                email=getattr(_user, "email", None) if _user is not None else None,
                full_name=getattr(_user, "full_name", None) if _user is not None else None,
            )
            if manager_name:
                shift_meta = result.shift_assignment_meta or {}
                shift_attachment = (
                    ShiftCompletionAttachment(
                        filename=shift_name,
                        content=result.shift_assignment_xlsx_bytes,
                    )
                    if result.shift_assignment_xlsx_bytes
                    else None
                )
                try:
                    sent_to = await send_shift_start_email(
                        manager_name=manager_name,
                        region_label=SHIFT_MANAGER_REGIONS.get(manager_name, ""),
                        shift_date=date.fromisoformat(today_msk_iso()),
                        summary=ShiftStartSummary(
                            total=int(shift_meta.get("task_count") or 0),
                            urgent=int(shift_meta.get("urgent_count") or 0)
                            + int(shift_meta.get("today_count") or 0),
                            week=int(shift_meta.get("week_count") or 0),
                        ),
                        week_period=str(shift_meta.get("week_period") or ""),
                        attachment=shift_attachment,
                    )
                    meta["shift_start_email_sent_to"] = sent_to
                except FeedbackEmailError as exc:
                    logger.warning("shift_start_email_failed", error=str(exc))
                except Exception as exc:
                    logger.warning("shift_start_email_failed", error=str(exc))
    meta["dashboard_analyzed_at"] = analyzed_at

    if response_format == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "meta.json",
                json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
            )
            if result.result_xlsx_bytes:
                archive.writestr("result.xlsx", result.result_xlsx_bytes)
            if result.shift_assignment_xlsx_bytes:
                archive.writestr("shift_assignment.xlsx", result.shift_assignment_xlsx_bytes)
            if result.schedule_diff_xlsx_bytes:
                archive.writestr(
                    result.schedule_diff_file_name or "schedule_diff.xlsx",
                    result.schedule_diff_xlsx_bytes,
                )
            if result.detailed_diff_xlsx_bytes:
                archive.writestr(
                    result.detailed_diff_file_name or "detailed_diff.xlsx",
                    result.detailed_diff_xlsx_bytes,
                )
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=\"aveon_analysis.zip\"",
                "X-Aveon-Response-Format": "zip",
            },
        )

    meta["file_base64"] = (
        base64.b64encode(result.result_xlsx_bytes).decode("ascii")
        if result.result_xlsx_bytes
        else None
    )
    meta["shift_assignment_file_base64"] = shift_b64
    meta["schedule_diff_file_base64"] = (
        base64.b64encode(result.schedule_diff_xlsx_bytes).decode("ascii")
        if result.schedule_diff_xlsx_bytes
        else None
    )
    meta["detailed_diff_file_base64"] = (
        base64.b64encode(result.detailed_diff_xlsx_bytes).decode("ascii")
        if result.detailed_diff_xlsx_bytes
        else None
    )
    return meta


class ShiftResultEvaluateRequest(BaseModel):
    task_type: str = ""
    problem: str = ""
    solution: str = ""
    nomenclature: str = ""
    manager_result: str = Field(min_length=1, max_length=4000)


class ShiftResultEvaluateResponse(BaseModel):
    status: str
    comment: str = ""
    source: str = "lm_studio"


@router.post(
    "/document-analysis/shift-assignment/evaluate-result",
    response_model=ShiftResultEvaluateResponse,
)
async def evaluate_shift_assignment_result(
    _user: DocumentAnalysisUser,
    payload: ShiftResultEvaluateRequest,
):
    """Оценка ответа менеджера в сменном задании через LM Studio."""
    from app.agents.document_analysis_agent.shift_assignment import evaluate_manager_result

    try:
        status, comment, source = await evaluate_manager_result(
            task_type=payload.task_type,
            problem=payload.problem,
            solution=payload.solution,
            nomenclature=payload.nomenclature,
            manager_result=payload.manager_result,
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Не удалось оценить результат: {exc}",
        ) from exc

    return ShiftResultEvaluateResponse(status=status, comment=comment, source=source)


class ShiftResultSuggestRequest(BaseModel):
    task_type: str = ""
    problem: str = ""
    solution: str = ""
    nomenclature: str = ""
    draft: str = Field(default="", max_length=4000)


class ShiftResultSuggestResponse(BaseModel):
    suggestion: str
    source: str = "lm_studio"


@router.post(
    "/document-analysis/shift-assignment/suggest-result",
    response_model=ShiftResultSuggestResponse,
)
async def suggest_shift_assignment_result(
    _user: DocumentAnalysisUser,
    payload: ShiftResultSuggestRequest,
):
    """Подсказка формулировки результата менеджера через LM Studio."""
    from app.agents.document_analysis_agent.shift_assignment import suggest_manager_result

    try:
        suggestion, source = await suggest_manager_result(
            task_type=payload.task_type,
            problem=payload.problem,
            solution=payload.solution,
            nomenclature=payload.nomenclature,
            draft=payload.draft,
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Не удалось получить подсказку: {exc}",
        ) from exc

    return ShiftResultSuggestResponse(suggestion=suggestion, source=source)


class ShiftTaskProgressRequest(BaseModel):
    result_texts: dict[str, str] = Field(default_factory=dict)
    result_evals: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post("/document-analysis/shift-assignment/progress")
async def save_shift_assignment_progress(
    _user: DocumentAnalysisUser,
    payload: ShiftTaskProgressRequest,
):
    """Сохраняет прогресс по заданиям (результаты менеджера) до следующего анализа."""
    user_id = getattr(_user, "id", None) if _user is not None else None
    updated = update_task_progress(
        user_id,
        result_texts=payload.result_texts,
        result_evals=payload.result_evals,
    )
    if updated is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Нет сохранённого дашборда по заданиям — сначала выполните анализ",
        )
    return {"ok": True}


class ShiftCompletionMetaPayload(BaseModel):
    as_of: str | None = None
    week_period: str | None = None
    week_in_period: bool | None = None


class ShiftCompletionStatsPayload(BaseModel):
    total: int = 0
    resolved: int = 0
    incomplete: int = 0
    partial: int = 0
    notResolved: int | None = None
    not_resolved: int | None = None
    active: int = 0

    @property
    def not_resolved_value(self) -> int:
        return self.not_resolved if self.not_resolved is not None else (self.notResolved or 0)


class ShiftCompletionTaskPayload(BaseModel):
    key: str = Field(min_length=1, max_length=600)
    task_type: str = ""
    nomenclature: str = ""
    problem: str = ""
    solution: str = ""
    priority: str = ""
    deadline: str = ""
    deficit: str = ""
    status: str = ""
    result_text: str = ""
    eval_comment: str | None = None
    reason: str | None = None


class ShiftCompletionRequest(BaseModel):
    report_date: date
    manager_name: str = Field(min_length=1, max_length=255)
    meta: ShiftCompletionMetaPayload | None = None
    stats: ShiftCompletionStatsPayload
    tasks: list[ShiftCompletionTaskPayload] = Field(default_factory=list)
    incomplete_reasons: dict[str, str] = Field(default_factory=dict)


def _empty_shift_completion_stats() -> dict[str, int]:
    return {
        "total": 0,
        "resolved": 0,
        "incomplete": 0,
        "partial": 0,
        "not_resolved": 0,
        "active": 0,
        "resolved_percent": 0,
    }


async def _shift_manager_user_ids(db: DbSession) -> dict[str, uuid.UUID]:
    emails = list(SHIFT_MANAGER_EMAILS.values())
    if not emails:
        return {}
    result = await db.execute(select(User.id, User.email).where(User.email.in_(emails)))
    by_email = {str(row.email or "").lower(): row.id for row in result.all()}
    return {
        name: by_email[email.lower()]
        for name, email in SHIFT_MANAGER_EMAILS.items()
        if email.lower() in by_email
    }


def _shift_completion_read_stats(
    reports: list[ShiftCompletionReport],
    *,
    report_date: date,
    include_roster: bool = True,
    manager_user_ids: dict[str, uuid.UUID] | None = None,
) -> dict:
    reports_by_name = {item.manager_name: item for item in reports}
    roster = list(SHIFT_MANAGER_ROSTER) if include_roster else sorted(reports_by_name.keys())
    manager_ids = manager_user_ids or {}

    managers = []
    total = 0
    resolved = 0
    incomplete = 0
    partial = 0
    not_resolved = 0
    active = 0
    submitted_count = 0
    in_progress_count = 0

    for name in roster:
        report = reports_by_name.get(name)
        region_label = SHIFT_MANAGER_REGIONS.get(name, "")
        if report:
            submitted_count += 1
            stats = report.stats_json or {}
            manager_total = int(stats.get("total") or 0)
            manager_resolved = int(stats.get("resolved") or 0)
            manager_incomplete = int(stats.get("incomplete") or 0)
            manager_partial = int(stats.get("partial") or 0)
            manager_not_resolved = int(stats.get("not_resolved") or 0)
            manager_active = int(stats.get("active") or 0)
            tasks = report.tasks_json or []
            managers.append(
                {
                    "id": str(report.id),
                    "manager_name": report.manager_name,
                    "report_date": report.report_date.isoformat(),
                    "report_status": "submitted",
                    "region_label": region_label,
                    "stats": {
                        "total": manager_total,
                        "resolved": manager_resolved,
                        "incomplete": manager_incomplete,
                        "partial": manager_partial,
                        "not_resolved": manager_not_resolved,
                        "active": manager_active,
                        "resolved_percent": round((manager_resolved / manager_total) * 100)
                        if manager_total
                        else 0,
                    },
                    "tasks": tasks,
                    "incomplete_tasks": [task for task in tasks if task.get("status") != "resolved"],
                    "email_sent_to": report.email_sent_to,
                    "email_sent_at": report.email_sent_at.isoformat() if report.email_sent_at else None,
                }
            )
            total += manager_total
            resolved += manager_resolved
            incomplete += manager_incomplete
            partial += manager_partial
            not_resolved += manager_not_resolved
            active += manager_active
            continue

        live_report = resolve_manager_live_report(
            name,
            report_date,
            manager_user_id=manager_ids.get(name),
        )
        if live_report is not None:
            in_progress_count += 1
            live_stats = live_report.get("stats") or {}
            managers.append(live_report)
            total += int(live_stats.get("total") or 0)
            resolved += int(live_stats.get("resolved") or 0)
            incomplete += int(live_stats.get("incomplete") or 0)
            partial += int(live_stats.get("partial") or 0)
            not_resolved += int(live_stats.get("not_resolved") or 0)
            active += int(live_stats.get("active") or 0)
            continue

        managers.append(
            {
                "id": f"missing:{name}",
                "manager_name": name,
                "report_date": report_date.isoformat(),
                "report_status": "missing",
                "region_label": region_label,
                "stats": _empty_shift_completion_stats(),
                "tasks": [],
                "incomplete_tasks": [],
                "email_sent_to": "",
                "email_sent_at": None,
            }
        )

    missing_count = len(roster) - submitted_count - in_progress_count
    return {
        "total": total,
        "resolved": resolved,
        "incomplete": incomplete,
        "partial": partial,
        "not_resolved": not_resolved,
        "active": active,
        "resolved_percent": round((resolved / total) * 100) if total else 0,
        "roster_total": len(roster),
        "submitted_count": submitted_count,
        "in_progress_count": in_progress_count,
        "missing_count": missing_count,
        "live_mode": report_date.isoformat() == today_msk_iso() and in_progress_count > 0,
        "managers": managers,
    }


@router.get("/document-analysis/shift-assignment/completion-dates")
async def get_shift_completion_dates(
    _user: DocumentAnalysisUser,
    db: DbSession,
    limit: int = 120,
):
    """Даты, за которые менеджеры завершали смены (для выбора в дашборде руководителя)."""
    await ensure_shift_completion_tables()
    safe_limit = max(1, min(limit, 365))
    result = await db.execute(
        select(
            ShiftCompletionReport.report_date,
            func.count(ShiftCompletionReport.id).label("reports_count"),
        )
        .group_by(ShiftCompletionReport.report_date)
        .order_by(ShiftCompletionReport.report_date.desc())
        .limit(safe_limit)
    )
    rows = result.all()
    roster_total = len(SHIFT_MANAGER_ROSTER)
    dates = [
        {
            "report_date": row.report_date.isoformat(),
            "reports_count": int(row.reports_count or 0),
            "roster_total": roster_total,
            "has_live": False,
        }
        for row in rows
    ]
    today = today_msk_iso()
    if has_any_live_shift_for_today() and not any(entry["report_date"] == today for entry in dates):
        dates.insert(
            0,
            {
                "report_date": today,
                "reports_count": 0,
                "roster_total": roster_total,
                "has_live": True,
            },
        )
    elif has_any_live_shift_for_today():
        for entry in dates:
            if entry["report_date"] == today:
                entry["has_live"] = True
                break
    return {
        "ok": True,
        "today": today,
        "roster_total": roster_total,
        "dates": dates,
    }


@router.get("/document-analysis/shift-assignment/completion-reports")
@router.get("/document-analysis/shift-assignment/completion-dashboard")
async def get_shift_completion_dashboard(
    _user: DocumentAnalysisUser,
    db: DbSession,
    report_date: date | None = None,
    manager_name: str | None = None,
    manager_user_id: uuid.UUID | None = None,
):
    """Сводка руководителя по завершённым сменам менеджеров."""
    await ensure_shift_completion_tables()
    target_date = report_date or date.fromisoformat(today_msk_iso())
    stmt = select(ShiftCompletionReport).where(ShiftCompletionReport.report_date == target_date)
    if manager_name:
        stmt = stmt.where(ShiftCompletionReport.manager_name == manager_name.strip())
    if manager_user_id:
        stmt = stmt.where(ShiftCompletionReport.manager_user_id == manager_user_id)
    result = await db.execute(
        stmt.order_by(
            ShiftCompletionReport.manager_name.asc(),
            ShiftCompletionReport.email_sent_at.desc(),
        )
    )
    reports = list(result.scalars().all())
    manager_user_ids = await _shift_manager_user_ids(db)
    summary = _shift_completion_read_stats(
        reports,
        report_date=target_date,
        manager_user_ids=manager_user_ids,
    )
    return {
        "ok": True,
        "report_date": target_date.isoformat(),
        "live_mode": summary.get("live_mode", False),
        "summary": {
            key: value
            for key, value in summary.items()
            if key
            not in {
                "managers",
                "roster_total",
                "submitted_count",
                "in_progress_count",
                "missing_count",
                "live_mode",
            }
        },
        "roster": {
            "total": summary["roster_total"],
            "submitted": summary["submitted_count"],
            "in_progress": summary["in_progress_count"],
            "missing": summary["missing_count"],
        },
        "managers": summary["managers"],
    }


@router.post("/document-analysis/shift-assignment/complete")
async def complete_shift_assignment(
    _user: DocumentAnalysisUser,
    db: DbSession,
    payload: ShiftCompletionRequest,
):
    """Сохраняет дневную статистику менеджера и отправляет отчёт руководителю."""
    if not payload.tasks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет заданий за сегодня для отчёта")

    incomplete_tasks = [task for task in payload.tasks if task.status != "resolved"]
    missing_reasons = [
        task.nomenclature or task.key
        for task in incomplete_tasks
        if not (payload.incomplete_reasons.get(task.key) or task.reason or "").strip()
    ]
    if missing_reasons:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Заполните основание невыполнения: " + "; ".join(missing_reasons[:5]),
        )

    await ensure_shift_completion_tables()

    user_id = getattr(_user, "id", None) if _user is not None else None
    manager_name = payload.manager_name.strip()
    snapshot = load_dashboard_snapshot(user_id)
    shift_snapshot = (snapshot or {}).get("shift_assignment") or {}
    shift_attachment: ShiftCompletionAttachment | None = None
    shift_file_base64 = shift_snapshot.get("file_base64")
    if isinstance(shift_file_base64, str) and shift_file_base64:
        try:
            shift_attachment = ShiftCompletionAttachment(
                filename=str(shift_snapshot.get("file_name") or "сменное_задание_закупки.xlsx"),
                content=base64.b64decode(shift_file_base64),
            )
        except Exception:
            shift_attachment = None

    result = await db.execute(
        select(ShiftCompletionReport).where(
            ShiftCompletionReport.report_date == payload.report_date,
            ShiftCompletionReport.manager_name == manager_name,
        )
    )
    report = result.scalar_one_or_none()
    if report is None and user_id is not None:
        result = await db.execute(
            select(ShiftCompletionReport).where(
                ShiftCompletionReport.report_date == payload.report_date,
                ShiftCompletionReport.manager_user_id == user_id,
            )
        )
        report = result.scalar_one_or_none()

    normalized_reasons = {
        task.key: (payload.incomplete_reasons.get(task.key) or task.reason or "").strip()
        for task in incomplete_tasks
    }
    tasks_json = [
        {
            "key": task.key,
            "task_type": task.task_type,
            "nomenclature": task.nomenclature,
            "problem": task.problem,
            "solution": task.solution,
            "priority": task.priority,
            "deadline": task.deadline,
            "deficit": task.deficit,
            "status": task.status,
            "result_text": task.result_text,
            "eval_comment": task.eval_comment,
            "reason": normalized_reasons.get(task.key, ""),
        }
        for task in payload.tasks
    ]
    stats_json = {
        "total": payload.stats.total,
        "resolved": payload.stats.resolved,
        "incomplete": payload.stats.incomplete,
        "partial": payload.stats.partial,
        "not_resolved": payload.stats.not_resolved_value,
        "active": payload.stats.active,
        "meta": payload.meta.model_dump() if payload.meta else {},
    }

    sent_to = await send_shift_completion_email(
        manager_name=manager_name,
        report_date=payload.report_date,
        stats=ShiftCompletionStats(
            total=payload.stats.total,
            resolved=payload.stats.resolved,
            incomplete=payload.stats.incomplete,
            partial=payload.stats.partial,
            not_resolved=payload.stats.not_resolved_value,
            active=payload.stats.active,
        ),
        tasks=[
            ShiftCompletionTaskView(
                task_type=task.task_type,
                nomenclature=task.nomenclature,
                priority=task.priority,
                deadline=task.deadline,
                deficit=task.deficit,
                status=task.status,
                result_text=task.result_text,
                reason=normalized_reasons.get(task.key, ""),
            )
            for task in payload.tasks
        ],
        attachment=shift_attachment,
        recipient=settings.SHIFT_COMPLETION_RECIPIENT_EMAIL or settings.SHIFT_REPORT_RECIPIENT_EMAIL,
    )
    sent_at = datetime.now(timezone.utc)

    if report is None:
        report = ShiftCompletionReport(
            manager_user_id=user_id,
            manager_name=manager_name,
            report_date=payload.report_date,
            stats_json=stats_json,
            tasks_json=tasks_json,
            incomplete_reasons_json=normalized_reasons,
            email_sent_to=sent_to,
            email_sent_at=sent_at,
        )
        db.add(report)
    else:
        report.manager_user_id = user_id
        report.manager_name = manager_name
        report.stats_json = stats_json
        report.tasks_json = tasks_json
        report.incomplete_reasons_json = normalized_reasons
        report.email_sent_to = sent_to
        report.email_sent_at = sent_at

    await db.flush()
    return {
        "ok": True,
        "id": str(report.id),
        "sent_to": sent_to,
        "email_sent_at": sent_at.isoformat(),
    }


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(db: DbSession, agent_id: uuid.UUID):
    agent = await AgentService(db).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    return await _agent_read(db, agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(db: DbSession, agent_id: uuid.UUID, data: AgentUpdate):
    service = AgentService(db)
    agent = await service.get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    updated = await service.update(agent, data)
    return await _agent_read(db, updated)


@router.get("/{agent_id}/access", response_model=AgentAccessManagementRead)
async def list_agent_access(agent_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    try:
        department_grants, user_grants = await AgentAccessService(db).list_access(agent_id)
        return AgentAccessManagementRead(
            department_grants=[AgentDepartmentGrantRead.model_validate(item) for item in department_grants],
            user_grants=[AgentUserGrantRead.model_validate(item) for item in user_grants],
        )
    except AgentAccessServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put("/{agent_id}/access", response_model=AgentAccessManagementRead)
async def replace_agent_access(
    agent_id: uuid.UUID,
    payload: AgentAccessUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    try:
        department_grants, user_grants = await AgentAccessService(db).replace_access(
            agent_id,
            payload,
            current_user=current_user,
        )
        await AuditService(db).log(
            action="agents.access_replaced",
            actor_id=current_user.id,
            resource_type="agent",
            resource_id=str(agent_id),
            payload={
                "department_grants": len(department_grants),
                "user_grants": len(user_grants),
            },
        )
        await db.commit()
        return AgentAccessManagementRead(
            department_grants=[AgentDepartmentGrantRead.model_validate(item) for item in department_grants],
            user_grants=[AgentUserGrantRead.model_validate(item) for item in user_grants],
        )
    except AgentAccessServiceError as exc:
        await db.rollback()
        message = str(exc)
        status_code = status.HTTP_403_FORBIDDEN if "администратор" in message else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code, message) from exc


@router.post("/{agent_id}/icon", response_model=AgentRead)
async def upload_agent_icon(
    db: DbSession,
    current_user: CurrentUser,
    agent_id: uuid.UUID,
    file: Annotated[UploadFile, File(...)],
):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")

    agent = await AgentService(db).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")

    uploaded_object_name: str | None = None
    try:
        updated = await AgentIconService(db).upload_icon(agent, file)
        uploaded_object_name = updated.icon_object_name
        await AuditService(db).log(
            action="agents.icon_upload",
            actor_id=current_user.id,
            resource_type="agent",
            resource_id=str(agent.id),
        )
        await db.commit()
    except AvatarValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except MinioObjectError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        if uploaded_object_name:
            try:
                AgentIconService(db).storage.delete(uploaded_object_name)
            except MinioObjectError:
                pass
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Иконка загружена в MinIO, но не сохранилась в базе данных.",
        ) from exc

    return await _agent_read(db, updated)
