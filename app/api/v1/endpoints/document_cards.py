from __future__ import annotations

import html
import uuid
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.documents.storage import object_storage
from app.models.document import Document
from app.schemas.common import Page
from app.schemas.document_card import (
    DocumentCardBootstrapResult,
    DocumentCardCreate,
    DocumentCardFolderImportResult,
    DocumentCardFolderScanResult,
    DocumentCardImportFolderRequest,
    DocumentCardRead,
    DocumentCardUpdate,
)
from app.services.document_card_service import DocumentCardService, DocumentCardServiceError
from app.services.document_folder_import_service import DocumentFolderImportService, DocumentFolderImportServiceError
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/document-cards", tags=["document-cards"])

DEFAULT_WELDING_FOLDER = (
    r"\\192.168.1.198\Files\10.СКТБ\НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ\НОРМАТИВНЫЕ ДОКУМЕНТЫ\Документы по сварке"
)


@router.get("", response_model=Page[DocumentCardRead])
async def list_document_cards(
    db: DbSession,
    current_user: CurrentUser,
    query: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
):
    _ = current_user
    return await DocumentCardService(db).list(query=query, page=page, size=size)


@router.get("/viewer", response_class=HTMLResponse)
async def document_cards_viewer(
    db: DbSession,
    current_user: CurrentUser,
    query: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=500),
):
    _ = current_user
    page_data = await DocumentCardService(db).list(query=query, page=page, size=size)
    rows = []
    for card in page_data.items:
        download_url = f"{settings.API_V1_PREFIX}/document-cards/{card.id}/download"
        rows.append(
            "<tr>"
            f"<td>{html.escape(card.document_code)}</td>"
            f"<td>{html.escape(card.document_name)}</td>"
            f"<td>{html.escape(card.document_type.value)}</td>"
            f"<td>{html.escape(card.qms_level.value)}</td>"
            f"<td>{html.escape(card.status.value)}</td>"
            f"<td>{html.escape(card.original_storage_location or '')}</td>"
            f'<td><a href="{html.escape(download_url)}" target="_blank">Файл</a></td>'
            "</tr>"
        )
    query_value = html.escape(query or "")
    body = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Карточки нормативных документов</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f5f5f5; text-align: left; }}
    .toolbar {{ margin-bottom: 16px; display: flex; gap: 12px; align-items: center; }}
    input[type="search"] {{ min-width: 320px; padding: 8px; }}
  </style>
</head>
<body>
  <h1>Карточки нормативных документов</h1>
  <form class="toolbar" method="get">
    <input type="search" name="query" value="{query_value}" placeholder="Поиск по коду, названию, пути..." />
    <button type="submit">Найти</button>
    <span>Всего: {page_data.total}</span>
  </form>
  <table>
    <thead>
      <tr>
        <th>Код</th>
        <th>Наименование</th>
        <th>Вид</th>
        <th>Уровень СМК</th>
        <th>Статус</th>
        <th>Оригинал</th>
        <th>Файл</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows) if rows else '<tr><td colspan="7">Карточки не найдены</td></tr>'}
    </tbody>
  </table>
</body>
</html>
"""
    return HTMLResponse(content=body)


@router.post("/scan-folder", response_model=DocumentCardFolderScanResult)
async def scan_document_folder(
    payload: DocumentCardImportFolderRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    _ = db
    _ = current_user
    try:
        files = DocumentFolderImportService(db).scan_folder(payload.folder_path, recursive=payload.recursive)
        return DocumentCardFolderScanResult(
            folder_path=payload.folder_path,
            total_files=len(files),
            files=files,
        )
    except DocumentFolderImportServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import-folder", response_model=DocumentCardFolderImportResult)
async def import_document_folder(
    payload: DocumentCardImportFolderRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только администратор может импортировать папку")
    service = DocumentFolderImportService(db)
    try:
        result = await service.import_folder(
            payload.folder_path,
            uploaded_by_user_id=current_user.id,
            department_id=current_user.department_id,
            recursive=payload.recursive,
            dry_run=payload.dry_run,
            is_knowledge_base=payload.is_knowledge_base,
        )
        if not payload.dry_run:
            await db.commit()
        else:
            await db.rollback()
        return result
    except DocumentFolderImportServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import-welding-folder", response_model=DocumentCardFolderImportResult)
async def import_welding_documents_folder(db: DbSession, current_user: CurrentUser, dry_run: bool = False):
    payload = DocumentCardImportFolderRequest(folder_path=DEFAULT_WELDING_FOLDER, dry_run=dry_run)
    return await import_document_folder(payload, db, current_user)


@router.get("/by-document/{document_id}", response_model=DocumentCardRead)
async def get_document_card_by_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    if not await PermissionService(db).can_access_document(current_user, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    card = await DocumentCardService(db).get_by_document_id(document_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карточка документа не найдена")
    return card


@router.get("/{card_id}/download")
async def download_document_card_file(card_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    card = await DocumentCardService(db).get_or_raise(card_id)
    document = await db.get(Document, card.document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    if not await PermissionService(db).can_access_document(current_user, document.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    if not document.bucket_name or not document.object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл документа не найден")
    content = object_storage.get_object_from_bucket(document.bucket_name, document.object_name)
    filename = document.original_filename or f"{card.document_code}.bin"
    media_type = document.content_type or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(BytesIO(content), media_type=media_type, headers=headers)


@router.get("/{card_id}", response_model=DocumentCardRead)
async def get_document_card(card_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    _ = current_user
    try:
        return await DocumentCardService(db).get_or_raise(card_id)
    except DocumentCardServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("", response_model=DocumentCardRead, status_code=status.HTTP_201_CREATED)
async def create_document_card(payload: DocumentCardCreate, db: DbSession, current_user: CurrentUser):
    _ = current_user
    try:
        card = await DocumentCardService(db).create(payload)
        await db.commit()
        return card
    except DocumentCardServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/from-document/{document_id}", response_model=DocumentCardRead, status_code=status.HTTP_201_CREATED)
async def create_document_card_from_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    if not await PermissionService(db).can_access_document(current_user, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    try:
        card = await DocumentCardService(db).create_from_document(document)
        await db.commit()
        return card
    except DocumentCardServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/bootstrap", response_model=DocumentCardBootstrapResult)
async def bootstrap_document_cards(db: DbSession, current_user: CurrentUser):
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только администратор может выполнить bootstrap")
    try:
        result = await DocumentCardService(db).bootstrap_for_all_documents()
        await db.commit()
        return result
    except DocumentCardServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/{card_id}", response_model=DocumentCardRead)
async def update_document_card(
    card_id: uuid.UUID,
    payload: DocumentCardUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    _ = current_user
    try:
        card = await DocumentCardService(db).update(card_id, payload)
        await db.commit()
        return card
    except DocumentCardServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
