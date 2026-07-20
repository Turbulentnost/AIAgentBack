"""Одноразовая очистка: удалить тела писем и extracted_text из PostgreSQL.

Запуск (локально):
  python scripts/strip_email_bodies_from_db.py

Запуск в Docker (postgres на хосте 5433):
  docker compose exec api python scripts/strip_email_bodies_from_db.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.db.models import EmailAttachmentRow, EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.email_payload import sanitize_payload_for_storage


def main() -> None:
    factory = get_session_factory()
    messages_updated = 0
    payloads_skipped = 0
    attachments_cleared = 0
    bytes_before = 0
    bytes_after = 0

    with factory() as session:
        for row in session.query(EmailMessageRow).yield_per(200):
            if not row.raw_payload_json:
                payloads_skipped += 1
                continue
            try:
                payload = json.loads(row.raw_payload_json)
            except json.JSONDecodeError:
                payloads_skipped += 1
                continue
            if not isinstance(payload, dict):
                payloads_skipped += 1
                continue

            before = row.raw_payload_json
            sanitized = sanitize_payload_for_storage(payload)
            after = json.dumps(sanitized, ensure_ascii=False)
            bytes_before += len(before.encode("utf-8"))
            bytes_after += len(after.encode("utf-8"))
            if after != before:
                row.raw_payload_json = after
                messages_updated += 1

        attachments_cleared = (
            session.query(EmailAttachmentRow)
            .filter(EmailAttachmentRow.extracted_text.isnot(None))
            .update({EmailAttachmentRow.extracted_text: None}, synchronize_session=False)
        )
        session.commit()

    saved_kb = max(0, bytes_before - bytes_after) / 1024
    print(
        f"Готово: обновлено raw_payload_json={messages_updated}, "
        f"пропущено={payloads_skipped}, "
        f"очищено extracted_text вложений={attachments_cleared}, "
        f"освобождено ~{saved_kb:.1f} KiB"
    )


if __name__ == "__main__":
    main()
