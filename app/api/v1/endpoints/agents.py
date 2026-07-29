from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse

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
)
from app.agents.document_analysis_agent.reveal_in_explorer import reveal_bytes_in_explorer
from app.agents.document_analysis_agent.templates_catalog import (
    get_aveon_template,
    list_aveon_templates,
)
from app.integrations.minio import MinioObjectError
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
from app.services.audit_service import AuditService
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


@router.get("/document-analysis/templates")
async def list_document_analysis_templates(_user: DocumentAnalysisUser):
    """Список Excel-шаблонов для загрузки в агент Авион."""
    return {
        "templates": [
            {
                "key": item.key,
                "role": item.role,
                "title": item.title,
                "filename": item.filename,
                "description": item.description,
            }
            for item in list_aveon_templates()
        ]
    }


@router.get("/document-analysis/templates/all.zip")
async def download_all_document_analysis_templates(_user: DocumentAnalysisUser):
    """ZIP со всеми шаблонами Авион."""
    items = list_aveon_templates()
    if not items:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблоны не найдены")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            archive.write(item.path, arcname=item.filename)
    buffer.seek(0)
    filename = "шаблоны_авион.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"aveon_templates.zip\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/document-analysis/templates/{template_key}")
async def download_document_analysis_template(
    template_key: str,
    _user: DocumentAnalysisUser,
):
    """Скачать один Excel-шаблон по ключу роли."""
    item = get_aveon_template(template_key)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return FileResponse(
        path=item.path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=item.filename,
        content_disposition_type="attachment",
    )


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


@router.post("/document-analysis/analyze-excel")
async def analyze_document_excel_files(
    _user: DocumentAnalysisUser,
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

    try:
        result = await analyze_aveon_excel_files(uploaded)
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
    analyzed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    user_id = getattr(_user, "id", None) if _user is not None else None
    try:
        save_dashboard_snapshot(
            user_id,
            logistics_risks=logistics_payload,
            analyzed_at=analyzed_at,
            # сменное задание — только в ответе анализа, не в персистентном дашборде
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
    return meta


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
