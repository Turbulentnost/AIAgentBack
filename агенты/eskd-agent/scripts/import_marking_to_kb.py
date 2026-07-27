#!/usr/bin/env python3
"""Upload PDFs to marking and mark as human-verified in knowledge base."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

DEFAULT_BASE = "http://localhost:3000"
VERIFY_REPORT = "Проверено экспертом (импорт в базу знаний)"


def import_folder(folder: Path, base_url: str) -> int:
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"Нет PDF в {folder}", file=sys.stderr)
        return 1

    ok = 0
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=600.0) as client:
        for pdf in pdfs:
            print(f"→ {pdf.name}")
            with pdf.open("rb") as fh:
                resp = client.post(
                    "/api/v1/eskd/marking/documents",
                    files={"file": (pdf.name, fh, "application/pdf")},
                )
            resp.raise_for_status()
            doc = resp.json()
            label_resp = client.post(
                "/api/v1/eskd/marking/labels",
                json={
                    "document_id": doc["id"],
                    "document_level": [],
                    "page_level": [],
                    "problem_report": VERIFY_REPORT,
                },
            )
            label_resp.raise_for_status()
            ok += 1
            print(f"  ✓ id={doc['id']} pages={len(doc.get('pages') or [])}")

    print(f"Готово: {ok} файл(ов)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder",
        nargs="?",
        default="/home/td-user/agent_nd/документы для разметки/UFG-600",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    args = parser.parse_args()
    return import_folder(Path(args.folder), args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
