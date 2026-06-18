"""
Уведомления на рабочий стол 1С через регистр InformationRegister_НапоминанияПользователя.

Повторяет логику ТД_ОбщийМодуль.ОтправитьУведомлениеНаРабочийСтол:
  - запись в РегистрыСведений.НапоминанияПользователя для каждого получателя;
  - получатель — ФИО из Catalog_Пользователи или GUID;
  - источник — объект 1С (GUID + тип); без объекта OData пишет пустой источник (Undefined).

CLI:
  python -m app.tools.onec.send_desktop_notification \\
    --message "Требуется согласование СЗ" \\
    --recipient "Комарькова Анастасия Эдуардовна"
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from app.core.config import settings
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.lookup_user_ref import USER_CATALOG, resolve_user_by_fio

REGISTER_ENTITY = "InformationRegister_НапоминанияПользователя"
REMINDER_METHOD_AT_TIME = "ВУказанноеВремя"
REMINDER_METHOD_RELATIVE = "ОтносительноТекущегоВремени"
DEFAULT_INTERVAL = 60
DEFAULT_INTERVAL_WHEN_METHOD_SET = 250
EVENT_OFFSET_SECONDS = 5
PERIOD_EVENT_OFFSET_SECONDS = 600
UNDEFINED_TYPE = "StandardODATA.Undefined"


def entity_url(base: str, entity: str) -> str:
    return f"{base.rstrip('/')}/{quote(entity)}"


def _format_onec_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_period_close_date(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    if "T" not in normalized and len(normalized) == 10:
        normalized = f"{normalized}T00:00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def default_recipient_fios() -> list[str]:
    raw = (settings.ONEC_NOTIFICATION_DEFAULT_RECIPIENT_FIOS or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalize_recipients(recipients_fio: list[str] | None) -> list[str]:
    if recipients_fio:
        normalized = [item.strip() for item in recipients_fio if item and item.strip()]
        if normalized:
            return normalized
    defaults = default_recipient_fios()
    if defaults:
        return defaults
    raise ValueError(
        "Не указаны получатели уведомления. Передайте recipients_fio "
        "или задайте ONEC_NOTIFICATION_DEFAULT_RECIPIENT_FIOS в .env."
    )


def resolve_source_user_ref(
    session: requests.Session,
    *,
    source_user_fio: str | None,
    fallback_fio: str,
    config: ODataConfig,
) -> tuple[str, str]:
    fio = (source_user_fio or settings.ONEC_NOTIFICATION_SOURCE_USER_FIO or fallback_fio).strip()
    user_ref, resolved_fio, _ = resolve_user_by_fio(session, fio, config=config)
    return user_ref, resolved_fio


def build_reminder_times(
    *,
    period_close_date: str | None,
    now: datetime | None = None,
) -> tuple[str, str]:
    current = now or datetime.now().replace(microsecond=0)
    if period_close_date:
        deadline = _parse_period_close_date(period_close_date)
        event_time = deadline - timedelta(seconds=PERIOD_EVENT_OFFSET_SECONDS)
        return _format_onec_datetime(event_time), _format_onec_datetime(deadline)
    event_time = current + timedelta(seconds=EVENT_OFFSET_SECONDS)
    formatted = _format_onec_datetime(event_time)
    return formatted, formatted


def build_reminder_method_fields(
    *,
    reminder_time_setting_method: str | None,
    reminder_time_interval: int | None,
) -> tuple[str, int]:
    if reminder_time_setting_method:
        method = reminder_time_setting_method.strip()
        interval = (
            reminder_time_interval
            if reminder_time_interval is not None
            else DEFAULT_INTERVAL_WHEN_METHOD_SET
        )
        return method, interval
    if reminder_time_interval is not None:
        return REMINDER_METHOD_AT_TIME, reminder_time_interval
    return REMINDER_METHOD_RELATIVE, DEFAULT_INTERVAL


def build_source_fields(
    *,
    message: str,
    source_ref: str | None,
    source_type: str | None,
    source_user_ref: str,
    source_user_fio: str,
) -> tuple[str, str, str]:
    if source_ref and source_type:
        source = source_ref
        source_type_value = (
            source_type
            if source_type.startswith("StandardODATA.")
            else f"StandardODATA.{source_type}"
        )
        presentation = message.strip() or source_user_fio
        return source, source_type_value, presentation
    # OData принимает пустой источник; GUID нулей иногда отклоняется обработчиком 1С.
    return "", UNDEFINED_TYPE, message.strip() or source_user_fio


def build_reminder_payload(
    *,
    message: str,
    user_ref: str,
    source_user_ref: str,
    source_user_fio: str,
    period_close_date: str | None = None,
    source_ref: str | None = None,
    source_type: str | None = None,
    reminder_time_setting_method: str | None = None,
    reminder_time_interval: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    text = message.strip()
    if not text:
        raise ValueError("Текст уведомления не может быть пустым")

    event_time, reminder_deadline = build_reminder_times(
        period_close_date=period_close_date,
        now=now,
    )
    method, interval = build_reminder_method_fields(
        reminder_time_setting_method=reminder_time_setting_method,
        reminder_time_interval=reminder_time_interval,
    )
    source, source_type_value, source_presentation = build_source_fields(
        message=text,
        source_ref=source_ref,
        source_type=source_type,
        source_user_ref=source_user_ref,
        source_user_fio=source_user_fio,
    )

    return {
        "Пользователь_Key": user_ref,
        "ВремяСобытия": event_time,
        "Источник": source,
        "Источник_Type": source_type_value,
        "СрокНапоминания": reminder_deadline,
        "Описание": text,
        "ПредставлениеИсточника": source_presentation,
        "СпособУстановкиВремениНапоминания": method,
        "ИнтервалВремениНапоминания": interval,
        "Идентификатор": str(uuid.uuid4()),
    }


def create_user_reminder(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(
        f"{entity_url(config.url, REGISTER_ENTITY)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            "Ошибка создания уведомления: "
            f"HTTP {response.status_code}: {response.text[:800]}"
        )
    if response.text.strip():
        return response.json()
    return payload


def send_desktop_notifications(
    *,
    message: str,
    recipients_fio: list[str] | None = None,
    source_user_fio: str | None = None,
    source_ref: str | None = None,
    source_type: str | None = None,
    period_close_date: str | None = None,
    reminder_time_setting_method: str | None = None,
    reminder_time_interval: int | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session = create_session(config)
    recipients = normalize_recipients(recipients_fio)
    source_user_ref, resolved_source_fio = resolve_source_user_ref(
        session,
        source_user_fio=source_user_fio,
        fallback_fio=recipients[0],
        config=config,
    )

    created: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    base_now = datetime.now().replace(microsecond=0)

    for index, recipient_fio in enumerate(recipients):
        try:
            user_ref, resolved_fio, _ = resolve_user_by_fio(session, recipient_fio, config=config)
            payload = build_reminder_payload(
                message=message,
                user_ref=user_ref,
                source_user_ref=source_user_ref,
                source_user_fio=resolved_source_fio,
                period_close_date=period_close_date,
                source_ref=source_ref,
                source_type=source_type,
                reminder_time_setting_method=reminder_time_setting_method,
                reminder_time_interval=reminder_time_interval,
                now=base_now + timedelta(seconds=index),
            )
            body = create_user_reminder(session, config, payload)
            created.append(
                {
                    "recipient_fio": resolved_fio,
                    "user_ref": user_ref,
                    "event_time": payload["ВремяСобытия"],
                    "reminder_deadline": payload["СрокНапоминания"],
                    "reminder_id": payload["Идентификатор"],
                    "response": body,
                }
            )
        except (RuntimeError, ValueError) as exc:
            errors.append({"recipient_fio": recipient_fio, "error": str(exc)})

    if not created and errors:
        raise RuntimeError(errors[0]["error"])

    return {
        "register_entity": REGISTER_ENTITY,
        "message": message.strip(),
        "source_user_fio": resolved_source_fio,
        "source_user_ref": source_user_ref,
        "source_ref": source_ref,
        "source_type": source_type,
        "sent_count": len(created),
        "failed_count": len(errors),
        "notifications": created,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Отправить уведомление на рабочий стол 1С через OData.",
    )
    parser.add_argument("--message", required=True, help="Текст уведомления")
    parser.add_argument(
        "--recipient",
        action="append",
        dest="recipients",
        help="ФИО получателя (можно указать несколько раз)",
    )
    parser.add_argument("--source-user-fio", help="ФИО отправителя для поля Источник")
    parser.add_argument("--source-ref", help="GUID объекта-источника")
    parser.add_argument("--source-type", help="Тип объекта-источника, например Document_ТД_СлужебнаяЗаписка")
    parser.add_argument("--period-close-date", help="Дата закрытия периода (ISO)")
    parser.add_argument(
        "--reminder-time-setting-method",
        help=f"Способ установки времени ({REMINDER_METHOD_AT_TIME} / {REMINDER_METHOD_RELATIVE})",
    )
    parser.add_argument("--reminder-time-interval", type=int, help="Интервал напоминания в секундах")
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = send_desktop_notifications(
            message=args.message,
            recipients_fio=args.recipients,
            source_user_fio=args.source_user_fio,
            source_ref=args.source_ref,
            source_type=args.source_type,
            period_close_date=args.period_close_date,
            reminder_time_setting_method=args.reminder_time_setting_method,
            reminder_time_interval=args.reminder_time_interval,
        )
    except (requests.RequestException, RuntimeError, ValueError) as error:
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
