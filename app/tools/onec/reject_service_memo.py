"""
Отклонение служебной записки в 1С:ERP (OData) и уведомление инициатора.

Переводит Статус из «НеСогласована» в «Отклонена», записывает причину в Комментарий
и отправляет уведомление на рабочий стол 1С инициатору (Ответственный).

CLI:
  python -m app.tools.onec.reject_service_memo --number 000009853 --reason "Не указана тема"
  python -m app.tools.onec.reject_service_memo --number 000009853 --reason "Тест" --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import DOCUMENT_ENTITY, fetch_document_header
from app.tools.onec.send_desktop_notification import send_desktop_notifications
from app.tools.onec.service_memo_shared import (
    REJECTED_STATUS,
    UNAPPROVED_STATUS,
    ServiceMemoWorkflowError,
    apply_executor_fields,
    ensure_meeting_memo,
    format_rejection_comment,
    load_memo_header,
    now_ud_timestamp,
    patch_service_memo,
    resolve_initiator_from_header,
)


def build_rejection_patch(
    session: requests.Session,
    config: ODataConfig,
    *,
    reason: str,
    rejector_fio: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Статус": REJECTED_STATUS,
        "ДатаИсполненияУД": now_ud_timestamp(),
        "Комментарий": format_rejection_comment(reason),
    }
    apply_executor_fields(session, config, payload, executor_fio=rejector_fio)
    return payload


def build_rejection_notification_message(
    *,
    number: str | None,
    reason: str,
) -> str:
    memo_number = (number or "").strip() or "?"
    return (
        f"Служебная записка №{memo_number} отклонена управлением делами. "
        f"Причина: {reason.strip()}"
    )


def build_rejection_result_message(
    *,
    number: str | None,
    changed: bool,
    already_rejected: bool,
    notification_sent: bool,
) -> str:
    memo_number = (number or "").strip() or "?"
    if already_rejected:
        return f"Служебная записка №{memo_number} уже отклонена."
    if changed and notification_sent:
        return f"Служебная записка №{memo_number} отклонена, инициатор уведомлён."
    if changed:
        return f"Служебная записка №{memo_number} отклонена."
    return f"Служебная записка №{memo_number} не изменена."


def reject_service_memo(
    *,
    ref_key: str | None = None,
    number: str | None = None,
    reason: str,
    rejector_fio: str | None = None,
    validate_meeting_theme: bool = True,
    notify_initiator: bool = True,
    dry_run: bool = False,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    reason_text = format_rejection_comment(reason)
    if not reason_text:
        raise ServiceMemoWorkflowError("Укажите причину отклонения (reason)")

    session, resolved_ref, before, metadata = load_memo_header(
        ref_key=ref_key,
        number=number,
        config=config,
    )

    if validate_meeting_theme:
        ensure_meeting_memo(session, config, before, metadata=metadata)

    previous_status = str(before.get("Статус") or "")
    initiator_ref, initiator_fio = resolve_initiator_from_header(session, config, before)
    notification_message = build_rejection_notification_message(
        number=str(before.get("Number") or number or ""),
        reason=reason_text,
    )

    base_result = {
        "ref_key": resolved_ref,
        "number": before.get("Number"),
        "date": before.get("Date"),
        "posted": before.get("Posted"),
        "status": before.get("Статус"),
        "previous_status": previous_status,
        "initiator_fio": initiator_fio,
        "initiator_ref": initiator_ref,
        "reason": reason_text,
        "comment": reason_text,
        "rejector_fio": rejector_fio,
        "notification_message": notification_message,
    }

    if previous_status == REJECTED_STATUS:
        return {
            **base_result,
            "already_rejected": True,
            "changed": False,
            "notification_sent": False,
            "dry_run": dry_run,
            "message": build_rejection_result_message(
                number=str(before.get("Number") or number or ""),
                changed=False,
                already_rejected=True,
                notification_sent=False,
            ),
        }

    if previous_status != UNAPPROVED_STATUS:
        raise ServiceMemoWorkflowError(
            f"Отклонение недоступно: текущий статус «{previous_status or 'не указан'}»"
        )

    if dry_run:
        return {
            **base_result,
            "already_rejected": False,
            "changed": False,
            "notification_sent": False,
            "dry_run": True,
            "would_notify_initiator": notify_initiator,
            "message": "Проверка пройдена — отклонение возможно.",
        }

    patch_payload = build_rejection_patch(
        session,
        config,
        reason=reason_text,
        rejector_fio=rejector_fio,
    )
    patch_service_memo(
        session,
        config,
        resolved_ref,
        patch_payload,
        action_label="отклонения",
    )
    after = fetch_document_header(session, config, resolved_ref)

    notification_result: dict[str, Any] | None = None
    notification_sent = False
    if notify_initiator:
        notification_result = send_desktop_notifications(
            message=notification_message,
            recipients_fio=[initiator_fio],
            source_user_fio=rejector_fio,
            source_ref=resolved_ref,
            source_type=DOCUMENT_ENTITY,
            config=config,
        )
        notification_sent = bool(notification_result.get("sent_count"))

    return {
        **base_result,
        "number": after.get("Number"),
        "status": after.get("Статус"),
        "already_rejected": False,
        "changed": True,
        "notification_sent": notification_sent,
        "notification": notification_result,
        "dry_run": False,
        "message": build_rejection_result_message(
            number=str(after.get("Number") or before.get("Number") or number or ""),
            changed=True,
            already_rejected=False,
            notification_sent=notification_sent,
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Отклонить служебную записку по совещанию в 1С:ERP и уведомить инициатора.",
    )
    parser.add_argument("--ref-key", help="Ref_Key документа")
    parser.add_argument("--number", help="Номер документа, например 000009853")
    parser.add_argument("--reason", required=True, help="Причина отклонения")
    parser.add_argument(
        "--rejector-fio",
        help="ФИО сотрудника УД (Catalog_Пользователи) для поля ИсполнительУД",
    )
    parser.add_argument(
        "--skip-theme-check",
        action="store_true",
        help="Не проверять тему «Организация совещаний»",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Не отправлять уведомление инициатору",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только проверить документ, без PATCH и уведомления",
    )
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = reject_service_memo(
            ref_key=args.ref_key,
            number=args.number,
            reason=args.reason,
            rejector_fio=args.rejector_fio,
            validate_meeting_theme=not args.skip_theme_check,
            notify_initiator=not args.no_notify,
            dry_run=args.dry_run,
        )
    except (ServiceMemoWorkflowError, RuntimeError, requests.RequestException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
