"""Аудит routing_decision из БД vs новая логика (без полного тела)."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select

from agent_pochta.db.message_filters import load_payload_dict, recipient_display_value
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.deterministic_sales import (
    is_commercial_ru_context,
    match_foreign_domain_route,
)

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 40


def ved_verdict(item: dict) -> str:
    src = item.get("match_source") or ""
    if src == "det_foreign_domain" or item.get("sender_looks_foreign"):
        return "OK_hard_foreign"
    if src == "exact_email" and "oved" in (item.get("recipient") or "").lower():
        return "OK_mailbox_ved"
    if src.startswith("dialog"):
        return "DIALOG_override"
    if not item.get("sender_looks_foreign"):
        return "WOULD_BLOCK_no_hard_foreign"
    return "REVIEW"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    factory = get_session_factory()
    with factory() as session:
        rows = list(
            session.scalars(
                select(EmailMessageRow)
                .where(EmailMessageRow.processed_at.is_not(None))
                .order_by(EmailMessageRow.processed_at.desc())
                .limit(LIMIT)
            ).all()
        )
        for row in rows:
            session.expunge(row)

    src_c: Counter[str] = Counter()
    status_c: Counter[str] = Counter()
    dept_c: Counter[str] = Counter()
    items: list[dict] = []
    ved_cases: list[dict] = []
    leadership: list[dict] = []

    for row in rows:
        payload = load_payload_dict(row.raw_payload_json) or {}
        rd = payload.get("routing_decision") or {}
        status_c[row.status] += 1
        src = rd.get("match_source") or (
            "(none)" if not row.department_id else "(no_meta)"
        )
        src_c[src] += 1
        if row.department_id:
            dept_c[row.department_id] += 1

        recipient = recipient_display_value(mailbox=row.mailbox, payload=payload)
        sender = row.sender_email or ""
        foreign = match_foreign_domain_route(
            subject=row.subject or "",
            body="",
            sender_email=sender,
        )
        commercial_ru = is_commercial_ru_context(
            subject=row.subject or "",
            body="",
            sender_email=sender,
        )
        item = {
            "id": str(row.id),
            "processed_at": str(row.processed_at),
            "status": row.status,
            "erp": row.erp_document_number,
            "subject": (row.subject or "")[:120],
            "sender": sender,
            "recipient": recipient,
            "department_id": row.department_id,
            "department_name": row.department_name,
            "dept_confidence": row.dept_confidence,
            "match_source": src,
            "confidence_score": rd.get("confidence_score"),
            "confidence_level": rd.get("confidence_level"),
            "hard_foreign_meta": rd.get("hard_foreign"),
            "evidence_notes": rd.get("evidence_notes"),
            "hard_signal_count": rd.get("hard_signal_count"),
            "sender_looks_foreign": foreign is not None,
            "commercial_ru_subject": commercial_ru,
            "has_new_evidence_fields": rd.get("evidence_notes") is not None
            or rd.get("hard_signal_count") is not None,
            "body_len": len(str(payload.get("body_text") or "")),
            "dialog": (payload.get("dialog") or {}).get("mode")
            if isinstance(payload.get("dialog"), dict)
            else None,
        }
        if row.department_id == "00-000015":
            item["new_logic_verdict"] = ved_verdict(item)
            ved_cases.append(item)
        if row.department_id in {"00-000001", "00-000152"}:
            leadership.append(item)
        items.append(item)

    out = {
        "note": (
            "Письма в БД обработаны пайплайном БЕЗ полей evidence "
            "(Docker ещё без нового кода). Сверка ВЭД — по sender/subject/recipient."
        ),
        "summary": {
            "total": len(items),
            "status": dict(status_c),
            "match_source": dict(src_c.most_common()),
            "top_departments": dept_c.most_common(12),
            "with_new_evidence_fields": sum(
                1 for i in items if i["has_new_evidence_fields"]
            ),
            "with_body_cached": sum(1 for i in items if i["body_len"] > 0),
            "ved_count": len(ved_cases),
            "ved_would_block": sum(
                1
                for v in ved_cases
                if v.get("new_logic_verdict") == "WOULD_BLOCK_no_hard_foreign"
            ),
            "leadership_count": len(leadership),
        },
        "ved_cases": ved_cases,
        "leadership_cases": leadership,
        "items": items,
    }
    path = ROOT / "data" / "temp" / "recent_mail_db_audit.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== SUMMARY ===")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("\n=== ВЭД vs новая логика ===")
    for v in ved_cases:
        print(
            v["processed_at"][:19],
            "|",
            (v["match_source"] or "")[:20].ljust(20),
            "|",
            v["new_logic_verdict"].ljust(28),
            "|",
            v["sender"],
            "|",
            (v["subject"] or "")[:55],
        )
    print("\n=== Leadership (001/152) ===")
    if not leadership:
        print("нет в выборке")
    for v in leadership:
        print(
            v["processed_at"][:19],
            v["department_id"],
            v["match_source"],
            v["confidence_score"],
            (v["subject"] or "")[:60],
        )
    print("\nsaved", path)


if __name__ == "__main__":
    main()
