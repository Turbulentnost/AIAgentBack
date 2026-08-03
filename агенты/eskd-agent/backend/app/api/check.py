from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.check_lookup import CheckCacheLookupResponse
from app.services.history_service import HistoryService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.marking_service import MarkingService

router = APIRouter(prefix="/api/v1/eskd", tags=["check"])


@router.get("/check/lookup", response_model=CheckCacheLookupResponse)
async def lookup_check_cache(
    filename: str = Query(..., min_length=1),
    checksum: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    name = filename.strip()
    marking = MarkingService(db)
    history = HistoryService(db)
    doc = await marking.find_latest_document_by_filename(name)
    label = await marking.get_latest_label_for_document(doc.id) if doc else None
    run = await history.find_latest_by_filename(name)
    if run is None and checksum:
        run = await history.find_latest_by_checksum(checksum.strip())

    rows, _, _, _ = await KnowledgeBaseService(db).list_entries(q=name, size=50)
    needle = name.lower()
    kb_row = next(
        (r for r in rows if str(r.get("display_name") or "").strip().lower() == needle),
        rows[0] if len(rows) == 1 else None,
    )

    from_marking = label is not None
    from_check_run = run is not None and not from_marking
    found = from_marking or from_check_run
    checked_in_kb = bool(kb_row and kb_row.get("checked"))
    display_name = (kb_row or {}).get("display_name") or (doc.source_filename if doc else name)

    message: str | None = None
    if found and from_marking:
        pages = len(label.page_level or []) if label else 0
        if checked_in_kb:
            message = (
                f"Файл «{display_name}» уже проверен в базе знаний. "
                "Будет показана сохранённая разметка без вызова ИИ."
            )
        else:
            message = (
                f"Найдена сохранённая разметка ({pages or 'пустая'}). "
                "ИИ не вызывается."
            )
    elif found and from_check_run:
        message = (
            f"Найдена сохранённая проверка ИИ для «{display_name}». "
            "Модель не вызывается."
        )
    elif kb_row:
        if checked_in_kb:
            message = f"Файл «{display_name}» отмечен проверенным, но сохранённого отчёта нет."
        else:
            message = f"Файл «{display_name}» есть в базе (не проверен). Нужна модель ИИ."

    return CheckCacheLookupResponse(
        found=found,
        from_marking=from_marking,
        from_check_run=from_check_run,
        checked_in_kb=checked_in_kb,
        display_name=display_name,
        marked_pages_count=len(label.page_level or []) if label else 0,
        has_ai_check=bool(kb_row and kb_row.get("has_ai_check")),
        message=message,
    )
