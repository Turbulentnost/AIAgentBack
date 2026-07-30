"""Синхронизация названий подразделений с Catalog_СтруктураПредприятия (enterprise_positions.json).

Обновляет PostgreSQL: departments, email_messages, classification_events.

Запуск:
  python scripts/sync_department_names_from_1c.py --dry-run
  python scripts/sync_department_names_from_1c.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.db.department_repository import DepartmentRepository, seed_departments_to_db  # noqa: E402
from agent_pochta.db.models import (  # noqa: E402
    ClassificationEventRow,
    DepartmentRow,
    EmailMessageRow,
)
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.xml_parser import parse_document_xml, rebuild_xml_document_from_row  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_department_records_for_db,
    load_onec_department_names_map,
    resolve_department_display_name,
)


def _email_from_row(row: EmailMessageRow) -> EmailMessage:
    payload: dict = {}
    if row.raw_payload_json:
        try:
            parsed = json.loads(row.raw_payload_json)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            pass
    return EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox,
        sender_email=row.sender_email,
        sender_name=row.sender_name,
        subject=row.subject or "",
        received_at=row.received_at,
        routing_recipient=payload.get("routing_recipient") or row.mailbox,
    )


def _xml_titles_need_update(xml: str, names: dict[str, str]) -> bool:
    doc = parse_document_xml(xml)
    for svc in doc.get("services") or []:
        code = str(svc.get("name") or "").strip()
        title = str(svc.get("title") or "").strip()
        expected = names.get(code)
        if expected and title and title != expected:
            return True
    return False


def _rename_field(
    row,
    field: str,
    code: str | None,
    names: dict[str, str],
    stats: Counter[str],
    *,
    apply: bool,
) -> None:
    if not code:
        return
    code = code.strip()
    new_name = names.get(code)
    if not new_name:
        return
    old_name = getattr(row, field)
    if old_name == new_name:
        return
    stats[f"{field}.updated"] += 1
    if apply:
        setattr(row, field, new_name)


def sync(*, apply: bool) -> None:
    names = load_onec_department_names_map()
    stats: Counter[str] = Counter()

    records = build_department_records_for_db()
    stats["departments.records"] = len(records)

    if apply:
        seed_departments_to_db(records)

    factory = get_session_factory()
    with factory() as session:
        for row in session.query(DepartmentRow).yield_per(200):
            expected = resolve_department_display_name(row.code, row.name)
            if row.name != expected:
                stats["departments.rows"] += 1
                if apply:
                    row.name = expected

        for row in session.query(EmailMessageRow).filter(
            EmailMessageRow.department_id.isnot(None)
        ).yield_per(200):
            code = (row.department_id or "").strip()
            expected = resolve_department_display_name(code, row.department_name)
            if row.department_name != expected:
                stats["email_messages.department_name"] += 1
                if apply:
                    row.department_name = expected

        for row in session.query(ClassificationEventRow).yield_per(200):
            _rename_field(
                row,
                "old_department_name",
                row.old_department_id,
                names,
                stats,
                apply=apply,
            )
            _rename_field(
                row,
                "new_department_name",
                row.new_department_id,
                names,
                stats,
                apply=apply,
            )

        for row in session.query(EmailMessageRow).filter(
            EmailMessageRow.raw_payload_json.isnot(None)
        ).yield_per(200):
            try:
                payload = json.loads(row.raw_payload_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            xml = str(payload.get("xml_document") or "")
            if not xml or not _xml_titles_need_update(xml, names):
                continue
            stats["email_messages.xml_rebuild"] += 1
            if apply:
                email = _email_from_row(row)
                rebuilt = rebuild_xml_document_from_row(row, email)
                if rebuilt:
                    payload["xml_document"] = rebuilt
                    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

        if apply:
            session.commit()
            active = DepartmentRepository(session).count_active()
            print(f"Миграция применена (commit). Активных отделов в БД: {active}")
        else:
            print("Режим --dry-run (изменения не сохранены).")

    print("\nСтатистика:")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync department display names from 1C structure")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync(apply=args.apply)


if __name__ == "__main__":
    main()
