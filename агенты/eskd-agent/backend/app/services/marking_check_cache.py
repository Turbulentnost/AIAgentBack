"""Подстановка результата проверки из сохранённой разметки без вызова ИИ."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.gost.aggregation import aggregate_from_check_response
from app.gost.catalog import GOST_LINE_ORDER
from app.models.check_run import EskdCheckRun
from app.models.marking import EskdMarkingDocument, EskdMarkingLabel
from app.services.history_service import HistoryService
from app.services.marking_service import MarkingService

_CACHED_STATUSES = frozenset({"from_marking", "from_cache"})


def build_check_response_from_marking(
    *,
    filename: str,
    designation: str | None,
    doc: EskdMarkingDocument,
    label: EskdMarkingLabel,
) -> dict[str, Any]:
    title_map = dict(GOST_LINE_ORDER)
    page_entries: dict[int, dict] = {}
    for entry in label.page_level or []:
        if isinstance(entry, dict):
            page_entries[int(entry.get("page") or 0)] = entry

    page_numbers: list[int] = []
    for item in doc.pages or []:
        if isinstance(item, dict):
            page_numbers.append(int(item.get("page") or 0))
    page_numbers.extend(page_entries.keys())
    page_numbers = sorted({p for p in page_numbers if p > 0}) or [1]

    items: list[dict[str, Any]] = []
    for idx, page_no in enumerate(page_numbers, start=1):
        entry = page_entries.get(page_no, {})
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for finding in entry.get("gost_findings") or []:
            if not isinstance(finding, dict):
                continue
            gost_key = str(finding.get("gost_key") or "")
            severity = str(finding.get("severity") or "ok")
            if severity not in {"error", "warning"}:
                continue
            note = str(finding.get("note") or "").strip()
            title = title_map.get(gost_key, gost_key)
            message = note or title
            remark = {
                "kind": "marking",
                "code": gost_key,
                "message": message,
                "severity": severity,
                "gost_reference": title,
                "text": message,
            }
            if severity == "error":
                errors.append(remark)
            else:
                warnings.append(remark)

        page_note = str(entry.get("note") or "").strip()
        if page_note and not errors and not warnings:
            warnings.append(
                {
                    "kind": "marking",
                    "code": "page_note",
                    "message": page_note,
                    "severity": "warning",
                    "text": page_note,
                }
            )

        if errors:
            summary = f"Лист {page_no}: {len(errors)} ошибок, {len(warnings)} замечаний (разметка)"
        elif warnings:
            summary = f"Лист {page_no}: {len(warnings)} замечаний (разметка)"
        else:
            summary = f"Лист {page_no}: замечаний не отмечено (разметка)"

        items.append(
            {
                "index": idx,
                "total": len(page_numbers),
                "source": filename,
                "filename": filename,
                "page": page_no,
                "status": "from_marking",
                "summary": summary,
                "errors_count": len(errors),
                "warnings_count": len(warnings),
                "errors": errors,
                "warnings": warnings,
                "positions": [],
                "elements": [],
                "report_text": page_note,
                "infer_seconds": 0.0,
                "error": None,
            }
        )

    total_errors = sum(i["errors_count"] for i in items)
    total_warnings = sum(i["warnings_count"] for i in items)
    global_note = (
        "Результат взят из сохранённой разметки — проверка ИИ не выполнялась. "
        f"Документ: {doc.source_filename}"
    )

    payload: dict[str, Any] = {
        "job_id": str(uuid.uuid4()),
        "designation": designation or doc.designation,
        "model": "",
        "adapter": "",
        "total_items": len(items),
        "processed": len(items),
        "failed": 0,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "total_infer_seconds": 0.0,
        "load_seconds": 0.0,
        "progress_percent": 100.0,
        "status": "from_marking",
        "global_warnings": [global_note],
        "items": items,
        "report_text": label.problem_report or "",
        "summary": "Из сохранённой разметки (без ИИ)",
        "marking_document_id": str(doc.id),
        "marking_label_id": str(label.id),
        "gost_summary": aggregate_from_check_response({"items": items}),
    }
    return payload


def build_check_response_from_run(
    *,
    filename: str,
    designation: str | None,
    run: EskdCheckRun,
) -> dict[str, Any]:
    payload = dict(run.raw_result or {})
    payload["job_id"] = str(uuid.uuid4())
    payload["designation"] = designation or run.designation or payload.get("designation")
    payload["status"] = "from_cache"
    payload["check_run_id"] = str(run.id)
    note = (
        "Результат взят из сохранённой проверки ИИ — модель не вызывалась. "
        f"Файл: {run.original_filename or filename}"
    )
    warnings = list(payload.get("global_warnings") or [])
    if note not in warnings:
        warnings.insert(0, note)
    payload["global_warnings"] = warnings
    payload["summary"] = payload.get("summary") or "Из сохранённой проверки (без ИИ)"
    if "gost_summary" not in payload and payload.get("items"):
        payload["gost_summary"] = aggregate_from_check_response(payload)
    return payload


class MarkingCheckCacheService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._marking = MarkingService(db)
        self._history = HistoryService(db)

    async def try_build_from_marking(
        self,
        *,
        uploads: list[tuple[str, bytes]],
        designation: str | None,
    ) -> dict[str, Any] | None:
        cached = await self.try_build_cached(uploads=uploads, designation=designation)
        if cached and cached.get("status") == "from_marking":
            return cached
        return None

    async def try_build_cached(
        self,
        *,
        uploads: list[tuple[str, bytes]],
        designation: str | None,
    ) -> dict[str, Any] | None:
        if len(uploads) != 1:
            return None

        filename = uploads[0][0]
        doc = await self._marking.find_latest_document_by_filename(filename)
        if doc:
            label = await self._marking.get_latest_label_for_document(doc.id)
            if label is not None:
                return build_check_response_from_marking(
                    filename=filename,
                    designation=designation,
                    doc=doc,
                    label=label,
                )

        run = await self._history.find_latest_by_filename(filename)
        if run is not None:
            return build_check_response_from_run(
                filename=filename,
                designation=designation,
                run=run,
            )

        checksum = hashlib.sha256(uploads[0][1]).hexdigest()
        run = await self._history.find_latest_by_checksum(checksum)
        if run is not None:
            return build_check_response_from_run(
                filename=filename,
                designation=designation,
                run=run,
            )
        return None
