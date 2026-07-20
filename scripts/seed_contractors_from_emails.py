"""Загрузка контрагентов из обработанных писем в Qdrant и erp_contractors.

Примеры:
  python scripts/seed_contractors_from_emails.py
  python scripts/seed_contractors_from_emails.py --dry-run
  python scripts/seed_contractors_from_emails.py --skip-db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.catalog_repository import CatalogRepository  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.schemas import ProcessingStatus  # noqa: E402
from agent_pochta.services.contractor_seed import (  # noqa: E402
    extract_contractors_from_messages,
    to_contractor,
)
from agent_pochta.services.rag_qdrant import upsert_contractors_merge  # noqa: E402


def _load_email_rows(*, include_spam: bool) -> list[tuple[str, str | None, str | None]]:
    factory = get_session_factory()
    with factory() as session:
        query = session.query(
            EmailMessageRow.sender_email,
            EmailMessageRow.sender_name,
            EmailMessageRow.raw_payload_json,
        ).filter(EmailMessageRow.raw_payload_json.isnot(None))
        if not include_spam:
            query = query.filter(EmailMessageRow.status != ProcessingStatus.SPAM.value)
        return list(query.all())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Qdrant contractors from processed email_messages"
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать статистику")
    parser.add_argument(
        "--include-spam",
        action="store_true",
        help="Включить письма со статусом spam",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Не писать в erp_contractors (только Qdrant)",
    )
    parser.add_argument(
        "--skip-qdrant",
        action="store_true",
        help="Не писать в Qdrant (только erp_contractors)",
    )
    args = parser.parse_args()

    rows = _load_email_rows(include_spam=args.include_spam)
    extracted = extract_contractors_from_messages(rows)
    contractors = [to_contractor(item) for item in extracted]

    print(f"Писем в выборке: {len(rows)}")
    print(f"Уникальных контрагентов (по email): {len(contractors)}")
    for item in extracted[:5]:
        print(f"  • {item.email} → {item.name}")
    if len(extracted) > 5:
        print(f"  ... ещё {len(extracted) - 5}")

    if args.dry_run:
        print("\n--dry-run: запись пропущена.")
        return

    settings = get_settings()
    qdrant_points = 0
    if not args.skip_qdrant:
        if settings.rag_backend != "qdrant":
            print("\nRAG_BACKEND != qdrant — пропуск Qdrant.")
        else:
            qdrant_points = upsert_contractors_merge(settings.qdrant_url, contractors)
            print(f"\nQdrant: upsert {qdrant_points} точек (коллекция contractors)")

    db_count = 0
    if not args.skip_db:
        factory = get_session_factory()
        with factory() as session:
            repo = CatalogRepository(session)
            run_id = repo.begin_sync_run("email", notes="seed_contractors_from_emails.py")
            db_count = repo.upsert_contractors(contractors, source="email", sync_run_id=run_id)
            repo.finish_sync_run(
                run_id,
                status="done",
                contractors_count=db_count,
                departments_count=0,
            )
            session.commit()
        print(f"PostgreSQL erp_contractors: upsert {db_count} записей (source=email)")

    print(f"\nГотово: {len(contractors)} контрагентов.")


if __name__ == "__main__":
    main()
