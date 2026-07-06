"""
Создание темы совещания в 1С:ERP — справочник Catalog_ТД_ТемыСовещаний (OData).

Обязательные поля при создании:
  - description (Description) — наименование темы
  - manager_fio → Руководитель_Key — руководитель совещания
  - meeting_type (ВидСовещания) — Отчетное | Внеплановое | Плановое | Селекторное

Рекомендуемые поля:
  - closed_date (ДатаЗакрытияТемы) — дата окончания действия темы; пустая = бессрочно
  - department_key (Подразделение_Key) — подразделение; можно взять из template_code/ref_key
  - reviewer_fio → Проверяющий_Key — по умолчанию совпадает с руководителем
  - room_key (Кабинет_Key) — переговорная
  - start_time / end_time — время совещания (формат 0001-01-01THH:MM:SS)

Опциональные поля:
  - project_key, committee_key, organization_key, basis_key
  - start_date, end_date (ДатаНачала, ДатаКонца)
  - is_project_topic, is_management_circle_topic
  - schedule_defined и поля повторения (repeat)
  - priority (Приоритет)

Code присваивает 1С автоматически. Ref_Key генерируется на стороне клиента.

CLI:
  python -m app.tools.onec.create_meeting_topic \\
    --description "Технический совет" \\
    --manager "Соломичева Светлана Викторовна" \\
    --meeting-type Отчетное \\
    --template-code 000009459 \\
    --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url
from app.tools.onec.lookup_user_ref import is_empty_key, resolve_user_by_fio
from app.tools.onec.meeting_topics_registry import (
    CATALOG_ENTITY,
    EMPTY_DATE,
    EMPTY_GUID,
    build_filter_parts,
    build_list_url,
    fetch_topic_by_key,
    normalize_topic,
    odata_get_json,
)

MEETING_TYPES = ("Отчетное", "Внеплановое", "Плановое", "Селекторное")
SKIP_TEMPLATE_FIELDS = frozenset(
    {
        "Ref_Key",
        "Code",
        "DataVersion",
        "DeletionMark",
        "Description",
        "Predefined",
        "PredefinedDataName",
    }
)


def default_closed_date(*, end_of_year: bool = True) -> str:
    if end_of_year:
        return f"{date.today().year}-12-31T00:00:00"
    return EMPTY_DATE


def meeting_time(hour: int, minute: int = 0) -> str:
    return f"0001-01-01T{hour:02d}:{minute:02d}:00"


def fetch_template_topic(
    session: requests.Session,
    config: ODataConfig,
    *,
    template_ref_key: str | None = None,
    template_code: str | None = None,
) -> dict[str, Any] | None:
    if template_ref_key:
        return fetch_topic_by_key(
            session,
            config,
            template_ref_key,
            expand_related=False,
        )

    if not template_code:
        return None

    filters = build_filter_parts(
        query=None,
        code=template_code.strip(),
        meeting_type=None,
        active_only=False,
        ref_key=None,
    )
    url = build_list_url(
        config,
        odata_filter=" and ".join(filters),
        limit=1,
        expand_related=False,
    )
    rows = odata_get_json(session, url, timeout=config.timeout).get("value") or []
    return rows[0] if rows else None


def copy_template_fields(source: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in source.items():
        if key in SKIP_TEMPLATE_FIELDS:
            continue
        if key.endswith("@navigationLinkUrl"):
            continue
        if isinstance(value, list):
            payload[key] = list(value)
            continue
        payload[key] = value
    return payload


def _apply_user_ref(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
    *,
    field_key: str,
    fio: str | None,
) -> str | None:
    if not fio:
        return None
    user_ref, _, _ = resolve_user_by_fio(session, fio, config=config)
    payload[field_key] = user_ref
    return user_ref


def build_meeting_topic_payload(
    *,
    description: str,
    manager_ref: str,
    meeting_type: str,
    reviewer_ref: str | None = None,
    closed_date: str | None = None,
    department_key: str | None = None,
    room_key: str | None = None,
    project_key: str | None = None,
    committee_key: str | None = None,
    organization_key: str | None = None,
    basis_key: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    is_project_topic: bool | None = None,
    is_management_circle_topic: bool | None = None,
    schedule_defined: bool | None = None,
    priority: str | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    topic_name = description.strip()
    if not topic_name:
        raise ValueError("Наименование темы (description) обязательно")

    normalized_type = meeting_type.strip()
    if normalized_type not in MEETING_TYPES:
        raise ValueError(
            f"Вид совещания должен быть одним из: {', '.join(MEETING_TYPES)}"
        )

    payload = copy_template_fields(template) if template else {}
    payload.update(
        {
            "Ref_Key": str(uuid.uuid4()),
            "DeletionMark": False,
            "Description": topic_name,
            "ВидСовещания": normalized_type,
            "Руководитель_Key": manager_ref,
            "Проверяющий_Key": reviewer_ref or manager_ref,
            "ДатаЗакрытияТемы": closed_date if closed_date is not None else payload.get(
                "ДатаЗакрытияТемы", EMPTY_DATE
            ),
            "ДатаНачала": start_date or payload.get("ДатаНачала") or EMPTY_DATE,
            "ДатаКонца": end_date or payload.get("ДатаКонца") or EMPTY_DATE,
            "ВремяНачалаСовещания": start_time
            or payload.get("ВремяНачалаСовещания")
            or EMPTY_DATE,
            "ВремяОкончанияСовещания": end_time
            or payload.get("ВремяОкончанияСовещания")
            or EMPTY_DATE,
            "ПоПроекту": (
                is_project_topic
                if is_project_topic is not None
                else bool(payload.get("ПоПроекту"))
            ),
            "ТемаКругаУправления": (
                is_management_circle_topic
                if is_management_circle_topic is not None
                else bool(payload.get("ТемаКругаУправления"))
            ),
            "РасписаниеЗадано": (
                schedule_defined
                if schedule_defined is not None
                else bool(payload.get("РасписаниеЗадано"))
            ),
            "Приоритет": priority if priority is not None else payload.get("Приоритет", ""),
            "ПериодПовтораДней": payload.get("ПериодПовтораДней", 0),
            "ПериодНедель": payload.get("ПериодНедель", 0),
            "ПериодМесяцев": payload.get("ПериодМесяцев", 0),
            "ПериодЛет": payload.get("ПериодЛет", 0),
            "КоличествоПовторов": payload.get("КоличествоПовторов", 0),
            "ДеньВМесяце": payload.get("ДеньВМесяце", 0),
            "ДеньНеделиВМесяце": payload.get("ДеньНеделиВМесяце", 0),
            "ПовторениеПоДнямНедели": payload.get("ПовторениеПоДнямНедели") or [],
            "ПовторениеПоМесяцам": payload.get("ПовторениеПоМесяцам") or [],
        }
    )

    for field_key, value in (
        ("Подразделение_Key", department_key),
        ("Кабинет_Key", room_key),
        ("Проект_Key", project_key),
        ("Комитет_Key", committee_key),
        ("Организация_Key", organization_key),
        ("Основание_Key", basis_key),
    ):
        if value:
            payload[field_key] = value
        elif field_key not in payload or is_empty_key(payload.get(field_key)):
            payload[field_key] = EMPTY_GUID

    return payload


def post_meeting_topic(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(
        f"{entity_url(config.url, CATALOG_ENTITY)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Ошибка создания темы совещания: HTTP {response.status_code}: {response.text[:1200]}"
        )
    return response.json()


def create_meeting_topic(
    *,
    description: str,
    manager_fio: str,
    meeting_type: str = "Отчетное",
    reviewer_fio: str | None = None,
    closed_date: str | None = None,
    closed_end_of_year: bool = False,
    department_key: str | None = None,
    room_key: str | None = None,
    project_key: str | None = None,
    committee_key: str | None = None,
    organization_key: str | None = None,
    basis_key: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    is_project_topic: bool | None = None,
    is_management_circle_topic: bool | None = None,
    schedule_defined: bool | None = None,
    priority: str | None = None,
    template_ref_key: str | None = None,
    template_code: str | None = None,
    dry_run: bool = False,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session = create_session(config)
    manager_ref, resolved_manager_fio, _ = resolve_user_by_fio(
        session,
        manager_fio,
        config=config,
    )

    reviewer_ref: str | None = None
    resolved_reviewer_fio: str | None = None
    if reviewer_fio:
        reviewer_ref, resolved_reviewer_fio, _ = resolve_user_by_fio(
            session,
            reviewer_fio,
            config=config,
        )

    template = fetch_template_topic(
        session,
        config,
        template_ref_key=template_ref_key,
        template_code=template_code,
    )
    if (template_ref_key or template_code) and template is None:
        raise ValueError("Тема-шаблон не найдена")

    resolved_closed_date = closed_date
    if resolved_closed_date is None and closed_end_of_year:
        resolved_closed_date = default_closed_date(end_of_year=True)

    payload = build_meeting_topic_payload(
        description=description,
        manager_ref=manager_ref,
        meeting_type=meeting_type,
        reviewer_ref=reviewer_ref,
        closed_date=resolved_closed_date,
        department_key=department_key,
        room_key=room_key,
        project_key=project_key,
        committee_key=committee_key,
        organization_key=organization_key,
        basis_key=basis_key,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        is_project_topic=is_project_topic,
        is_management_circle_topic=is_management_circle_topic,
        schedule_defined=schedule_defined,
        priority=priority,
        template=template,
    )

    result: dict[str, Any] = {
        "catalog_entity": CATALOG_ENTITY,
        "dry_run": dry_run,
        "manager_fio": resolved_manager_fio,
        "reviewer_fio": resolved_reviewer_fio or resolved_manager_fio,
        "template_ref_key": template.get("Ref_Key") if template else template_ref_key,
        "template_code": template.get("Code") if template else template_code,
        "payload": payload,
        "topic": None,
    }

    if dry_run:
        result["topic"] = normalize_topic(payload, expand_related=False)
        return result

    body = post_meeting_topic(session, config, payload)
    result["topic"] = normalize_topic(body, expand_related=False)
    result["body"] = body
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Создание темы совещания в 1С:ERP (Catalog_ТД_ТемыСовещаний)."
    )
    parser.add_argument("--description", required=True, help="Наименование темы")
    parser.add_argument("--manager", required=True, help="ФИО руководителя")
    parser.add_argument(
        "--meeting-type",
        default="Отчетное",
        choices=MEETING_TYPES,
        help="Вид совещания",
    )
    parser.add_argument("--reviewer", help="ФИО проверяющего (по умолчанию = руководитель)")
    parser.add_argument(
        "--closed-date",
        help=f"Дата окончания действия темы (ISO, например {date.today().year}-12-31T00:00:00)",
    )
    parser.add_argument(
        "--closed-end-of-year",
        action="store_true",
        help="Установить дату закрытия на 31.12 текущего года",
    )
    parser.add_argument("--department-key", help="GUID подразделения")
    parser.add_argument("--room-key", help="GUID переговорной (Кабинет)")
    parser.add_argument("--project-key", help="GUID проекта")
    parser.add_argument("--committee-key", help="GUID комитета")
    parser.add_argument("--organization-key", help="GUID организации")
    parser.add_argument("--start-time", help="Время начала, например 13:10")
    parser.add_argument("--end-time", help="Время окончания, например 14:00")
    parser.add_argument(
        "--management-circle",
        action="store_true",
        help="Тема круга управления (ТемаКругаУправления)",
    )
    parser.add_argument(
        "--template-ref-key",
        help="Ref_Key существующей темы для копирования реквизитов",
    )
    parser.add_argument(
        "--template-code",
        help="Код существующей темы для копирования реквизитов",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Сформировать payload без записи в 1С",
    )
    parser.add_argument("--output", help="Путь для сохранения JSON-ответа")
    return parser


def _parse_cli_time(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверный формат времени: {value!r}, ожидается HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    return meeting_time(hour, minute)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = create_meeting_topic(
            description=args.description,
            manager_fio=args.manager,
            meeting_type=args.meeting_type,
            reviewer_fio=args.reviewer,
            closed_date=args.closed_date,
            closed_end_of_year=args.closed_end_of_year,
            department_key=args.department_key,
            room_key=args.room_key,
            project_key=args.project_key,
            committee_key=args.committee_key,
            organization_key=args.organization_key,
            start_time=_parse_cli_time(args.start_time),
            end_time=_parse_cli_time(args.end_time),
            is_management_circle_topic=args.management_circle,
            template_ref_key=args.template_ref_key,
            template_code=args.template_code,
            dry_run=args.dry_run,
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
