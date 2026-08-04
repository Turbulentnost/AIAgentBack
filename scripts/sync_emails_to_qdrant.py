"""Backfill векторизации писем (тело + вложения) в Qdrant через BGE.

Запуск:
  python scripts/sync_emails_to_qdrant.py
  python scripts/sync_emails_to_qdrant.py --limit 200 --force
  python scripts/sync_emails_to_qdrant.py --since-days 7
  python scripts/sync_emails_to_qdrant.py --limit 200 --force --reextract
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy.orm import selectinload  # noqa: E402

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.email_indexing import index_email_row  # noqa: E402
from agent_pochta.services.email_rag_qdrant import ensure_email_messages_collection  # noqa: E402


def _needs_index(row: EmailMessageRow, *, force: bool) -> bool:
    if force:
        return True
    if not row.raw_payload_json:
        return True
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return True
    return not payload.get("qdrant_indexed_at")


def sync_pending_emails(
    *,
    limit: int = 50,
    force: bool = False,
    reextract: bool = False,
    since_days: int | None = None,
) -> dict:
    settings = get_settings()
    if not settings.email_rag_enabled:
        return {"ok": False, "skipped": True, "reason": "EMAIL_RAG_ENABLED=false"}
    if settings.rag_backend != "qdrant":
        return {"ok": False, "skipped": True, "reason": "RAG_BACKEND!=qdrant"}
    if not settings.embedding_base_url:
        return {"ok": False, "skipped": True, "reason": "EMBEDDING_BASE_URL empty"}

    ensure_email_messages_collection(settings.qdrant_url)

    since_dt = None
    if since_days is not None:
        since_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=since_days)

    indexed = 0
    skipped = 0
    errors = 0
    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        query = (
            session.query(EmailMessageRow)
            .options(selectinload(EmailMessageRow.attachments))
            .order_by(EmailMessageRow.received_at.desc())
        )
        if since_dt is not None:
            query = query.filter(EmailMessageRow.received_at >= since_dt)
        rows = query.limit(max(limit * 5, limit)).all()
        for row in rows:
            if indexed >= limit:
                break
            if not _needs_index(row, force=force):
                skipped += 1
                continue
            result = index_email_row(repo, row, force=force, reextract=reextract)
            if result.get("ok") and not result.get("skipped"):
                indexed += 1
                session.commit()
            elif result.get("skipped"):
                skipped += 1
            else:
                errors += 1
                session.rollback()

    return {
        "ok": True,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "limit": limit,
        "force": force,
        "reextract": reextract,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync email vectors to Qdrant (BGE)")
    parser.add_argument("--limit", type=int, default=get_settings().email_rag_sync_batch_size)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="Fetch body+attachments from IMAP and rebuild full text before embed",
    )
    parser.add_argument("--since-days", type=int, default=None)
    args = parser.parse_args()
    result = sync_pending_emails(
        limit=args.limit,
        force=args.force,
        reextract=args.reextract,
        since_days=args.since_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
