from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.marking import (
    GostStatsResponse,
    MarkingDocumentListResponse,
    MarkingDocumentLookupResponse,
    MarkingDocumentPage,
    MarkingDocumentRead,
    MarkingLabelCreate,
    MarkingLabelListResponse,
    MarkingLabelRead,
    MarkingLabelSuggestedResponse,
    MarkingLabelUpdate,
)
from app.gost.marking_from_check import build_page_level_from_check
from app.services.check_upload_storage import CheckUploadStorage
from app.services.history_service import HistoryService
from app.services.marking_service import MarkingService

router = APIRouter(prefix="/api/v1/eskd/marking", tags=["eskd-marking"])


def _doc_to_read(
    doc,
    svc: MarkingService,
    *,
    reused_existing: bool = False,
    has_saved_label: bool = False,
) -> MarkingDocumentRead:
    pages = []
    for item in doc.pages or []:
        page_no = int(item.get("page") or 0)
        pages.append(
            MarkingDocumentPage(
                page=page_no,
                preview_url=f"/api/v1/eskd/marking/documents/{doc.id}/pages/{page_no}/preview",
                width=item.get("width"),
                height=item.get("height"),
            )
        )
    return MarkingDocumentRead(
        id=doc.id,
        designation=doc.designation,
        source_filename=doc.source_filename,
        pages=sorted(pages, key=lambda p: p.page),
        created_at=doc.created_at,
        reused_existing=reused_existing,
        has_saved_label=has_saved_label,
    )


def _label_to_read(label) -> MarkingLabelRead:
    return MarkingLabelRead(
        id=label.id,
        document_id=label.document_id,
        check_run_id=label.check_run_id,
        is_rework=label.is_rework,
        document_level=label.document_level or [],
        page_level=label.page_level or [],
        problem_report=label.problem_report,
        created_at=label.created_at,
    )


@router.get("/documents", response_model=MarkingDocumentListResponse)
async def list_marking_documents(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    items = await MarkingService(db).list_documents(limit=limit)
    return MarkingDocumentListResponse(items=items, total=len(items))


@router.get("/documents/lookup", response_model=MarkingDocumentLookupResponse)
async def lookup_marking_document_by_filename(
    filename: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    svc = MarkingService(db)
    doc = await svc.find_latest_document_by_filename(filename)
    if not doc:
        return MarkingDocumentLookupResponse(found=False)
    latest = await svc.get_latest_label_for_document(doc.id)
    marked_pages = len(latest.page_level or []) if latest else 0
    return MarkingDocumentLookupResponse(
        found=True,
        document=_doc_to_read(
            doc,
            svc,
            has_saved_label=latest is not None,
        ),
        marked_pages_count=marked_pages,
        label_updated_at=latest.updated_at if latest else None,
    )


@router.post("/documents/open-from-check-run/{run_id}", response_model=MarkingDocumentRead)
async def open_marking_from_check_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    file: UploadFile | None = File(default=None),
):
    import hashlib

    history = HistoryService(db)
    run = await history.get_run(run_id)
    if not run:
        raise HTTPException(404, "Проверка не найдена")

    svc = MarkingService(db)
    filename = (run.original_filename or "").strip()
    if filename:
        existing = await svc.find_latest_document_by_filename(filename)
        if existing:
            latest = await svc.get_latest_label_for_document(existing.id)
            return _doc_to_read(
                existing,
                svc,
                reused_existing=True,
                has_saved_label=latest is not None,
            )

    stored: tuple[str, bytes] | None = None
    if run.file_sha256:
        stored = CheckUploadStorage().load(sha256=run.file_sha256, filename=run.original_filename)

    if stored is None and file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(400, "Пустой файл")
        upload_name = file.filename or run.original_filename or "upload.pdf"
        if filename and upload_name.strip().lower() != filename.lower():
            raise HTTPException(
                400,
                f"Выберите файл «{run.original_filename}» — имя не совпадает",
            )
        actual_sha = hashlib.sha256(data).hexdigest()
        expected_sha = (run.file_sha256 or "").strip().lower()
        if expected_sha and actual_sha != expected_sha:
            raise HTTPException(
                400,
                f"Содержимое файла не совпадает с проверкой «{run.original_filename}»",
            )
        digest = expected_sha or actual_sha
        CheckUploadStorage().save(sha256=digest, filename=upload_name, data=data)
        stored = (upload_name, data)

    if stored is None:
        raise HTTPException(
            409,
            f"Выберите файл «{run.original_filename or 'PDF'}» — исходник проверки не сохранён на сервере",
        )

    upload_name, data = stored
    try:
        doc = await svc.create_document(
            filename=upload_name,
            data=data,
            designation=run.designation,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _doc_to_read(doc, svc)


@router.post("/documents", response_model=MarkingDocumentRead)
async def upload_marking_document(
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    designation: str | None = Form(default=None),
    reuse_existing: str = Form(default="true"),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    reuse = reuse_existing.strip().lower() not in {"false", "0", "no"}
    try:
        doc, reused = await MarkingService(db).upload_document(
            filename=file.filename or "upload.bin",
            data=data,
            designation=designation,
            reuse_existing=reuse,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    try:
        CheckUploadStorage().save(sha256=digest, filename=file.filename or "upload.bin", data=data)
    except (ValueError, OSError):
        pass
    svc = MarkingService(db)
    latest = await svc.get_latest_label_for_document(doc.id)
    return _doc_to_read(
        doc,
        svc,
        reused_existing=reused,
        has_saved_label=latest is not None,
    )


@router.get("/documents/{doc_id}", response_model=MarkingDocumentRead)
async def get_marking_document(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    doc = await MarkingService(db).get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    return _doc_to_read(doc, MarkingService(db))


@router.get("/documents/{doc_id}/pages/{page}/preview")
async def get_page_preview(doc_id: uuid.UUID, page: int, db: AsyncSession = Depends(get_db)):
    svc = MarkingService(db)
    doc = await svc.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    path = svc.resolve_preview_file(doc, page)
    if not path:
        raise HTTPException(404, "Страница не найдена")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/documents/{doc_id}/label/latest", response_model=MarkingLabelRead)
async def get_latest_marking_label(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    doc = await MarkingService(db).get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    label = await MarkingService(db).get_latest_label_for_document(doc_id)
    if not label:
        raise HTTPException(404, "Разметка для документа не найдена")
    return _label_to_read(label)


@router.get("/documents/{doc_id}/label/suggested", response_model=MarkingLabelSuggestedResponse)
async def get_suggested_marking_label(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = MarkingService(db)
    doc = await svc.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")

    label = await svc.get_latest_label_for_document(doc_id)
    if label and ((label.page_level or []) or (label.problem_report or "").strip()):
        return MarkingLabelSuggestedResponse(
            found=True,
            source="saved",
            label_id=label.id,
            check_run_id=label.check_run_id,
            page_level=label.page_level or [],
            problem_report=label.problem_report or "",
        )

    run = await HistoryService(db).find_latest_by_filename(doc.source_filename)
    if run is None or not run.raw_result:
        return MarkingLabelSuggestedResponse(found=False, source="none")

    page_level, problem_report = build_page_level_from_check(run.raw_result)
    if not page_level and not problem_report:
        return MarkingLabelSuggestedResponse(found=False, source="none", check_run_id=run.id)

    return MarkingLabelSuggestedResponse(
        found=True,
        source="check_run",
        check_run_id=run.id,
        page_level=page_level,
        problem_report=problem_report,
    )


@router.get("/labels/{label_id}", response_model=MarkingLabelRead)
async def get_marking_label(label_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    label = await MarkingService(db).get_label(label_id)
    if not label:
        raise HTTPException(404, "Разметка не найдена")
    return _label_to_read(label)


@router.put("/labels/{label_id}", response_model=MarkingLabelRead)
async def update_marking_label(
    label_id: uuid.UUID,
    payload: MarkingLabelUpdate,
    db: AsyncSession = Depends(get_db),
):
    svc = MarkingService(db)
    label = await svc.get_label(label_id)
    if not label:
        raise HTTPException(404, "Разметка не найдена")
    try:
        updated = await svc.update_label(
            label_id,
            MarkingLabelCreate(
                document_id=label.document_id,
                document_level=payload.document_level,
                page_level=payload.page_level,
                problem_report=payload.problem_report,
            ),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _label_to_read(updated)


@router.post("/labels", response_model=MarkingLabelRead)
async def create_marking_label(payload: MarkingLabelCreate, db: AsyncSession = Depends(get_db)):
    try:
        label = await MarkingService(db).create_label(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _label_to_read(label)


@router.get("/labels", response_model=MarkingLabelListResponse)
async def list_marking_labels(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    labels = await MarkingService(db).list_labels(limit=limit)
    return MarkingLabelListResponse(items=[_label_to_read(lb) for lb in labels], total=len(labels))


@router.get("/stats", response_model=GostStatsResponse)
async def marking_stats(db: AsyncSession = Depends(get_db)):
    items = await MarkingService(db).compute_stats()
    return GostStatsResponse(items=items)
