"""Inspect memo text fields from 1C OData."""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.get_meetings import fetch_document_header, fetch_meeting_memo_rows


def main() -> None:
    number = sys.argv[1] if len(sys.argv) > 1 else "000011844"
    session = create_session(CONFIG)
    safe = number.replace("'", "''")
    rows = fetch_meeting_memo_rows(
        session,
        CONFIG,
        f"Number eq '{safe}'",
        limit=1,
        fetch_pool=5,
    )
    if not rows:
        print(f"NOT FOUND: {number}")
        return
    header = fetch_document_header(session, CONFIG, rows[0]["Ref_Key"])
    row = rows[0]
    print(f"Number: {header.get('Number')}")
    print("--- list row ---")
    for key in sorted(row):
        if any(token in key for token in ("Текст", "Цель", "Тема", "План", "Коммент")):
            value = row.get(key)
            if value and str(value).strip():
                print(f"{key} = {value!r}")
    print("--- full header ---")
    for key in sorted(header):
        if any(token in key for token in ("Текст", "Цель", "Тема", "План", "Коммент")):
            value = header.get(key)
            if value and str(value).strip():
                print(f"{key} = {value!r}")


if __name__ == "__main__":
    main()
