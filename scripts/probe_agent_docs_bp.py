"""Compare done vs spam agent docs for business process state."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from sqlalchemy import select  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
ENTITIES = ("BusinessProcess_Задание", "BusinessProcess_CRM_БизнесПроцесс", "Task_ЗадачаИсполнителя")


def process_hits(base: str, auth: tuple[str, str], doc_ref: str) -> dict:
    out = {}
    for entity in ENTITIES:
        flt = f"Предмет eq '{doc_ref}'"
        url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=10"
        resp = httpx.get(url, auth=auth, timeout=120)
        items = resp.json().get("value", []) if resp.status_code == 200 else []
        out[entity] = len(items)
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    with get_session_factory()() as session:
        rows = session.scalars(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_task_id.is_not(None))
            .order_by(EmailMessageRow.received_at.desc())
            .limit(8)
        ).all()

    report = []
    for row in rows:
        ref = (row.erp_task_id or "").strip()
        if not ref or ref.startswith("SKIP") or ref.startswith("TASK-STUB"):
            continue
        doc = httpx.get(
            f"{base}{quote(DOC_ENTITY)}(guid'{ref}')?$format=json",
            auth=auth,
            timeout=120,
        )
        if doc.status_code != 200:
            continue
        data = doc.json()
        report.append(
            {
                "number": row.erp_document_number,
                "status_agent": row.status,
                "is_spam": row.is_spam,
                "doc_status": data.get("Статус"),
                "bp_started": data.get("БизнесПроцессЗапущен"),
                "posted": data.get("Posted"),
                "process_hits": process_hits(base, auth, ref),
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
