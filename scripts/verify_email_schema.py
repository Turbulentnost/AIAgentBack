"""Проверка модели PostgreSQL (ТЗ §7): email_messages, email_attachments.

Запуск:  python scripts/verify_email_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import inspect, text  # noqa: E402

from agent_pochta.db.models import Base, EmailAttachmentRow, EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402

EXPECTED_TABLES = ("email_messages", "email_attachments")


def _model_columns(model) -> set[str]:
    return {c.key for c in model.__mapper__.columns}


def main() -> None:
    factory = get_session_factory()
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
            inspector = inspect(session.bind)
            tables = set(inspector.get_table_names())
    except Exception as exc:
        print(f"FAIL: PostgreSQL недоступен ({exc})")
        print("\n1. Запустите Docker Desktop")
        print("2. docker compose up -d")
        print("3. python scripts/run_migrate.py")
        sys.exit(1)

    ok = True
    for name in EXPECTED_TABLES:
        if name not in tables:
            print(f"FAIL: таблица {name} отсутствует — выполните python scripts/run_migrate.py")
            ok = False

    if not ok:
        sys.exit(1)

    msg_cols = {c["name"] for c in inspector.get_columns("email_messages")}
    att_cols = {c["name"] for c in inspector.get_columns("email_attachments")}
    missing_msg = _model_columns(EmailMessageRow) - msg_cols
    missing_att = _model_columns(EmailAttachmentRow) - att_cols

    if missing_msg:
        print(f"FAIL: email_messages — нет колонок: {sorted(missing_msg)}")
        ok = False
    if missing_att:
        print(f"FAIL: email_attachments — нет колонок: {sorted(missing_att)}")
        ok = False

    if not ok:
        sys.exit(1)

    with factory() as session:
        msg_count = session.execute(text("SELECT COUNT(*) FROM email_messages")).scalar()
        att_count = session.execute(text("SELECT COUNT(*) FROM email_attachments")).scalar()

    print("OK: модель данных PostgreSQL (ТЗ §7)")
    print(f"  email_messages:    {len(msg_cols)} колонок, {msg_count} записей")
    print(f"  email_attachments: {len(att_cols)} колонок, {att_count} записей")
    print("\nКолонки email_messages:")
    for col in sorted(msg_cols):
        print(f"    • {col}")
    print("\nКолонки email_attachments:")
    for col in sorted(att_cols):
        print(f"    • {col}")


if __name__ == "__main__":
    main()
