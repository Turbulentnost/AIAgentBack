"""Преобразование результата проверки ИИ в формат разметки."""

from __future__ import annotations

from typing import Any

from app.gost.aggregation import collect_issues_from_item
from app.gost.catalog import GOST_LINE_KEYS, issue_to_line


def remark_to_gost_key(remark: dict[str, Any]) -> str | None:
    key = issue_to_line(
        code=remark.get("code"),
        element=remark.get("element"),
        zone=remark.get("zone"),
        gost_reference=remark.get("gost_reference"),
    )
    if key and key in GOST_LINE_KEYS:
        return key
    return None


def _merge_page_finding(
    bucket: dict[int, dict[str, dict[str, Any]]],
    *,
    page: int,
    gost_key: str,
    severity: str,
    note: str,
) -> None:
    if page <= 0 or gost_key not in GOST_LINE_KEYS:
        return
    if severity not in {"error", "warning"}:
        return
    page_bucket = bucket.setdefault(page, {})
    note = note.strip()
    existing = page_bucket.get(gost_key)
    if existing:
        prev = str(existing.get("note") or "").strip()
        if note and note not in prev:
            existing["note"] = f"{prev}; {note}".strip("; ") if prev else note
        if severity == "error":
            existing["severity"] = "error"
        return
    page_bucket[gost_key] = {
        "gost_key": gost_key,
        "severity": severity,
        "pages": [page],
        "note": note,
    }


def package_error_pages(err: dict[str, Any]) -> list[int]:
    pages = [int(p) for p in (err.get("pages") or []) if str(p).isdigit() and int(p) > 0]
    if pages:
        return pages
    details = err.get("details")
    if not isinstance(details, dict):
        return []
    raw = details.get("pages_on_drawing") or details.get("pages") or []
    return [int(p) for p in raw if str(p).isdigit() and int(p) > 0]


def build_page_level_from_check(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Преобразует результат проверки ИИ в формат page_level для разметки."""
    by_page: dict[int, dict[str, dict[str, Any]]] = {}
    page_notes: dict[int, str] = {}

    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        page = int(item.get("page") or 0)
        if page <= 0:
            continue
        page_note = str(item.get("report_text") or "").strip()
        if page_note:
            page_notes[page] = page_note

        for err in item.get("errors") or []:
            if not isinstance(err, dict):
                continue
            gost_key = remark_to_gost_key(err)
            if gost_key:
                _merge_page_finding(
                    by_page,
                    page=page,
                    gost_key=gost_key,
                    severity="error",
                    note=str(err.get("message") or err.get("text") or ""),
                )

        for warn in item.get("warnings") or []:
            if not isinstance(warn, dict):
                continue
            gost_key = remark_to_gost_key(warn)
            if gost_key:
                _merge_page_finding(
                    by_page,
                    page=page,
                    gost_key=gost_key,
                    severity="warning",
                    note=str(warn.get("message") or warn.get("text") or ""),
                )

        item_errors, item_warnings = collect_issues_from_item(item)
        for gost_key, pages in item_errors.items():
            for page_no in pages:
                _merge_page_finding(
                    by_page,
                    page=page_no,
                    gost_key=gost_key,
                    severity="error",
                    note="",
                )
        for gost_key, pages in item_warnings.items():
            for page_no in pages:
                _merge_page_finding(
                    by_page,
                    page=page_no,
                    gost_key=gost_key,
                    severity="warning",
                    note="",
                )

    for err in payload.get("package_errors") or []:
        if not isinstance(err, dict):
            continue
        gost_key = remark_to_gost_key(err)
        if not gost_key:
            continue
        severity = str(err.get("severity") or "error")
        if severity not in {"error", "warning"}:
            continue
        note = str(err.get("message") or err.get("text") or "")
        for page_no in package_error_pages(err):
            _merge_page_finding(
                by_page,
                page=page_no,
                gost_key=gost_key,
                severity=severity,
                note=note,
            )

    page_level: list[dict[str, Any]] = []
    for page_no in sorted(set(by_page) | set(page_notes)):
        findings = list(by_page.get(page_no, {}).values())
        note = page_notes.get(page_no, "")
        if not findings and not note:
            continue
        page_level.append({"page": page_no, "gost_findings": findings, "note": note})

    problem_report = str(payload.get("report_text") or payload.get("summary") or "").strip()
    return page_level, problem_report
