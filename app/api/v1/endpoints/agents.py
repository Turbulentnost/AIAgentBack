from __future__ import annotations

import asyncio
import base64
import io
import json
import smtplib
import uuid
import zipfile
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, DocumentAnalysisUser
from app.agents.document_analysis_agent.excel_service import (
    UploadedWorkbook,
    analyze_aveon_excel_files,
    classify_aveon_excel_files,
)
from app.agents.document_analysis_agent.dashboard_snapshot import (
    clear_dashboard_snapshot,
    load_dashboard_snapshot,
    save_dashboard_snapshot,
    today_msk_iso,
    update_merged_shipment_snapshot,
    update_task_progress,
)
from app.core.config import settings
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
from app.services.agent_access_service import AgentAccessService, AgentAccessServiceError
from app.services.agent_icon_service import AgentIconService
from app.services.agent_service import AgentService
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.developer_feedback_email import (
    FeedbackAttachment,
    FeedbackEmailError,
    is_feedback_send_available,
    send_developer_feedback_email,
)
from app.services.shift_completion_email import (
    ShiftCompletionAttachment,
    ShiftCompletionStats,
    ShiftCompletionTaskView,
    send_shift_completion_email,
)
from app.services.shift_completion_schema import ensure_shift_completion_tables
from app.services.meeting_permission import append_meeting_agent_for_office_management
from app.services.nd_control_permission import append_nd_control_agent_for_quality_deputy
from app.services.permission_service import PermissionService
from app.services.procurement_permission import (
    append_production_preparation_engineer_agent,
)
from app.services.profile_image_service import AvatarValidationError

router = APIRouter(prefix="/agents", tags=["agents"])


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


def _document_analysis_author_name(user: User | None) -> str:
    if user is None:
        return "Неизвестный пользователь"
    parts = [user.last_name, user.first_name]
    composed = " ".join(part.strip() for part in parts if part and part.strip())
    if composed:
        return composed
    full_name = (user.full_name or "").strip()
    if full_name:
        return full_name
    return user.email or "Неизвестный пользователь"


@router.post("/document-analysis/developer-feedback")
async def send_document_analysis_developer_feedback(
    _user: DocumentAnalysisUser,
    message: Annotated[str, Form(min_length=3)],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    """Отправка обратной связи разработчикам Авиона на email."""
    if not is_feedback_send_available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Отправка обратной связи недоступна. Откройте Outlook с ящиком "
            f"{settings.FEEDBACK_RECIPIENT_EMAIL} или укажите OUTLOOK_EMAIL и OUTLOOK_PASSWORD в .env.",
        )

    author_name = _document_analysis_author_name(_user)
    author_email = (_user.email if _user is not None else "") or ""

    attachments: list[FeedbackAttachment] = []
    for upload in files or []:
        content = await upload.read()
        if not content:
            continue
        attachments.append(
            FeedbackAttachment(
                filename=upload.filename or "attachment",
                content=content,
                content_type=upload.content_type,
            )
        )

    try:
        await send_developer_feedback_email(
            author_name=author_name,
            author_email=author_email,
            message=message.strip(),
            attachments=attachments,
        )
    except FeedbackEmailError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Не удалось отправить обратную связь: {exc}",
        ) from exc
    except smtplib.SMTPException as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Не удалось отправить обратную связь: {exc}",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Не удалось отправить обратную связь: {exc}",
        ) from exc

    return {"ok": True}


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
async def get_document_analysis_dashboard_latest(_user: DocumentAnalysisUser):
    """Последний сохранённый дашборд (контрольные точки) после анализа Авион."""
    user_id = getattr(_user, "id", None) if _user is not None else None
    snapshot = load_dashboard_snapshot(user_id)
    if snapshot is None:
        return {"ok": False, "snapshot": None}
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
    from app.services.onec_resource_spec_sync import get_resource_spec_sync_status
    from app.services.onec_stock_sync import get_stock_sync_status

    try:
        await ensure_onec_agent_tables()
        stock, resource_specs = await asyncio.gather(
            get_stock_sync_status(db, ensure=False),
            get_resource_spec_sync_status(db, ensure=False),
        )
        return {"ok": True, "stock": stock, "resource_specs": resource_specs}
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
    task_type: str = ""
    problem: str = ""
    solution: str = ""
    nomenclature: str = ""
    manager_result: str = Field(min_length=1, max_length=4000)


@router.post("/document-analysis/shipment-schedule/apply-manager-date-change")
async def apply_manager_date_change(
    _user: DocumentAnalysisUser,
    payload: ShipmentDateChangeRequest,
):
    """Применяет изменение даты из результата менеджера к объединённому графику отгрузок."""
    from app.agents.document_analysis_agent.temp_schedule_merge import (
        apply_manager_date_change_to_schedule,
    )

    try:
        raw = base64.b64decode(payload.file_base64)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Некорректный file_base64") from exc

    result = await apply_manager_date_change_to_schedule(
        raw=raw,
        task_type=payload.task_type,
        problem=payload.problem,
        solution=payload.solution,
        nomenclature=payload.nomenclature,
        manager_result=payload.manager_result,
    )

    if result.get("applied"):
        user_id = getattr(_user, "id", None) if _user is not None else None
        update_merged_shipment_snapshot(
            user_id,
            merged_shipment_schedule={
                "file_name": result.get("file_name") or payload.file_name,
                "file_base64": result.get("file_base64") or payload.file_base64,
                "values": result.get("preview_values") or [],
                "stats": {},
                "source_count": 0,
                "changed_cells": result.get("changed_cells") or [],
            },
        )
    return result


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
                "values": preview_values,
                "stats": {
                    "nomenclature_total": max(len(preview_values) - 1, 0),
                    "date_columns": max(header_len - 12, 0),
                },
                "source_count": 0,
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
        )
    except OSError:
        # дашборд не критичен для ответа анализа
        pass
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


def _shift_completion_read_stats(reports: list[ShiftCompletionReport]) -> dict:
    managers = []
    total = 0
    resolved = 0
    incomplete = 0
    partial = 0
    not_resolved = 0
    active = 0

    for report in sorted(reports, key=lambda item: item.manager_name):
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
                "stats": {
                    "total": manager_total,
                    "resolved": manager_resolved,
                    "incomplete": manager_incomplete,
                    "partial": manager_partial,
                    "not_resolved": manager_not_resolved,
                    "active": manager_active,
                    "resolved_percent": round((manager_resolved / manager_total) * 100) if manager_total else 0,
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

    return {
        "total": total,
        "resolved": resolved,
        "incomplete": incomplete,
        "partial": partial,
        "not_resolved": not_resolved,
        "active": active,
        "resolved_percent": round((resolved / total) * 100) if total else 0,
        "managers": managers,
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
    summary = _shift_completion_read_stats(reports)
    return {
        "ok": True,
        "report_date": target_date.isoformat(),
        "summary": {
            key: value
            for key, value in summary.items()
            if key != "managers"
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
