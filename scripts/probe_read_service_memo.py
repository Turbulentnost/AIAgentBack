"""Пробный запрос на чтение СЗ из 1С OData."""
from __future__ import annotations

import sys
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8")

from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    build_meeting_theme_text_filter,
    entity_url,
    fetch_document_header,
    fetch_meeting_memo_rows,
    meeting_theme,
    odata_get_json,
)


def main() -> int:
    session = create_session(CONFIG)
    print(f"URL: {CONFIG.url}")
    print(f"User: {CONFIG.user}")
    print(f"Theme: {meeting_theme()}")
    print()

    def report(name: str, status: object, detail: str) -> None:
        print(f"[{status}] {name}: {detail}")

    try:
        response = session.get(f"{CONFIG.url.rstrip('/')}/$metadata", timeout=30)
        report("$metadata", response.status_code, response.text[:120].replace("\n", " "))
    except Exception as exc:
        report("$metadata", "ERR", str(exc))

    try:
        url = f"{entity_url(CONFIG.url, 'Catalog_Пользователи')}?$top=1&$format=json"
        data = odata_get_json(session, url, timeout=30)
        row = (data.get("value") or [{}])[0]
        report("Catalog_Пользователи", 200, str(row.get("Description", ""))[:80])
    except Exception as exc:
        report("Catalog_Пользователи", "ERR", str(exc)[:240])

    try:
        url = f"{entity_url(CONFIG.url, DOCUMENT_ENTITY)}?$top=1&$orderby=Date desc&$format=json"
        response = session.get(url, timeout=60)
        if response.ok:
            row = (response.json().get("value") or [{}])[0]
            report(
                "Document top1 (no filter)",
                response.status_code,
                f"Number={row.get('Number')} Status={row.get('Статус')}",
            )
        else:
            report("Document top1 (no filter)", response.status_code, response.text[:240])
    except Exception as exc:
        report("Document top1 (no filter)", "ERR", str(exc)[:240])

    try:
        flt = build_meeting_theme_text_filter()
        url = (
            f"{entity_url(CONFIG.url, DOCUMENT_ENTITY)}"
            f"?$filter={quote(flt, safe='')}"
            f"&$top=3&$orderby=Date desc&$format=json"
        )
        response = session.get(url, timeout=60)
        if response.ok:
            rows = response.json().get("value") or []
            sample = [
                f"{item.get('Number')}|{item.get('Статус')}"
                for item in rows[:3]
            ]
            report("Document theme filter", response.status_code, "; ".join(sample) or "empty")
        else:
            report("Document theme filter", response.status_code, response.text[:240])
    except Exception as exc:
        report("Document theme filter", "ERR", str(exc)[:240])

    for number in ("000010703", "000010430"):
        try:
            safe = number.replace("'", "''")
            rows = fetch_meeting_memo_rows(
                session,
                CONFIG,
                f"Number eq '{safe}'",
                limit=1,
                fetch_pool=5,
            )
            if rows:
                row = rows[0]
                report(
                    f"fetch_meeting_memo_rows {number}",
                    200,
                    f"Ref={row.get('Ref_Key')} Number={row.get('Number')} Status={row.get('Статус')}",
                )
            else:
                report(f"fetch_meeting_memo_rows {number}", 200, "not found")
        except Exception as exc:
            report(f"fetch_meeting_memo_rows {number}", "ERR", str(exc)[:300])

    try:
        rows = fetch_meeting_memo_rows(session, CONFIG, "", limit=1, fetch_pool=3)
        if not rows:
            report("fetch_document_header", "SKIP", "no themed rows")
        else:
            header = fetch_document_header(session, CONFIG, rows[0]["Ref_Key"])
            report(
                "fetch_document_header",
                200,
                f"Number={header.get('Number')} Status={header.get('Статус')} "
                f"Manager={header.get('РуководительСовещания')}",
            )
    except Exception as exc:
        report("fetch_document_header", "ERR", str(exc)[:300])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
