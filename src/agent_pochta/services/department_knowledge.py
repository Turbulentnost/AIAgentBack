"""Анализ и подготовка базы знаний по коррекциям отделов."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agent_pochta.config import PROJECT_ROOT, get_settings
from agent_pochta.db.message_filters import load_payload_dict, resolved_turbo_recipient
from agent_pochta.db.models import ClassificationEventRow, EmailMessageRow
from agent_pochta.routing.corrections import load_corrections


@dataclass
class DepartmentCorrectionRecord:
    source: str
    email_id: str | None
    message_id: str | None
    recipient: str
    sender_email: str
    subject: str
    embed_text: str
    dept_wrong_id: str
    dept_wrong_name: str
    dept_correct_id: str
    dept_correct_name: str
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_department_display_names() -> dict[str, str]:
    names: dict[str, str] = {}
    tz_path = PROJECT_ROOT / "data" / "tz_department_topics.json"
    if tz_path.exists():
        tz = json.loads(tz_path.read_text(encoding="utf-8"))
        for code, meta in tz.items():
            if isinstance(meta, dict):
                if meta.get("topics"):
                    names[code] = str(meta["topics"][0])
                elif meta.get("names"):
                    names[code] = str(meta["names"][0])
    rules_path = PROJECT_ROOT / "data" / "routing_rules.json"
    if rules_path.exists():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        for rule in rules.get("exact_email_rules", []) + rules.get("content_rules", []):
            if rule.get("code") and rule.get("name"):
                names.setdefault(rule["code"], rule["name"])
    ui_path = PROJECT_ROOT / "data" / "ui_department_allowlist.json"
    if ui_path.exists():
        ui = json.loads(ui_path.read_text(encoding="utf-8"))
        for item in ui.get("departments", []):
            names.setdefault(item["code"], item["name"])
    return names


def dept_name(names: dict[str, str], code: str | None, fallback: str | None = None) -> str:
    if not code:
        return fallback or ""
    return names.get(code, fallback or code)


def build_unified_embed_text(
    *,
    subject: str | None,
    sender_email: str | None,
    summary_ru: str | None = None,
    body_text: str | None = None,
    attachment_blocks: list[tuple[str, str, str]] | None = None,
    stored_source: str | None = None,
) -> str:
    """Единый текст для BGE: тема + отправитель + summary + тело + вложения."""
    if stored_source and stored_source.strip():
        return stored_source.strip()

    parts: list[str] = []
    if subject:
        parts.append(f"Тема: {subject.strip()}")
    if sender_email:
        parts.append(f"От: {sender_email.strip()}")
    if summary_ru:
        parts.append(f"Краткое содержание: {summary_ru.strip()}")
    if body_text and body_text.strip():
        parts.append(body_text.strip())

    blocks: list[str] = []
    for filename, mime_type, text in attachment_blocks or []:
        cleaned = (text or "").strip()
        if cleaned:
            blocks.append(f"--- {filename} ({mime_type}) ---\n{cleaned}")
    if blocks:
        parts.append("=== ВЛОЖЕНИЯ ===\n" + "\n\n".join(blocks))

    return "\n\n".join(part for part in parts if part.strip())


def build_unified_embed_text_from_row(row: EmailMessageRow) -> str:
    payload = load_payload_dict(row.raw_payload_json) or {}
    stored = payload.get("embedding_source_text")
    attachment_blocks = [
        (att.filename or "file", att.mime_type or "", att.extracted_text or "")
        for att in (row.attachments or [])
    ]
    return build_unified_embed_text(
        subject=row.subject,
        sender_email=row.sender_email,
        summary_ru=row.summary_ru,
        body_text=str(payload.get("body_text") or ""),
        attachment_blocks=attachment_blocks,
        stored_source=str(stored) if stored else None,
    )


def _fingerprint(*, sender: str, recipient: str, subject: str) -> str:
    subj = " ".join((subject or "").lower().split())[:120]
    return f"{sender.lower().strip()}|{recipient.lower().strip()}|{subj}"


def _records_from_postgres(session: Session, names: dict[str, str]) -> list[DepartmentCorrectionRecord]:
    stmt = (
        select(ClassificationEventRow)
        .where(
            ClassificationEventRow.category == "department",
            ClassificationEventRow.event_type == "operator_change",
            ClassificationEventRow.old_department_id.isnot(None),
            ClassificationEventRow.new_department_id.isnot(None),
        )
        .order_by(ClassificationEventRow.created_at.desc())
    )
    events = session.scalars(stmt).all()
    email_ids = {event.email_id for event in events if event.email_id}
    rows_by_id: dict[Any, EmailMessageRow] = {}
    if email_ids:
        email_rows = session.scalars(
            select(EmailMessageRow)
            .where(EmailMessageRow.id.in_(email_ids))
            .options(selectinload(EmailMessageRow.attachments))
        ).all()
        rows_by_id = {row.id: row for row in email_rows}

    records: list[DepartmentCorrectionRecord] = []
    for event in events:
        old_id = (event.old_department_id or "").strip()
        new_id = (event.new_department_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            continue

        row = rows_by_id.get(event.email_id) if event.email_id else None
        payload = load_payload_dict(row.raw_payload_json) if row else None
        mailbox = row.mailbox if row else ""
        recipient = resolved_turbo_recipient(mailbox=mailbox, payload=payload)
        if not recipient and row:
            recipient = (payload or {}).get("routing_recipient") or row.mailbox or ""

        sender = row.sender_email if row else ""
        subject = row.subject if row else ""
        embed_text = build_unified_embed_text_from_row(row) if row else subject

        records.append(
            DepartmentCorrectionRecord(
                source="operator_change",
                email_id=str(row.id) if row else None,
                message_id=event.message_id,
                recipient=recipient,
                sender_email=sender,
                subject=subject or "",
                embed_text=embed_text,
                dept_wrong_id=old_id,
                dept_wrong_name=dept_name(names, old_id, event.old_department_name),
                dept_correct_id=new_id,
                dept_correct_name=dept_name(names, new_id, event.new_department_name),
                fingerprint=_fingerprint(sender=sender, recipient=recipient, subject=subject or ""),
            )
        )
    return records


def _records_from_routing_corrections(names: dict[str, str]) -> list[DepartmentCorrectionRecord]:
    records: list[DepartmentCorrectionRecord] = []
    for entry in load_corrections().get("entries", []):
        wrong_id = (entry.get("original_department_id") or "").strip()
        correct_id = (entry.get("department_id") or "").strip()
        if not correct_id:
            continue
        if wrong_id and wrong_id == correct_id:
            continue

        recipient = str(entry.get("recipient") or "").strip().lower()
        sender = str(entry.get("sender_email") or "").strip()
        subject = str(entry.get("subject") or "").strip()
        keywords = entry.get("keywords") or []
        embed_parts = [f"Тема: {subject}", f"От: {sender}", f"Кому: {recipient}"]
        if keywords:
            embed_parts.append("Ключевые слова: " + ", ".join(keywords))
        embed_text = "\n\n".join(part for part in embed_parts if part.strip())

        records.append(
            DepartmentCorrectionRecord(
                source="routing_correction",
                email_id=None,
                message_id=entry.get("message_id"),
                recipient=recipient,
                sender_email=sender,
                subject=subject,
                embed_text=embed_text,
                dept_wrong_id=wrong_id or "",
                dept_wrong_name=dept_name(
                    names, wrong_id, entry.get("original_department_name")
                ),
                dept_correct_id=correct_id,
                dept_correct_name=dept_name(names, correct_id, entry.get("department_name")),
                fingerprint=_fingerprint(sender=sender, recipient=recipient, subject=subject),
            )
        )
    return records


def collect_department_correction_records(session: Session | None = None) -> list[DepartmentCorrectionRecord]:
    names = load_department_display_names()
    combined: list[DepartmentCorrectionRecord] = []
    combined.extend(_records_from_routing_corrections(names))

    if session is not None:
        combined.extend(_records_from_postgres(session, names))

    seen: set[str] = set()
    deduped: list[DepartmentCorrectionRecord] = []
    for record in combined:
        key = record.fingerprint or f"{record.source}:{record.message_id}:{record.subject}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def write_analysis_outputs(
    records: list[DepartmentCorrectionRecord],
    *,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    import csv

    out_dir = out_dir or (PROJECT_ROOT / "data" / "stats")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "department_knowledge_analysis.json"
    csv_path = out_dir / "department_knowledge_analysis.csv"

    payload = {
        "count": len(records),
        "records": [record.to_dict() for record in records],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "recipient",
                "sender_email",
                "embed_text",
                "dept_wrong_id",
                "dept_wrong_name",
                "dept_correct_id",
                "dept_correct_name",
                "source",
                "subject",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "recipient": record.recipient,
                    "sender_email": record.sender_email,
                    "embed_text": record.embed_text,
                    "dept_wrong_id": record.dept_wrong_id,
                    "dept_wrong_name": record.dept_wrong_name,
                    "dept_correct_id": record.dept_correct_id,
                    "dept_correct_name": record.dept_correct_name,
                    "source": record.source,
                    "subject": record.subject,
                }
            )
    return json_path, csv_path
