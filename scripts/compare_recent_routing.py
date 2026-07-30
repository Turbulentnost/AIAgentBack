"""Сверка последних писем: факт в БД vs пересчёт evidence / gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select

from agent_pochta.db.message_filters import load_payload_dict, recipient_display_value
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.evidence import (
    CHAIRMAN_DEPARTMENT_CODE,
    OD_DEPARTMENT_CODE,
    VED_DEPARTMENT_CODE,
    department_confidence_accepted,
)
from agent_pochta.schemas import EmailMessage

TERMINAL = ("done", "spam", "error", "awaiting_human", "dialog")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 25


def _email_from_row(row: EmailMessageRow, payload: dict) -> EmailMessage:
    received = row.received_at
    if received is not None and getattr(received, "tzinfo", None) is None:
        from datetime import timezone

        received = received.replace(tzinfo=timezone.utc)
    return EmailMessage(
        message_id=row.message_id or f"<db-{row.id}>",
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        sender_name=row.sender_name,
        subject=row.subject or "",
        body_text=str(payload.get("body_text") or payload.get("body") or "")[:8000],
        received_at=received,
        routing_recipient=str(
            payload.get("routing_recipient")
            or row.mailbox
            or ""
        ),
        reply_to=payload.get("reply_to"),
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    engine = RouteEngine.load()
    factory = get_session_factory()
    rows: list[EmailMessageRow] = []
    with factory() as session:
        rows = list(
            session.scalars(
                select(EmailMessageRow)
                .where(EmailMessageRow.status.in_(TERMINAL))
                .where(EmailMessageRow.processed_at.is_not(None))
                .order_by(EmailMessageRow.processed_at.desc())
                .limit(LIMIT)
            ).all()
        )
        for r in rows:
            session.expunge(r)

    report: list[dict] = []
    for row in rows:
        payload = load_payload_dict(row.raw_payload_json)
        rd = payload.get("routing_decision") or {}
        recipient = recipient_display_value(mailbox=row.mailbox, payload=payload)
        email = _email_from_row(row, payload)
        body = email.body_text or ""
        # combined: subject+body as stored / available
        combined = " ".join(
            p
            for p in (
                email.subject or "",
                body,
                str(payload.get("attachments_text") or "")[:4000],
            )
            if p
        ).strip()

        decision = route_email(
            email,
            combined_text=combined or email.subject or "",
            recipient=recipient or email.mailbox,
            engine=engine,
        )
        primary = decision.services[0] if decision.services else None
        accepted, gate_reason = department_confidence_accepted(
            department_code=primary.code if primary else "",
            score=decision.confidence_score,
            hard_count=decision.hard_signal_count,
            adaptive_count=decision.adaptive_signal_count,
            hard_foreign=decision.hard_foreign,
            has_conflict=decision.has_conflict,
        )

        db_dept = row.department_id or ""
        new_dept = primary.code if primary else ""
        match = db_dept == new_dept
        flags: list[str] = []
        if not match:
            flags.append("DEPT_MISMATCH")
        if new_dept == VED_DEPARTMENT_CODE and not decision.hard_foreign:
            flags.append("VED_NO_HARD_FOREIGN")
        if new_dept in {CHAIRMAN_DEPARTMENT_CODE, OD_DEPARTMENT_CODE} and not accepted:
            flags.append("LEADERSHIP_GATE_FAIL")
        if db_dept == VED_DEPARTMENT_CODE and not bool(rd.get("hard_foreign")):
            # старая запись могла не иметь поля
            if rd.get("match_source") not in {"det_foreign_domain", "exact_email"}:
                flags.append("DB_VED_SUSPECT")

        item = {
            "id": str(row.id),
            "processed_at": str(row.processed_at),
            "status": row.status,
            "erp": row.erp_document_number,
            "subject": (row.subject or "")[:120],
            "sender": row.sender_email,
            "recipient": recipient,
            "db": {
                "department_id": db_dept,
                "department_name": row.department_name,
                "dept_confidence": row.dept_confidence,
                "match_source": rd.get("match_source"),
                "confidence_score": rd.get("confidence_score"),
                "confidence_level": rd.get("confidence_level"),
                "hard_foreign": rd.get("hard_foreign"),
                "evidence_notes": rd.get("evidence_notes"),
                "hard_signal_count": rd.get("hard_signal_count"),
                "adaptive_signal_count": rd.get("adaptive_signal_count"),
            },
            "recompute": {
                "department_id": new_dept,
                "department_name": primary.name if primary else None,
                "match_source": decision.match_source,
                "confidence_score": decision.confidence_score,
                "confidence_level": decision.confidence_level.value,
                "hard_foreign": decision.hard_foreign,
                "hard_signal_count": decision.hard_signal_count,
                "adaptive_signal_count": decision.adaptive_signal_count,
                "evidence_notes": decision.evidence_notes,
                "gate_ok": accepted,
                "gate_reason": gate_reason,
                "reasoning": primary.reasoning if primary else None,
            },
            "match": match,
            "flags": flags,
        }
        report.append(item)

    summary = {
        "total": len(report),
        "dept_match": sum(1 for i in report if i["match"]),
        "dept_mismatch": sum(1 for i in report if not i["match"]),
        "with_new_evidence_in_db": sum(
            1
            for i in report
            if i["db"].get("evidence_notes") is not None
            or i["db"].get("hard_signal_count") is not None
        ),
        "flags": {},
    }
    for i in report:
        for f in i["flags"]:
            summary["flags"][f] = summary["flags"].get(f, 0) + 1

    out = {
        "summary": summary,
        "items": report,
    }
    out_path = ROOT / "data" / "temp" / "recent_routing_compare.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    for i in report:
        mark = "OK" if i["match"] else "DIFF"
        flags = ",".join(i["flags"]) if i["flags"] else "-"
        print(
            f"[{mark}] {i['processed_at'][:19]} | {i['erp'] or '-':12} | "
            f"DB={i['db']['department_id'] or '?':10} ({i['db'].get('match_source') or '?'}) "
            f"→ NEW={i['recompute']['department_id'] or '?':10} "
            f"({i['recompute']['match_source']}, "
            f"score={i['recompute']['confidence_score']}, "
            f"gate={i['recompute']['gate_ok']}) | {flags}"
        )
        print(f"     subj: {i['subject']}")
        print(f"     from: {i['sender']} → {i['recipient']}")
        if not i["match"] or i["flags"]:
            print(
                f"     db_name={i['db']['department_name']} | "
                f"new_name={i['recompute']['department_name']} | "
                f"new_reason={i['recompute']['reasoning']}"
            )
    print()
    print("saved", out_path)


if __name__ == "__main__":
    main()
