"""Google Sheets API v4 through Service Account (Aveon)."""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)

DEFAULT_SHEET_TITLE = "ИТЦ В РАБОТЕ"


class GoogleSheetsConfigError(Exception):
    """Service Account is not configured or JSON is invalid."""


def is_configured() -> bool:
    settings = get_settings()
    return bool(
        (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or "").strip()
        or (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or "").strip()
    )


def _load_service_account_info() -> dict[str, Any]:
    settings = get_settings()
    raw_json = (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or "").strip()
    if raw_json:
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise GoogleSheetsConfigError(
                f"GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON: invalid JSON ({exc})"
            ) from exc

    file_path = (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or "").strip()
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise GoogleSheetsConfigError(
                f"GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE not found: {file_path}"
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GoogleSheetsConfigError(
                f"Service Account file is not valid JSON: {file_path}"
            ) from exc

    raise GoogleSheetsConfigError(
        "Google Sheets Service Account is not configured. "
        "Set GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON."
    )


@lru_cache(maxsize=1)
def _credentials_bundle() -> tuple[Any, str | None]:
    from google.oauth2 import service_account

    info = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=list(SCOPES),
    )
    client_email = info.get("client_email")
    return credentials, str(client_email) if client_email else None


def get_service_account_email() -> str | None:
    if not is_configured():
        return None
    try:
        return _credentials_bundle()[1]
    except Exception:
        return None


def _sheets_service():
    from googleapiclient.discovery import build

    credentials, _ = _credentials_bundle()
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _find_sheet(
    spreadsheet: dict[str, Any],
    *,
    sheet_gid: str | None = None,
    sheet_title: str | None = None,
) -> dict[str, Any] | None:
    sheets = spreadsheet.get("sheets") or []
    if sheet_title:
        title_norm = sheet_title.strip().casefold()
        for sheet in sheets:
            props = sheet.get("properties") or {}
            if str(props.get("title") or "").strip().casefold() == title_norm:
                return sheet
    if sheet_gid:
        for sheet in sheets:
            props = sheet.get("properties") or {}
            if str(props.get("sheetId")) == str(sheet_gid):
                return sheet
    return None


def _preview_values(values: list[list[Any]], max_rows: int = 5, max_cols: int = 10) -> list[list[Any]]:
    preview: list[list[Any]] = []
    for row in values[:max_rows]:
        preview.append([(cell if cell is not None else "") for cell in row[:max_cols]])
    return preview


def _normalize_matrix(values: list[list[Any]]) -> list[list[str]]:
    width = max((len(row) for row in values), default=0)
    matrix: list[list[str]] = []
    for row in values:
        cells = [("" if cell is None else str(cell)) for cell in row]
        if len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        matrix.append(cells)
    return matrix


def fetch_sheet_via_api(
    spreadsheet_id: str,
    sheet_gid: str | None = None,
    *,
    sheet_title: str | None = None,
    include_values: bool = False,
    preview_rows: int = 5,
) -> dict[str, Any]:
    """Read a sheet via Google Sheets API v4 (Service Account)."""
    started = time.perf_counter()
    target_title = (sheet_title or DEFAULT_SHEET_TITLE).strip()
    record: dict[str, Any] = {
        "name": "service_account_api",
        "ok": False,
        "spreadsheet_id": spreadsheet_id,
        "sheet_gid": sheet_gid,
        "sheet_title": target_title,
        "service_account_email": get_service_account_email(),
        "elapsed_ms": None,
        "error": None,
        "hint": None,
        "parsed": None,
    }

    if not is_configured():
        record["error"] = "Service Account is not configured on backend"
        record["hint"] = (
            "Set GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON in .env"
        )
        return record

    try:
        service = _sheets_service()
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, includeGridData=False)
            .execute()
        )
        sheet = _find_sheet(meta, sheet_gid=sheet_gid, sheet_title=target_title)
        if sheet is None:
            available = [
                {
                    "gid": (item.get("properties") or {}).get("sheetId"),
                    "title": (item.get("properties") or {}).get("title"),
                }
                for item in meta.get("sheets") or []
            ]
            record["error"] = f'Sheet "{target_title}" was not found'
            record["parsed"] = {"available_sheets": available}
            record["hint"] = "Check sheet title or GOOGLE_SHEETS_SHEET_GID"
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return record

        props = sheet.get("properties") or {}
        resolved_title = props.get("title") or target_title
        spreadsheet_title = (meta.get("properties") or {}).get("title")
        resolved_gid = props.get("sheetId")

        values_result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{resolved_title}'")
            .execute()
        )
        values = values_result.get("values") or []
        matrix = _normalize_matrix(values)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        parsed: dict[str, Any] = {
            "format": "sheets_api_v4",
            "spreadsheet_title": spreadsheet_title,
            "sheet_title": resolved_title,
            "sheet_gid": resolved_gid,
            "row_count": len(matrix),
            "column_count": max((len(row) for row in matrix), default=0),
            "preview_rows": _preview_values(matrix, max_rows=preview_rows),
        }
        if include_values:
            parsed["values"] = matrix

        record["ok"] = True
        record["sheet_gid"] = str(resolved_gid) if resolved_gid is not None else sheet_gid
        record["sheet_title"] = resolved_title
        record["elapsed_ms"] = elapsed_ms
        record["hint"] = "Access via Google Sheets API v4 (Service Account)"
        record["parsed"] = parsed
        return record
    except GoogleSheetsConfigError as exc:
        record["error"] = str(exc)
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return record
    except ImportError as exc:
        record["error"] = f"Google API libraries not installed: {exc}"
        record["hint"] = "pip install google-auth google-api-python-client"
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return record
    except Exception as exc:
        message = str(exc)
        record["error"] = f"{type(exc).__name__}: {message}"
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        lowered = message.lower()
        if "403" in message or "permission" in lowered or "forbidden" in lowered:
            email = get_service_account_email() or "<service-account-email>"
            record["hint"] = (
                f"Share the spreadsheet with {email} (Viewer access)"
            )
        elif "404" in message or "not found" in lowered:
            record["hint"] = "Check GOOGLE_SHEETS_SPREADSHEET_ID"
        elif "invalid_grant" in lowered:
            record["hint"] = "Check Service Account JSON and that Sheets API is enabled"
        return record


def get_default_spreadsheet_target() -> tuple[str, str]:
    settings = get_settings()
    spreadsheet_id = (settings.GOOGLE_SHEETS_SPREADSHEET_ID or "").strip()
    sheet_gid = (settings.GOOGLE_SHEETS_SHEET_GID or "").strip()
    if not spreadsheet_id:
        raise GoogleSheetsConfigError("Set GOOGLE_SHEETS_SPREADSHEET_ID in .env")
    return spreadsheet_id, sheet_gid


def _values_matrix_to_xlsx_bytes(sheet_title: str, values: list[list[str]]) -> bytes:
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_title[:31]
    for row_index, row in enumerate(values, start=1):
        for col_index, cell in enumerate(row, start=1):
            if cell != "":
                worksheet.cell(row_index, col_index, cell)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def fetch_itc_sheet_workbook_payload() -> tuple[str, bytes] | None:
    """Скачивает лист «ИТЦ В РАБОТЕ» из Google Sheets как .xlsx для merge."""
    if not is_configured():
        return None

    try:
        spreadsheet_id, sheet_gid = get_default_spreadsheet_target()
    except GoogleSheetsConfigError:
        return None

    result = fetch_sheet_via_api(
        spreadsheet_id,
        sheet_gid or None,
        sheet_title=DEFAULT_SHEET_TITLE,
        include_values=True,
    )
    if not result.get("ok"):
        return None

    parsed = result.get("parsed") or {}
    values = parsed.get("values") or []
    if not values:
        return None

    sheet_title = str(parsed.get("sheet_title") or DEFAULT_SHEET_TITLE)
    filename = f"google_sheets_{sheet_title.replace('/', '-')}.xlsx"
    return filename, _values_matrix_to_xlsx_bytes(sheet_title, values)
