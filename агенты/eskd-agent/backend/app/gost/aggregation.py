"""Агрегация результатов проверки по 8 ГОСТ."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.gost.catalog import DOCUMENT_WIDE_PACKAGE_CODES, GOST_LINE_KEYS, issue_to_line


def _add_issue(
    target: dict[str, set[int]],
    *,
    page: int,
    code: str | None = None,
    element: str | None = None,
    zone: str | None = None,
    gost_reference: str | None = None,
) -> None:
    line = issue_to_line(code=code, element=element, zone=zone, gost_reference=gost_reference)
    if line:
        target[line].add(page)


def collect_issues_from_item(item: dict[str, Any]) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    errors: dict[str, set[int]] = defaultdict(set)
    warnings: dict[str, set[int]] = defaultdict(set)
    page = int(item.get("page") or 0)
    if not page:
        return errors, warnings

    for err in item.get("errors") or []:
        if isinstance(err, dict):
            _add_issue(
                errors,
                page=page,
                code=err.get("code"),
                element=err.get("element"),
                zone=err.get("zone"),
                gost_reference=err.get("gost_reference"),
            )

    for warn in item.get("warnings") or []:
        if isinstance(warn, dict):
            _add_issue(
                warnings,
                page=page,
                code=warn.get("code"),
                element=warn.get("element"),
                zone=warn.get("zone"),
                gost_reference=warn.get("gost_reference"),
            )

    for check in item.get("checks") or []:
        if not isinstance(check, dict):
            continue
        if str(check.get("status") or "").lower() in {"error", "fail"}:
            _add_issue(
                errors,
                page=page,
                element=check.get("element"),
                zone=check.get("zone"),
                gost_reference=check.get("gost_reference"),
            )

    if item.get("positions_order_ok") is False:
        errors["2.105"].add(page)

    for pos in item.get("positions") or []:
        if isinstance(pos, dict) and pos.get("readable") is False:
            errors["2.105"].add(page)
            break

    for overlay in item.get("overlays") or []:
        if isinstance(overlay, dict) and overlay.get("present"):
            warnings["2.105"].add(page)
            break

    return errors, warnings


def _pages_from_package_error(err: dict[str, Any], item_pages: list[int]) -> list[int]:
    pages = [int(p) for p in (err.get("pages") or []) if str(p).isdigit() and int(p) > 0]
    if not pages:
        details = err.get("details") or {}
        for value in details.get("pages_on_drawing") or details.get("pages") or []:
            if str(value).isdigit() and int(value) > 0:
                pages.append(int(value))
        for entry in details.get("sheets") or []:
            if isinstance(entry, (list, tuple)) and entry and str(entry[0]).isdigit():
                pages.append(int(entry[0]))
            elif isinstance(entry, dict) and str(entry.get("page") or "").isdigit():
                pages.append(int(entry["page"]))
        by_page = details.get("by_page")
        if isinstance(by_page, dict):
            for key in by_page:
                if str(key).isdigit() and int(key) > 0:
                    pages.append(int(key))
    pages = sorted(set(pages))
    if not pages and str(err.get("code") or "") in DOCUMENT_WIDE_PACKAGE_CODES and item_pages:
        return sorted(set(item_pages))
    return pages


def aggregate_gost_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    error_pages: dict[str, set[int]] = defaultdict(set)
    warning_pages: dict[str, set[int]] = defaultdict(set)

    for item in items:
        item_errors, item_warnings = collect_issues_from_item(item)
        for key, pages in item_errors.items():
            error_pages[key].update(pages)
        for key, pages in item_warnings.items():
            warning_pages[key].update(pages)

    errors_out: dict[str, list[int]] = {
        key: sorted(pages) for key, pages in error_pages.items() if pages
    }
    warnings_out: dict[str, list[int]] = {
        key: sorted(pages) for key, pages in warning_pages.items() if pages
    }

    passed: list[str] = []
    for key in GOST_LINE_KEYS:
        if key not in errors_out and key not in warnings_out:
            passed.append(key)

    return {"passed": passed, "warnings": warnings_out, "errors": errors_out}


def aggregate_from_check_response(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") or []
    if not isinstance(items, list):
        items = []
    item_pages = sorted(
        {
            int(item.get("page") or 0)
            for item in items
            if isinstance(item, dict) and int(item.get("page") or 0) > 0
        }
    )
    summary = aggregate_gost_summary(items)
    errors_out: dict[str, set[int]] = {k: set(v) for k, v in summary["errors"].items()}
    warnings_out: dict[str, set[int]] = {k: set(v) for k, v in summary["warnings"].items()}

    for err in payload.get("package_errors") or []:
        if not isinstance(err, dict):
            continue
        pages = _pages_from_package_error(err, item_pages)
        line = issue_to_line(
            code=err.get("code"),
            element=err.get("element"),
            zone=err.get("zone"),
            gost_reference=err.get("gost_reference"),
        )
        if not line or not pages:
            continue
        bucket = errors_out if str(err.get("severity") or "error") == "error" else warnings_out
        bucket.setdefault(line, set()).update(pages)

    errors_final = {k: sorted(v) for k, v in errors_out.items() if v}
    warnings_final = {k: sorted(v) for k, v in warnings_out.items() if v}
    passed = [key for key in GOST_LINE_KEYS if key not in errors_final and key not in warnings_final]
    return {"passed": passed, "warnings": warnings_final, "errors": errors_final}
