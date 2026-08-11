"""Создание документа Document_ТД_ВходящаяКорреспонденция через OData.

Режимы:
  --test          тестовый документ (без БД)
  --from-info-db  последнее письмо info@ с XML из PostgreSQL
  --message-id    письмо из PostgreSQL (routing + summary + xml)
  --dry-run       только payload (по умолчанию)
  --post          выполнить POST в 1С (осторожно!)

Примеры:
  python scripts/create_incoming_odata.py --test --dry-run
  python scripts/create_incoming_odata.py --test
  python scripts/create_incoming_odata.py --from-info-db --dry-run
  python scripts/create_incoming_odata.py --message-id "<id@mail>"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.message_filters import (
    INFO_MAILBOX,
    email_eligible_for_erp,
)
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.xml_parser import parse_document_xml  # noqa: E402
from agent_pochta.schemas import EmailMessage, Priority, RoutingResult  # noqa: E402
from agent_pochta.services import build_container  # noqa: E402
from agent_pochta.services.odata_integration import ODataIntegrationService  # noqa: E402


def _test_email() -> EmailMessage:
    return EmailMessage(
        message_id="<test-odata-create-" + datetime.now().strftime("%Y%m%d%H%M%S") + "@agent-pochta.local>",
        mailbox="info@turbo-don.ru",
        sender_email="sender@example.com",
        subject="Тест OData: входящая корреспонденция",
        received_at=datetime.now(timezone.utc),
    )


def _test_routing() -> RoutingResult:
    return RoutingResult(
        department_id="00-000066",
        department_name="Управление делами (офис-менеджер)",
        confidence=1.0,
        reasoning="manual test",
        priority=Priority.NORMAL,
    )


def _test_xml() -> str:
    return (
        "<document>"
        "<organization>НП</organization>"
        "<theme>Тест OData agent-pochta</theme>"
        "<направление>ПР</направление>"
        "<claim>false</claim>"
        "<partner>ООО Тест</partner>"
        "<services>"
        "<service><name>00-000066</name><process>исполнение</process>"
        "<reasoning>Тест</reasoning></service>"
        "</services>"
        "<email_sender>sender@example.com</email_sender>"
        "<email_recipient>info@turbo-don.ru</email_recipient>"
        f"<mail_datetime>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</mail_datetime>"
        "<process>исполнение</process>"
        "<spam>false</spam>"
        "</document>"
    )


def _extract_xml_from_row(row) -> str | None:
    if not row.raw_payload_json:
        return None
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    xml_document = payload.get("xml_document")
    if isinstance(xml_document, str) and xml_document.strip():
        return xml_document.strip()
    return None


def _load_from_db(message_id: str) -> tuple[EmailMessage, RoutingResult, str, str | None, EmailMessageRow]:
    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_message_id(message_id)
        if row is None:
            raise SystemExit(f"Письмо не найдено: {message_id}")
        email = repo.load_email_from_row(row)
        routing = repo.build_routing_from_row(row)
        if email is None or routing is None:
            raise SystemExit("Неполная запись: нет payload или routing")
        summary = row.summary_ru or ""
        return email, routing, summary, _extract_xml_from_row(row), row


def _load_latest_info_from_db() -> tuple[EmailMessage, RoutingResult, str, str | None, str, EmailMessageRow]:
    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        rows = (
            session.query(EmailMessageRow)
            .order_by(EmailMessageRow.received_at.desc())
            .limit(2000)
            .all()
        )
        for row in rows:
            payload: dict | None = None
            if row.raw_payload_json:
                try:
                    raw = json.loads(row.raw_payload_json)
                    if isinstance(raw, dict):
                        payload = raw
                except json.JSONDecodeError:
                    payload = None
            if not email_eligible_for_erp(
                mailbox=row.mailbox or "",
                payload=payload,
                status=row.status or "",
            ):
                continue
            xml_document = _extract_xml_from_row(row)
            if not xml_document:
                continue
            email = repo.load_email_from_row(row)
            routing = repo.build_routing_from_row(row)
            if email is None or routing is None:
                continue
            return email, routing, row.summary_ru or "", xml_document, row.message_id, row
    raise SystemExit("Не найдено письмо info@ с xml_document в PostgreSQL")


def _print_xml_summary(xml_document: str | None) -> None:
    parsed = parse_document_xml(xml_document) if xml_document else None
    if not parsed:
        print("[xml] не удалось разобрать document")
        return
    print("[xml]")
    print(f"  organization: {parsed.get('organization')}")
    print(f"  направление:  {parsed.get('direction')}")
    print(f"  partner:      {parsed.get('partner')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create incoming correspondence in 1C via OData")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true", help="Тестовый документ")
    mode.add_argument("--from-info-db", action="store_true", help="Последнее info@ письмо с XML из БД")
    mode.add_argument("--message-id", metavar="ID", help="message_id из email_messages")
    parser.add_argument(
        "--post",
        action="store_true",
        help="Выполнить POST в 1С (без этого флага — только dry-run)",
    )
    args = parser.parse_args()

    settings = get_settings()
    container = build_container(settings)
    if not isinstance(container.integration, ODataIntegrationService):
        raise SystemExit(
            "Интеграция не в режиме OData. Задайте ERP_MODE=odata, ODATA_BASE_URL и USE_STUBS=false."
        )

    source_message_id = ""
    db_row: EmailMessageRow | None = None
    if args.from_info_db:
        email, routing, summary, xml_document, source_message_id, db_row = _load_latest_info_from_db()
        print(f"Источник: info@ из БД, message_id={source_message_id}")
    elif args.message_id:
        email, routing, summary, xml_document, db_row = _load_from_db(args.message_id)
        source_message_id = args.message_id
        print(f"Источник: message_id={source_message_id}")
    else:
        email, routing = _test_email(), _test_routing()
        summary = "Проверка записи через OData (scripts/create_incoming_odata.py)"
        xml_document = _test_xml()
        print("Источник: тестовый документ")

    _print_xml_summary(xml_document)

    service: ODataIntegrationService = container.integration
    from agent_pochta.services.odata_incoming_mapper import build_incoming_document_payload

    body = build_incoming_document_payload(
        email,
        routing,
        summary,
        xml_document=xml_document,
        field_map=service._field_map,
        extra_fields=service._extra_fields or None,
        organization_keys=service._organization_keys,
        department_keys=service._department_keys,
        department_names=service._department_names,
    )

    print(json.dumps(body, ensure_ascii=False, indent=2))

    existing_doc = (db_row.erp_document_number or "").strip() if db_row else ""
    if existing_doc:
        print(f"\n[skip-post] Документ 1С уже создан: {existing_doc} — POST пропущен")
        if args.post:
            print("           (удалите --post или выберите письмо без erp_document_number)")
        return

    if not args.post:
        print("\n[dry-run] POST не выполнялся (для записи в 1С добавьте --post)")
        return

    result = service.create_incoming_correspondence(
        email,
        routing,
        summary,
        xml_document=xml_document,
    )
    print("\nOK:")
    print(json.dumps(
        {
            "erp_document_number": result.get("erp_document_number"),
            "erp_document_id": result.get("erp_document_id"),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
