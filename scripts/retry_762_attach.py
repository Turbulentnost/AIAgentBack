"""Retry ERP attach for АЛ00-000762 with force reattach."""
from __future__ import annotations

import json
import sys

from sqlalchemy import create_engine, text

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.workers.tasks import retry_erp_task  # noqa: E402

DOC = "АЛ00-000762"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, erp_document_number, erp_task_id, status "
                "FROM email_messages WHERE erp_document_number = :doc "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"doc": DOC},
        ).fetchone()
    if not row:
        print(json.dumps({"error": f"no row for {DOC}"}, ensure_ascii=False))
        raise SystemExit(1)

    message_id = row.message_id
    print("retry", message_id, row.status, row.erp_task_id)
    result = retry_erp_task(message_id, force_reattach_eml=True)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
