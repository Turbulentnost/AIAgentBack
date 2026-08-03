"""Перенос устаревших кодов подразделений на актуальные коды 1С в PostgreSQL.

Соответствие (код ТЗ → активный код 1С):
  00-000034 → 00-000152  ОПЕРАЦИОННЫЙ ДИРЕКТОР
  00-000037 → 00-000163  ТЕХНИЧЕСКИЙ ДИРЕКТОР
  00-000109 → 00-000163  ТЕХНИЧЕСКИЙ ДИРЕКТОР
  00-000122 → 00-000163  ТЕХНИЧЕСКИЙ ДИРЕКТОР
  00-000139 → 00-000042  ОРКК
  00-000140 → 00-000042  ОРКК
  00-000141 → 00-000042  ОРКК
  00-000075 → 00-000155  Отдел дилерских продаж
  00-000105 → 00-000155  (тот же)
  00-000131 → 00-000128  Отдел продаж БМИ

Удаляются строки departments без замены: 00-000016, 00-000045, 00-000081

Обновляет email_messages, classification_events, change_events, erp_departments,
erp_contractors, XML в raw_payload_json; удаляет строки departments с устаревшими кодами.

Запуск:
  python scripts/migrate_department_codes.py --dry-run
  python scripts/migrate_department_codes.py --apply
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

from agent_pochta.db.models import (  # noqa: E402
    ChangeEventRow,
    ClassificationEventRow,
    DepartmentRow,
    EmailMessageRow,
    ErpContractorRow,
    ErpDepartmentRow,
)
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.xml_parser import rebuild_xml_document_from_row  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402

# Код ТЗ (ликвидирован в 1С) → актуальный код в Catalog_СтруктураПредприятия.
DEPARTMENT_CODE_ALIASES: dict[str, str] = {
    "00-000034": "00-000152",
    "00-000037": "00-000163",
    "00-000109": "00-000163",
    "00-000122": "00-000163",
    "00-000139": "00-000042",
    "00-000140": "00-000042",
    "00-000141": "00-000042",
    "00-000075": "00-000155",
    "00-000105": "00-000155",
    "00-000131": "00-000128",
}

DELETE_DEPARTMENT_CODES = frozenset({"00-000016", "00-000045", "00-000081"})

OLD_CODES = frozenset(DEPARTMENT_CODE_ALIASES)


def _replace_code(value: str | None) -> str | None:
    if not value:
        return value
    stripped = value.strip()
    return DEPARTMENT_CODE_ALIASES.get(stripped, stripped)


def _load_new_names(session) -> dict[str, str]:
    new_codes = set(DEPARTMENT_CODE_ALIASES.values())
    rows = session.query(DepartmentRow).filter(DepartmentRow.code.in_(new_codes)).all()
    names = {row.code: row.name for row in rows}
    missing = sorted(new_codes - set(names))
    if missing:
        raise SystemExit(f"В departments нет актуальных кодов: {', '.join(missing)}")
    return names


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


def _replace_codes_in_json_text(text: str) -> str:
    updated = text
    for old, new in DEPARTMENT_CODE_ALIASES.items():
        updated = updated.replace(old, new)
    return updated


def migrate(*, apply: bool) -> None:
    factory = get_session_factory()
    stats: Counter[str] = Counter()

    with factory() as session:
        new_names = _load_new_names(session)

        for row in session.query(EmailMessageRow).yield_per(200):
            old_id = (row.department_id or "").strip()
            if old_id not in OLD_CODES:
                continue
            new_id = DEPARTMENT_CODE_ALIASES[old_id]
            new_name = new_names[new_id]
            stats["email_messages.department_id"] += 1
            if apply:
                row.department_id = new_id
                row.department_name = new_name

            if row.raw_payload_json:
                payload_changed = False
                try:
                    payload = json.loads(row.raw_payload_json)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    xml = str(payload.get("xml_document") or "")
                    if xml and any(old in xml for old in OLD_CODES):
                        if apply:
                            row.department_id = new_id
                            row.department_name = new_name
                            email = _email_from_row(row)
                            rebuilt = rebuild_xml_document_from_row(row, email)
                            if rebuilt:
                                payload["xml_document"] = rebuilt
                                stats["email_messages.xml_rebuilt"] += 1
                            else:
                                payload["xml_document"] = _replace_codes_in_json_text(xml)
                                stats["email_messages.xml_string_replace"] += 1
                        else:
                            stats["email_messages.xml_needs_update"] += 1
                        payload_changed = True
                    serialized = json.dumps(payload, ensure_ascii=False)
                    if serialized != row.raw_payload_json:
                        if apply:
                            row.raw_payload_json = serialized
                        payload_changed = True
                elif any(old in row.raw_payload_json for old in OLD_CODES):
                    if apply:
                        row.raw_payload_json = _replace_codes_in_json_text(row.raw_payload_json)
                    payload_changed = True
                if payload_changed:
                    stats["email_messages.raw_payload_json"] += 1

        # Письма с устаревшим кодом только в XML/payload (department_id уже другой).
        for row in session.query(EmailMessageRow).filter(
            EmailMessageRow.raw_payload_json.isnot(None)
        ).yield_per(200):
            if not any(old in (row.raw_payload_json or "") for old in OLD_CODES):
                continue
            try:
                payload = json.loads(row.raw_payload_json)
            except json.JSONDecodeError:
                if apply:
                    row.raw_payload_json = _replace_codes_in_json_text(row.raw_payload_json)
                    stats["email_messages.payload_text_replace"] += 1
                else:
                    stats["email_messages.payload_text_replace"] += 1
                continue
            if not isinstance(payload, dict):
                continue
            xml = str(payload.get("xml_document") or "")
            if not xml or not any(old in xml for old in OLD_CODES):
                if apply:
                    serialized = _replace_codes_in_json_text(row.raw_payload_json)
                    if serialized != row.raw_payload_json:
                        row.raw_payload_json = serialized
                        stats["email_messages.payload_text_replace"] += 1
                else:
                    stats["email_messages.payload_text_replace"] += 1
                continue
            stats["email_messages.xml_orphan_needs_update"] += 1
            if apply:
                email = _email_from_row(row)
                rebuilt = rebuild_xml_document_from_row(row, email)
                if rebuilt and not any(old in rebuilt for old in OLD_CODES):
                    payload["xml_document"] = rebuilt
                    stats["email_messages.xml_orphan_rebuilt"] += 1
                else:
                    payload["xml_document"] = _replace_codes_in_json_text(xml)
                    stats["email_messages.xml_orphan_string_replace"] += 1
                row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

        for field_name in (
            "old_department_id",
            "new_department_id",
        ):
            column = getattr(ClassificationEventRow, field_name)
            for row in session.query(ClassificationEventRow).filter(column.in_(OLD_CODES)).yield_per(200):
                old_val = (getattr(row, field_name) or "").strip()
                new_val = DEPARTMENT_CODE_ALIASES[old_val]
                stats[f"classification_events.{field_name}"] += 1
                if apply:
                    setattr(row, field_name, new_val)
                    name_field = field_name.replace("_id", "_name")
                    setattr(row, name_field, new_names[new_val])

        for row in session.query(ChangeEventRow).yield_per(200):
            changed = False
            for attr in ("old_value", "new_value"):
                val = getattr(row, attr)
                if val and val.strip() in OLD_CODES:
                    stats[f"change_events.{attr}"] += 1
                    if apply:
                        setattr(row, attr, DEPARTMENT_CODE_ALIASES[val.strip()])
                    changed = True
            if changed:
                stats["change_events.rows"] += 1

        for old_code in OLD_CODES:
            new_code = DEPARTMENT_CODE_ALIASES[old_code]
            old_rows = (
                session.query(ErpDepartmentRow)
                .filter(ErpDepartmentRow.department_id == old_code)
                .all()
            )
            if not old_rows:
                continue
            new_exists = (
                session.query(ErpDepartmentRow)
                .filter(ErpDepartmentRow.department_id == new_code)
                .count()
                > 0
            )
            for erp_row in old_rows:
                stats["erp_departments.rows"] += 1
                if apply:
                    if new_exists:
                        session.delete(erp_row)
                        stats["erp_departments.deleted"] += 1
                    else:
                        erp_row.department_id = new_code
                        erp_row.department_name = new_names[new_code]
                        stats["erp_departments.renamed"] += 1

        for row in session.query(ErpContractorRow).yield_per(200):
            try:
                codes = json.loads(row.department_codes_json or "[]")
            except json.JSONDecodeError:
                continue
            if not isinstance(codes, list):
                continue
            updated_codes: list[str] = []
            changed = False
            for code in codes:
                code_str = str(code).strip()
                if code_str in OLD_CODES:
                    mapped = DEPARTMENT_CODE_ALIASES[code_str]
                    if mapped not in updated_codes:
                        updated_codes.append(mapped)
                    changed = True
                elif code_str not in updated_codes:
                    updated_codes.append(code_str)
            if changed:
                stats["erp_contractors.department_codes_json"] += 1
                if apply:
                    row.department_codes_json = json.dumps(updated_codes, ensure_ascii=False)

        for old_code in sorted(OLD_CODES):
            dept = session.query(DepartmentRow).filter(DepartmentRow.code == old_code).first()
            if dept is None:
                stats["departments.skip_missing"] += 1
                continue
            new_code = DEPARTMENT_CODE_ALIASES[old_code]
            if session.query(DepartmentRow).filter(DepartmentRow.code == new_code).count() == 0:
                raise SystemExit(
                    f"Нельзя удалить {old_code}: нет целевого отдела {new_code} в departments"
                )
            stats["departments.deleted"] += 1
            if apply:
                session.delete(dept)

        for delete_code in sorted(DELETE_DEPARTMENT_CODES):
            dept = session.query(DepartmentRow).filter(DepartmentRow.code == delete_code).first()
            if dept is None:
                stats["departments.skip_missing_delete"] += 1
                continue
            stats["departments.deleted_no_alias"] += 1
            if apply:
                session.delete(dept)

        if apply:
            session.commit()
            print("Миграция применена (commit).")
        else:
            print("Режим --dry-run (изменения не сохранены).")

    print("\nСтатистика:")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy department codes in PostgreSQL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Только подсчёт, без записи")
    group.add_argument("--apply", action="store_true", help="Применить изменения")
    args = parser.parse_args()
    migrate(apply=args.apply)


if __name__ == "__main__":
    main()
