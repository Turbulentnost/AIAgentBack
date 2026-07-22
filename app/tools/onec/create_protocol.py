"""
Создание и удаление протокола совещания в 1С:ERP (OData).

Сущности:
  - Document_ТД_Протокол
  - InformationRegister_ТД_ЗадачиПротоколов

CLI:
  python -m app.tools.onec.create_protocol create --number "НСР_001_О_001" --comment "Тест"
  python -m app.tools.onec.create_protocol create --topic-key ... --manager-fio "..." --meeting-type Отчетное --department-key ...
  python -m app.tools.onec.create_protocol delete --number "НСР_001_О_001"
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url
from app.tools.onec.lookup_user_ref import is_empty_key, resolve_user_by_fio

PROTOCOL_DOCUMENT = "Document_ТД_Протокол"
PROTOCOL_TASKS_REGISTER = "InformationRegister_ТД_ЗадачиПротоколов"
DEFAULT_MEETING_TYPE = "Отчетное"
DEFAULT_STATUS = "Подготовлен"
SKIP_HEADER_FIELDS = frozenset(
    {"Ref_Key", "Number", "Date", "Posted", "DeletionMark", "DataVersion"}
)


def fetch_protocol_by_ref(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
) -> dict[str, Any]:
    response = session.get(
        f"{entity_url(config.url, PROTOCOL_DOCUMENT)}(guid'{ref_key}')?$format=json",
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Протокол не найден: HTTP {response.status_code}: {response.text[:400]}"
        )
    return response.json()


def fetch_protocol_by_number(
    session: requests.Session,
    config: ODataConfig,
    number: str,
) -> dict[str, Any] | None:
    safe_number = number.replace("'", "''")
    url = (
        f"{entity_url(config.url, PROTOCOL_DOCUMENT)}"
        f"?$filter=Number eq '{safe_number}'&$top=1&$format=json"
    )
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        raise RuntimeError(
            f"Ошибка поиска протокола: HTTP {response.status_code}: {response.text[:400]}"
        )
    rows = response.json().get("value") or []
    return rows[0] if rows else None


def fetch_template_protocol(
    session: requests.Session,
    config: ODataConfig,
    *,
    template_ref_key: str | None = None,
    number_prefix: str | None = None,
) -> dict[str, Any]:
    if template_ref_key:
        return fetch_protocol_by_ref(session, config, template_ref_key)

    limit = 1 if not number_prefix else 200
    url = (
        f"{entity_url(config.url, PROTOCOL_DOCUMENT)}"
        f"?$filter=DeletionMark eq false&$orderby=Date desc&$top={limit}&$format=json"
    )
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        raise RuntimeError(
            f"Не удалось загрузить протоколы-шаблоны: HTTP {response.status_code}: {response.text[:400]}"
        )
    rows = response.json().get("value") or []
    if not rows:
        raise ValueError("В 1С не найден ни один протокол для шаблона")

    if number_prefix:
        for row in rows:
            if str(row.get("Number") or "").startswith(number_prefix):
                return row
    return rows[0]


def copy_header_fields(source: dict[str, Any], *, include_number: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    skip = set(SKIP_HEADER_FIELDS)
    if not include_number:
        skip.add("Number")
    for key, value in source.items():
        if key in skip:
            continue
        if key.endswith("_Key") and is_empty_key(value):
            continue
        if isinstance(value, list):
            continue
        payload[key] = value
        if key.endswith("_Key"):
            type_key = key.replace("_Key", "_Type")
            if source.get(type_key):
                payload[type_key] = source[type_key]
    return payload


def _apply_user_ref(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
    *,
    field_key: str,
    fio: str | None,
) -> None:
    if not fio:
        return
    user_ref, _, _ = resolve_user_by_fio(session, fio, config=config)
    payload[field_key] = user_ref
    payload[f"{field_key.replace('_Key', '')}_Type"] = "StandardODATA.Catalog_Пользователи"


def build_protocol_payload(
    template: dict[str, Any],
    *,
    number: str | None = None,
    comment: str = "",
    manager_fio: str | None = None,
    responsible_fio: str | None = None,
    prepared_by_fio: str | None = None,
    topic_key: str | None = None,
    meeting_type: str | None = None,
    department_key: str | None = None,
    session: requests.Session | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    payload = copy_header_fields(template)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    payload.update(
        {
            "Ref_Key": str(uuid.uuid4()),
            "Date": now,
            "DeletionMark": False,
            "Posted": False,
            "Комментарий": comment,
            "Статус": template.get("Статус") or DEFAULT_STATUS,
            "ВидСовещания": meeting_type or template.get("ВидСовещания") or DEFAULT_MEETING_TYPE,
        }
    )
    normalized_number = (number or "").strip()
    if normalized_number:
        payload["Number"] = normalized_number
    if topic_key:
        payload["ТемаСовещания_Key"] = topic_key
        payload["ТемаСовещания_Type"] = template.get("ТемаСовещания_Type") or (
            "StandardODATA.Catalog_ТД_ТемыСовещаний"
        )
    if department_key and not is_empty_key(department_key):
        payload["Подразделение_Key"] = department_key
        payload["Подразделение_Type"] = template.get("Подразделение_Type") or (
            "StandardODATA.Catalog_ПодразделенияОрганизаций"
        )

    if session is not None:
        _apply_user_ref(session, config, payload, field_key="Руководитель_Key", fio=manager_fio)
        _apply_user_ref(session, config, payload, field_key="Ответственный_Key", fio=responsible_fio)
        _apply_user_ref(session, config, payload, field_key="Подготовил_Key", fio=prepared_by_fio)
    return payload


def post_protocol_document(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(
        f"{entity_url(config.url, PROTOCOL_DOCUMENT)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Ошибка создания протокола: HTTP {response.status_code}: {response.text[:1200]}"
        )
    return response.json()


def post_protocol_task(
    session: requests.Session,
    config: ODataConfig,
    *,
    protocol_ref: str,
    protocol_body: dict[str, Any],
    item_number: int,
    task_text: str,
    due_date: str | None = None,
    responsible_fio: str | None = None,
) -> dict[str, Any]:
    due = due_date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    payload: dict[str, Any] = {
        "Протокол_Key": protocol_ref,
        "Протокол_Type": f"StandardODATA.{PROTOCOL_DOCUMENT}",
        "ИдентификаторЗадачи": str(uuid.uuid4()),
        "НомерПунктаПротокола": item_number,
        "Задача": task_text,
        "СрокИсполнения": due,
    }
    for key in ("ТемаСовещания_Key",):
        value = protocol_body.get(key)
        if value and not is_empty_key(value):
            payload[key] = value
            type_key = key.replace("_Key", "_Type")
            if protocol_body.get(type_key):
                payload[type_key] = protocol_body[type_key]

    if responsible_fio:
        user_ref, _, _ = resolve_user_by_fio(session, responsible_fio, config=config)
        payload["Ответственный_Key"] = user_ref
        payload["Ответственный_Type"] = protocol_body.get("Ответственный_Type") or (
            "StandardODATA.Catalog_Пользователи"
        )

    response = session.post(
        f"{entity_url(config.url, PROTOCOL_TASKS_REGISTER)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Ошибка создания пункта протокола: HTTP {response.status_code}: {response.text[:1200]}"
        )
    return response.json()


def ensure_protocol_number_in_body(
    session: requests.Session,
    config: ODataConfig,
    body: dict[str, Any],
) -> dict[str, Any]:
    """OData POST иногда не возвращает Number — перечитываем документ по Ref_Key."""
    if (body.get("Number") or "").strip():
        return body
    ref_key = (body.get("Ref_Key") or "").strip()
    if not ref_key:
        return body
    refreshed = fetch_protocol_by_ref(session, config, ref_key)
    if (refreshed.get("Number") or "").strip():
        body["Number"] = refreshed["Number"]
    return body


def create_meeting_protocol(
    *,
    number: str | None = None,
    comment: str = "",
    template_ref_key: str | None = None,
    template_number_prefix: str | None = None,
    manager_fio: str | None = None,
    responsible_fio: str | None = None,
    prepared_by_fio: str | None = None,
    topic_key: str | None = None,
    meeting_type: str | None = None,
    department_key: str | None = None,
    tasks: list[dict[str, Any]] | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    normalized_number = (number or "").strip() or None

    session = create_session(config)
    template = fetch_template_protocol(
        session,
        config,
        template_ref_key=template_ref_key,
        number_prefix=template_number_prefix or (
            _number_prefix(normalized_number) if normalized_number else None
        ),
    )
    payload = build_protocol_payload(
        template,
        number=normalized_number,
        comment=comment,
        manager_fio=manager_fio,
        responsible_fio=responsible_fio,
        prepared_by_fio=prepared_by_fio,
        topic_key=topic_key,
        meeting_type=meeting_type,
        department_key=department_key,
        session=session,
        config=config,
    )
    body = post_protocol_document(session, config, payload)
    body = ensure_protocol_number_in_body(session, config, body)

    created_tasks: list[dict[str, Any]] = []
    for index, task in enumerate(tasks or [], start=1):
        task_text = str(task.get("text") or task.get("task") or "").strip()
        if not task_text:
            raise ValueError(f"Пустой текст пункта протокола #{index}")
        task_body = post_protocol_task(
            session,
            config,
            protocol_ref=body["Ref_Key"],
            protocol_body=body,
            item_number=int(task.get("item_number") or index),
            task_text=task_text,
            due_date=task.get("due_date"),
            responsible_fio=task.get("responsible_fio"),
        )
        created_tasks.append(
            {
                "item_number": task_body.get("НомерПунктаПротокола"),
                "task_id": task_body.get("ИдентификаторЗадачи"),
                "text": task_body.get("Задача"),
                "due_date": task_body.get("СрокИсполнения"),
                "responsible_ref": task_body.get("Ответственный_Key"),
            }
        )

    return {
        "protocol": {
            "ref_key": body.get("Ref_Key"),
            "number": body.get("Number"),
            "date": body.get("Date"),
            "status": body.get("Статус"),
            "posted": body.get("Posted"),
            "comment": body.get("Комментарий"),
        },
        "template": {
            "ref_key": template.get("Ref_Key"),
            "number": template.get("Number"),
        },
        "tasks": created_tasks,
    }


def delete_meeting_protocol(
    *,
    ref_key: str | None = None,
    number: str | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    ref_key = (ref_key or "").strip() or None
    number = (number or "").strip() or None
    if not ref_key and not number:
        raise ValueError("Укажите ref_key или number протокола")

    session = create_session(config)
    resolved_number = number
    if not ref_key:
        row = fetch_protocol_by_number(session, config, number or "")
        if row is None:
            raise ValueError(f"Протокол с номером «{number}» не найден")
        ref_key = row["Ref_Key"]
        resolved_number = row.get("Number")

    response = session.delete(
        f"{entity_url(config.url, PROTOCOL_DOCUMENT)}(guid'{ref_key}')",
        timeout=config.timeout,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Ошибка удаления протокола: HTTP {response.status_code}: {response.text[:400]}"
        )
    return {
        "ref_key": ref_key,
        "number": resolved_number,
        "deleted": True,
    }


def _number_prefix(number: str) -> str | None:
    if "_" in number:
        return number.split("_", 1)[0]
    return number[:3] if len(number) >= 3 else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Создание и удаление протокола 1С через OData.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Создать протокол")
    create_parser.add_argument(
        "--number",
        help="Номер протокола, например НСР_001_О_001; если не указан — нумерация 1С",
    )
    create_parser.add_argument("--comment", default="", help="Комментарий документа")
    create_parser.add_argument("--template-ref-key", help="Ref_Key протокола-шаблона")
    create_parser.add_argument("--manager-fio", help="ФИО руководителя")
    create_parser.add_argument("--responsible-fio", help="ФИО ответственного")
    create_parser.add_argument("--prepared-by-fio", help="ФИО подготовившего")
    create_parser.add_argument("--topic-key", help="Ref_Key темы совещания")
    create_parser.add_argument("--meeting-type", help="Вид совещания, например Отчетное")
    create_parser.add_argument("--department-key", help="Ref_Key подразделения протокола")
    create_parser.add_argument("--task", action="append", default=[], help="Текст пункта протокола")
    create_parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")

    delete_parser = subparsers.add_parser("delete", help="Удалить протокол")
    delete_parser.add_argument("--ref-key", help="Ref_Key протокола")
    delete_parser.add_argument("--number", help="Номер протокола")
    delete_parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            tasks = [{"text": text} for text in args.task if str(text).strip()]
            result = create_meeting_protocol(
                number=args.number,
                comment=args.comment,
                template_ref_key=args.template_ref_key,
                manager_fio=args.manager_fio,
                responsible_fio=args.responsible_fio,
                prepared_by_fio=args.prepared_by_fio,
                topic_key=args.topic_key,
                meeting_type=args.meeting_type,
                department_key=args.department_key,
                tasks=tasks or None,
            )
        else:
            result = delete_meeting_protocol(ref_key=args.ref_key, number=args.number)
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
