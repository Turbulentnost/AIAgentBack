"""
Создание темы совещания в 1С:ERP — справочник Catalog_ТД_ТемыСовещаний (OData).

Перед созданием проверяет, есть ли у руководителя похожая активная тема
(дата закрытия строго позже сегодня, только темы этого руководителя).
Если есть — новая тема не создаётся, возвращается существующая.

Обязательные поля при создании:
  - description (Description) — наименование темы
  - manager_fio → Руководитель_Key — руководитель совещания
  - meeting_type (ВидСовещания) — Отчетное | Внеплановое | Плановое | Селекторное

CLI:
  python -m app.tools.onec.create_meeting_topic \\
    --description "Еженедельное совещание с главным метрологом" \\
    --manager "Мегрелишвили Михаил Эмзарович" \\
    --meeting-type Отчетное \\
    --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from typing import Any

import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url
from app.tools.onec.lookup_user_ref import resolve_user_by_fio
from app.tools.onec.meeting_topic_participants import (
    add_meeting_topic_participants,
    resolve_participant_refs_by_fio,
)
from app.tools.onec.meeting_topic_similarity import find_similar_topic_for_manager
from app.tools.onec.meeting_topics_registry import (
    CATALOG_ENTITY,
    EMPTY_DATE,
    EMPTY_GUID,
    normalize_topic,
)

MEETING_TYPES = ("Отчетное", "Внеплановое", "Плановое", "Селекторное")


def default_closed_date(*, end_of_year: bool = True) -> str:
    if end_of_year:
        return f"{date.today().year}-12-31T00:00:00"
    return EMPTY_DATE


def meeting_time(hour: int, minute: int = 0) -> str:
    return f"0001-01-01T{hour:02d}:{minute:02d}:00"


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
    topic_details: str | None = None,
) -> dict[str, Any]:
    topic_name = description.strip()
    if not topic_name:
        raise ValueError("Наименование темы (description) обязательно")

    normalized_type = meeting_type.strip()
    if normalized_type not in MEETING_TYPES:
        raise ValueError(
            f"Вид совещания должен быть одним из: {', '.join(MEETING_TYPES)}"
        )

    payload: dict[str, Any] = {
        "Ref_Key": str(uuid.uuid4()),
        "DeletionMark": False,
        "Description": topic_name,
        "ВидСовещания": normalized_type,
        "Руководитель_Key": manager_ref,
        "Проверяющий_Key": reviewer_ref or manager_ref,
        "ДатаЗакрытияТемы": closed_date or EMPTY_DATE,
        "ДатаНачала": start_date or EMPTY_DATE,
        "ДатаКонца": end_date or EMPTY_DATE,
        "ВремяНачалаСовещания": start_time or EMPTY_DATE,
        "ВремяОкончанияСовещания": end_time or EMPTY_DATE,
        "ПоПроекту": bool(is_project_topic),
        "ТемаКругаУправления": bool(is_management_circle_topic),
        "РасписаниеЗадано": bool(schedule_defined),
        "Приоритет": priority or "",
        "Описание": (topic_details or "").strip(),
        "ПериодПовтораДней": 0,
        "ПериодНедель": 0,
        "ПериодМесяцев": 0,
        "ПериодЛет": 0,
        "КоличествоПовторов": 0,
        "ДеньВМесяце": 0,
        "ДеньНеделиВМесяце": 0,
        "ПовторениеПоДнямНедели": [],
        "ПовторениеПоМесяцам": [],
    }

    for field_key, value in (
        ("Подразделение_Key", department_key),
        ("Кабинет_Key", room_key),
        ("Проект_Key", project_key),
        ("Комитет_Key", committee_key),
        ("Организация_Key", organization_key),
        ("Основание_Key", basis_key),
    ):
        payload[field_key] = value if value else EMPTY_GUID

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


def resolve_topic_participants(
    session: requests.Session,
    config: ODataConfig,
    *,
    manager_ref: str,
    participant_fios: list[str] | None,
) -> list[dict[str, str]]:
    resolved = (
        resolve_participant_refs_by_fio(session, config, participant_fios)
        if participant_fios
        else []
    )
    seen = {item["participant_ref_key"].strip().lower() for item in resolved}
    if manager_ref.strip().lower() not in seen:
        resolved.insert(0, {"participant_ref_key": manager_ref, "fio": None})
    return resolved


def build_skip_result(
    *,
    similar_topic: dict[str, Any],
    dry_run: bool,
    manager_fio: str,
    reviewer_fio: str,
) -> dict[str, Any]:
    code = similar_topic.get("code") or "?"
    description = similar_topic.get("description") or "?"
    return {
        "catalog_entity": CATALOG_ENTITY,
        "dry_run": dry_run,
        "created": False,
        "skipped": True,
        "skip_reason": "similar_topic_exists",
        "manager_fio": manager_fio,
        "reviewer_fio": reviewer_fio,
        "similar_topic": similar_topic,
        "similarity_score": similar_topic.get("similarity_score"),
        "similarity_method": similar_topic.get("similarity_method"),
        "similarity_breakdown": similar_topic.get("similarity_breakdown"),
        "topic": similar_topic,
        "payload": None,
        "participants_count": 0,
        "participants": [],
        "message": (
            f"У руководителя уже есть похожая тема №{code}: {description}. "
            "Новая тема не создана."
        ),
    }


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
    topic_details: str | None = None,
    participant_fios: list[str] | None = None,
    skip_similarity_check: bool = False,
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

    if not skip_similarity_check:
        similar_topic = find_similar_topic_for_manager(
            session,
            config,
            manager_ref_key=manager_ref,
            description=description,
            meeting_type=meeting_type,
            topic_details=topic_details,
            participant_fios=participant_fios,
        )
        if similar_topic:
            return build_skip_result(
                similar_topic=similar_topic,
                dry_run=dry_run,
                manager_fio=resolved_manager_fio,
                reviewer_fio=resolved_reviewer_fio or resolved_manager_fio,
            )

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
        topic_details=topic_details,
    )

    topic_ref_key = str(payload["Ref_Key"])
    participant_refs = resolve_topic_participants(
        session,
        config,
        manager_ref=manager_ref,
        participant_fios=participant_fios,
    )

    result: dict[str, Any] = {
        "catalog_entity": CATALOG_ENTITY,
        "dry_run": dry_run,
        "created": False,
        "skipped": False,
        "skip_reason": None,
        "manager_fio": resolved_manager_fio,
        "reviewer_fio": resolved_reviewer_fio or resolved_manager_fio,
        "similar_topic": None,
        "similarity_score": None,
        "similarity_method": None,
        "similarity_breakdown": None,
        "payload": payload,
        "topic": None,
        "participants_count": len(participant_refs),
        "participants": [],
        "message": None,
    }

    if dry_run:
        result["topic"] = normalize_topic(payload, expand_related=False)
        result["participants"] = add_meeting_topic_participants(
            session,
            config,
            topic_ref_key=topic_ref_key,
            participant_refs=participant_refs,
            dry_run=True,
        )
        result["message"] = "Проверка пройдена — тема может быть создана."
        return result

    body = post_meeting_topic(session, config, payload)
    result["created"] = True
    result["topic"] = normalize_topic(body, expand_related=False)
    result["body"] = body
    result["message"] = f"Создана тема совещания №{result['topic'].get('code') or '?'}."
    created_topic_ref = str(body.get("Ref_Key") or topic_ref_key)
    result["participants"] = add_meeting_topic_participants(
        session,
        config,
        topic_ref_key=created_topic_ref,
        participant_refs=participant_refs,
        dry_run=False,
    )
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
        "--details",
        help="Описание темы (поле Описание в 1С)",
    )
    parser.add_argument(
        "--participant",
        action="append",
        dest="participants",
        help="ФИО участника (можно указать несколько раз)",
    )
    parser.add_argument(
        "--skip-similarity-check",
        action="store_true",
        help="Не проверять похожие темы у руководителя перед созданием",
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
            topic_details=args.details,
            participant_fios=args.participants,
            skip_similarity_check=args.skip_similarity_check,
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
