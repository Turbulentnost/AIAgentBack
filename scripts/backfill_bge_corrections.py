"""Backfill operator corrections into department_corrections_bge (dedup by email_id)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.corrections import load_corrections  # noqa: E402
from agent_pochta.services.bge_correction_learning import upsert_correction_from_row  # noqa: E402
from agent_pochta.services.email_rag_qdrant import ensure_department_corrections_collection  # noqa: E402
from agent_pochta.db.repository import EmailRepository  # noqa: E402


def _dedupe_entries(entries: list[dict]) -> list[dict]:
    """Keep latest correction per message_id / sender+recipient+subject."""
    by_key: dict[str, dict] = {}
    for entry in entries:
        message_id = str(entry.get("message_id") or "").strip()
        if message_id:
            key = f"msg:{message_id}"
        else:
            key = "|".join(
                [
                    str(entry.get("sender_email") or "").lower().strip(),
                    str(entry.get("recipient") or "").lower().strip(),
                    str(entry.get("subject") or "").lower().strip()[:120],
                ]
            )
        by_key[key] = entry
    return list(by_key.values())


def backfill(
    *,
    reextract: bool = False,
    limit: int | None = None,
    corrections_path: Path | None = None,
) -> dict:
    settings = get_settings()
    ensure_department_corrections_collection(settings.qdrant_url)

    store = load_corrections(corrections_path)
    entries = _dedupe_entries(list(store.get("entries") or []))
    if limit is not None:
        entries = entries[:limit]

    factory = get_session_factory()
    ok = 0
    skipped = 0
    failed = 0
    details: list[dict] = []

    with factory() as session:
        repo = EmailRepository(session)
        for entry in entries:
            wrong_id = str(entry.get("original_department_id") or "").strip()
            correct_id = str(entry.get("department_id") or "").strip()
            if not correct_id or (wrong_id and wrong_id == correct_id):
                skipped += 1
                continue

            message_id = str(entry.get("message_id") or "").strip()
            row = None
            if message_id:
                row = repo.get_by_message_id(message_id)
            if row is None and entry.get("sender_email") and entry.get("recipient"):
                row = session.scalar(
                    select(EmailMessageRow)
                    .where(EmailMessageRow.sender_email == entry["sender_email"])
                    .where(EmailMessageRow.mailbox == entry["recipient"])
                    .order_by(EmailMessageRow.received_at.desc())
                    .limit(1)
                    .options(selectinload(EmailMessageRow.attachments))
                )

            if row is None:
                skipped += 1
                details.append({"entry_id": entry.get("id"), "reason": "row_not_found"})
                continue

            result = upsert_correction_from_row(
                repo,
                row,
                wrong_dept_id=wrong_id or None,
                wrong_dept_name=str(entry.get("original_department_name") or ""),
                correct_dept_id=correct_id,
                correct_dept_name=str(entry.get("department_name") or ""),
                reextract=reextract,
                settings=settings,
            )
            if result.get("ok"):
                session.commit()
                ok += 1
            elif result.get("skipped"):
                skipped += 1
            else:
                failed += 1
            details.append({"entry_id": entry.get("id"), **result})

    return {
        "ok": failed == 0,
        "processed": len(entries),
        "upserted": ok,
        "skipped": skipped,
        "failed": failed,
        "reextract": reextract,
        "details": details[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill BGE department corrections")
    parser.add_argument("--reextract", action="store_true", help="Re-fetch full body via IMAP")
    parser.add_argument("--all", action="store_true", help="Process all JSON entries")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--corrections-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "routing_corrections.json",
    )
    args = parser.parse_args()
    if not args.all and args.limit is None:
        args.limit = 50

    result = backfill(
        reextract=args.reextract,
        limit=args.limit,
        corrections_path=args.corrections_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
