from __future__ import annotations

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.agents.document_analysis_agent.excel_service import (
    UploadedWorkbook,
    analyze_aveon_excel_files,
    classify_aveon_excel_files,
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


@router.post("/document-analysis/classify-excel")
async def classify_document_excel_files(
    current_user: CurrentUser,
    files: Annotated[list[UploadFile], File(...)],
):
    """Быстрое определение ролей загруженных Excel до полного запуска агента."""
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один Excel-файл")

    uploaded: list[UploadedWorkbook] = []
    for file in files:
        filename = file.filename or "workbook.xlsx"
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Файл {filename} должен быть в формате .xlsx или .xlsm",
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


@router.post("/document-analysis/analyze-excel")
async def analyze_document_excel_files(
    current_user: CurrentUser,
    files: Annotated[list[UploadFile], File(...)],
):
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один Excel-файл")

    uploaded: list[UploadedWorkbook] = []
    for file in files:
        filename = file.filename or "workbook.xlsx"
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Файл {filename} должен быть в формате .xlsx или .xlsm",
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

    file_base64 = (
        base64.b64encode(result.result_xlsx_bytes).decode("ascii")
        if result.result_xlsx_bytes
        else None
    )
    return {
        "source": result.source,
        "roles": [
            {"filename": filename, "role": role}
            for filename, role in sorted(result.roles.items())
        ],
        "production_schedule_files": result.production_schedule_files,
        "production_schedule_products": result.production_schedule_products,
        "production_schedule_plans": [
            {
                "product": plan.product,
                "monthly_qty": plan.monthly_qty,
            }
            for plan in result.production_schedule_plans
        ],
        "product_spec_links": [
            {
                "schedule_product": link.schedule_product,
                "nomenclature": link.nomenclature,
                "spec_sheet": link.spec_sheet,
                "status": link.status,
                "reason": link.reason,
            }
            for link in result.product_spec_links
        ],
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
        "logistics_risks": {
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
        },
        "file_name": "result.xlsx",
        "file_base64": file_base64,
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
