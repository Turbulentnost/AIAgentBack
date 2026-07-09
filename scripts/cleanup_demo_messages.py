"""Удаление демо/тестовых писем из PostgreSQL.

Запуск:
  python scripts/cleanup_demo_messages.py
  python scripts/cleanup_demo_messages.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.demo_filter import demo_row_filter  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Удалить демо/тестовые письма из БД")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, сколько записей будет удалено",
    )
    args = parser.parse_args()

    factory = get_session_factory()
    with factory() as session:
        rows = (
            session.query(EmailMessageRow)
            .filter(demo_row_filter(EmailMessageRow))
            .order_by(EmailMessageRow.received_at.desc())
            .all()
        )
        if rows:
            print("Найдены демо/тестовые записи:")
            for row in rows:
                print(f"  - {row.message_id} | {row.sender_email} | {row.subject!r}")
        else:
            print("Демо/тестовые записи не найдены.")
            return

        if args.dry_run:
            print(f"\nDry-run: будет удалено записей: {len(rows)}")
            return

        deleted = EmailRepository(session).delete_demo_messages()
        session.commit()
        print(f"\nУдалено записей: {deleted}")


if __name__ == "__main__":
    main()
