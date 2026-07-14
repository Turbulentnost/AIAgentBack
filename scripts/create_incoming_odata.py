"""Создание документа Document_ТД_ВходящаяКорреспонденция через OData.

Режимы:
  --test          тестовый документ (без БД)
  --message-id    письмо из PostgreSQL (routing + summary + xml)
  --dry-run       только payload, без POST

Примеры:
  python scripts/create_incoming_odata.py --test --dry-run
  python scripts/create_incoming_odata.py --test
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
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.schemas import EmailMessage, Priority, RoutingResult  # noqa: E402
from agent_pochta.services import build_container  # noqa: E402
from agent_pochta.services.odata_integration import ODataIntegrationService  # noqa: E402


def _test_email() -> EmailMessage:
    return EmailMessage(
        message_id="<test-odata-create@agent-pochta.local>",
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
        "<направление>КС</направление>"
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


def _load_from_db(message_id: str) -> tuple[EmailMessage, RoutingResult, str, str | None]:
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
        xml_document = None
        if row.raw_payload_json:
            try:
                payload = json.loads(row.raw_payload_json)
                if isinstance(payload, dict):
                    xml_document = payload.get("xml_document")
            except json.JSONDecodeError:
                pass
        return email, routing, summary, xml_document


def main() -> None:
    parser = argparse.ArgumentParser(description="Create incoming correspondence in 1C via OData")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true", help="Тестовый документ")
    mode.add_argument("--message-id", metavar="ID", help="message_id из email_messages")
    parser.add_argument("--dry-run", action="store_true", help="Показать payload без POST")
    args = parser.parse_args()

    settings = get_settings()
    container = build_container(settings)
    if not isinstance(container.integration, ODataIntegrationService):
        raise SystemExit(
            "Интеграция не в режиме OData. Задайте ERP_MODE=odata, ODATA_BASE_URL и USE_STUBS=false."
        )

    if args.message_id:
        email, routing, summary, xml_document = _load_from_db(args.message_id)
    else:
        email, routing = _test_email(), _test_routing()
        summary = "Проверка записи через OData (scripts/create_incoming_odata.py)"
        xml_document = _test_xml()

    service: ODataIntegrationService = container.integration
    from agent_pochta.services.odata_incoming_mapper import build_incoming_document_payload

    extra = dict(service._extra_fields)
    extra.setdefault("Posted", False)

    body = build_incoming_document_payload(
        email,
        routing,
        summary,
        xml_document=xml_document,
        field_map=service._field_map,
        extra_fields=extra,
        organization_keys=service._organization_keys,
        department_keys=service._department_keys,
        department_names=service._department_names,
    )

    print(json.dumps(body, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n[dry-run] POST не выполнялся")
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
