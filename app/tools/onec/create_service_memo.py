"""
Создание служебной записки в 1С:ERP и отправка задачи исполнителю.

Тема по умолчанию — «Иное». Получатель указывается по ФИО (Catalog_Пользователи).
Доставка — через Task_ЗадачаИсполнителя со ссылкой на документ.

Использование:
  python -m app.tools.onec.create_service_memo \\
    --recipient "Комарькова Анастасия Эдуардовна" \\
    --text "Прошу ознакомиться с информацией."
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
from app.tools.onec.lookup_user_ref import resolve_user_by_fio

MEMO_ENTITY = "Document_ТД_СлужебнаяЗаписка"
TASK_ENTITY = "Task_ЗадачаИсполнителя"
THEME_CATALOG = "Catalog_ТД_ТемыСлужебныхЗаписок"
DEFAULT_THEME = "Иное"
DEFAULT_GROUP_KEY = "6043915d-88b5-11e7-812e-001e67112509"
DEFAULT_TASK_DESCRIPTION = "Ознакомиться со служебной запиской"


def entity_url(base: str, entity: str) -> str:
    return f"{base.rstrip('/')}/{quote(entity)}"


def lookup_theme_key(
    session: requests.Session,
    config: ODataConfig,
    theme_description: str,
) -> tuple[str, str]:
    safe = theme_description.replace("'", "''")
    url = (
        f"{entity_url(config.url, THEME_CATALOG)}"
        f"?$filter=Description eq '{safe}'&$select=Ref_Key,Description&$top=1&$format=json"
    )
    rows = session.get(url, timeout=config.timeout).json().get("value") or []
    if not rows:
        raise ValueError(f"Тема служебной записки не найдена: «{theme_description}»")
    return rows[0]["Ref_Key"], theme_description


def load_memo_defaults(
    session: requests.Session,
    config: ODataConfig,
    theme_key: str,
) -> dict[str, Any]:
    url = (
        f"{entity_url(config.url, MEMO_ENTITY)}"
        f"?$filter=ТемаСлужебнойЗаписки_Key eq guid'{theme_key}' and DeletionMark eq false"
        f"&$orderby=Date desc&$top=1&$format=json"
    )
    rows = session.get(url, timeout=config.timeout).json().get("value") or []
    if rows:
        ref = rows[0]["Ref_Key"]
        header = session.get(
            f"{entity_url(config.url, MEMO_ENTITY)}(guid'{ref}')?$format=json",
            timeout=config.timeout,
        ).json()
        return {
            "source_number": header.get("Number"),
            "Организация_Key": header.get("Организация_Key"),
            "Подразделение_Key": header.get("Подразделение_Key"),
            "Ответственный_Key": header.get("Ответственный_Key"),
            "ГрифДоступа_Key": header.get("ГрифДоступа_Key"),
            "Направление": header.get("Направление") or "ПрочиеВнутренние",
        }
    return {
        "Организация_Key": "fbca2148-6cfd-11e7-812d-001e67112509",
        "Подразделение_Key": "ee1fb4f1-a9a7-11e3-ac46-001e67112509",
        "Ответственный_Key": "8e3693b3-37d9-11f1-97e2-6cb31113810e",
        "ГрифДоступа_Key": "bbdfce50-4266-11e8-8272-ac1f6b05524d",
        "Направление": "ПрочиеВнутренние",
    }


def create_service_memo(
    session: requests.Session,
    config: ODataConfig,
    *,
    text: str,
    theme_key: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    memo_ref = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "Ref_Key": memo_ref,
        "Date": now,
        "DeletionMark": False,
        "Posted": False,
        "ТемаСлужебнойЗаписки_Key": theme_key,
        "ТемаСлужебнойЗаписки_Type": f"StandardODATA.{THEME_CATALOG}",
        "ТекстСлужебнойЗаписки": text,
        "Направление": defaults.get("Направление") or "ПрочиеВнутренние",
        "Организация_Key": defaults["Организация_Key"],
        "Подразделение_Key": defaults["Подразделение_Key"],
        "Ответственный_Key": defaults["Ответственный_Key"],
        "ГрифДоступа_Key": defaults["ГрифДоступа_Key"],
    }
    response = session.post(
        f"{entity_url(config.url, MEMO_ENTITY)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Ошибка создания СЗ: HTTP {response.status_code}: {response.text[:800]}")
    body = response.json()
    return {"payload": payload, "body": body}


def create_executor_task(
    session: requests.Session,
    config: ODataConfig,
    *,
    memo_ref: str,
    memo_number: str,
    memo_date: str,
    recipient_ref: str,
    author_ref: str,
    description: str = DEFAULT_TASK_DESCRIPTION,
) -> dict[str, Any]:
    task_ref = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    subject = f"Служебная записка {memo_number} от {memo_date.replace('T', ' ')}"
    payload = {
        "Ref_Key": task_ref,
        "Date": now,
        "DeletionMark": False,
        "Executed": False,
        "Description": description,
        "Описание": description,
        "Предмет": memo_ref,
        "Предмет_Type": f"StandardODATA.{MEMO_ENTITY}",
        "ПредметСтрокой": subject,
        "Исполнитель": recipient_ref,
        "Исполнитель_Type": "StandardODATA.Catalog_Пользователи",
        "Автор": author_ref,
        "Автор_Type": "StandardODATA.Catalog_Пользователи",
        "BusinessProcess": "",
        "BusinessProcess_Type": "StandardODATA.Undefined",
        "RoutePoint": "",
        "RoutePoint_Type": "StandardODATA.Undefined",
        "Важность": "Обычная",
        "ГруппаИсполнителейЗадач_Key": DEFAULT_GROUP_KEY,
        "ДатаНачала": now,
        "ПринятаКИсполнению": False,
        "СостояниеБизнесПроцесса": "Активен",
    }
    response = session.post(
        f"{entity_url(config.url, TASK_ENTITY)}?$format=json",
        json=payload,
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Ошибка создания задачи: HTTP {response.status_code}: {response.text[:800]}")
    return {"payload": payload, "body": response.json()}


def create_and_send_service_memo(
    *,
    recipient_fio: str,
    text: str,
    theme: str = DEFAULT_THEME,
    task_description: str = DEFAULT_TASK_DESCRIPTION,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session = create_session(config)
    recipient_ref, resolved_fio, _ = resolve_user_by_fio(session, recipient_fio, config=config)
    theme_key, theme_description = lookup_theme_key(session, config, theme)
    defaults = load_memo_defaults(session, config, theme_key)
    author_ref = defaults.get("Ответственный_Key") or recipient_ref

    memo = create_service_memo(
        session,
        config,
        text=text,
        theme_key=theme_key,
        defaults=defaults,
    )
    memo_body = memo["body"]
    task = create_executor_task(
        session,
        config,
        memo_ref=memo_body["Ref_Key"],
        memo_number=memo_body.get("Number", "?"),
        memo_date=memo_body.get("Date", memo["payload"]["Date"]),
        recipient_ref=recipient_ref,
        author_ref=author_ref,
        description=task_description,
    )
    task_body = task["body"]
    return {
        "theme": theme_description,
        "recipient": {"fio": resolved_fio, "user_ref": recipient_ref},
        "memo": {
            "ref_key": memo_body.get("Ref_Key"),
            "number": memo_body.get("Number"),
            "date": memo_body.get("Date"),
            "posted": memo_body.get("Posted"),
            "status": memo_body.get("Статус"),
        },
        "task": {
            "ref_key": task_body.get("Ref_Key"),
            "number": task_body.get("Number"),
            "description": task_body.get("Description"),
            "executor_ref": task_body.get("Исполнитель"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Создать служебную записку в 1С и отправить задачу исполнителю по ФИО.",
    )
    parser.add_argument(
        "--recipient",
        required=True,
        help="ФИО получателя (Catalog_Пользователи)",
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Текст служебной записки",
    )
    parser.add_argument(
        "--theme",
        default=DEFAULT_THEME,
        help=f"Тема из Catalog_ТД_ТемыСлужебныхЗаписок (по умолчанию «{DEFAULT_THEME}»)",
    )
    parser.add_argument(
        "--task-description",
        default=DEFAULT_TASK_DESCRIPTION,
        help="Текст задачи для исполнителя",
    )
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = create_and_send_service_memo(
            recipient_fio=args.recipient,
            text=args.text,
            theme=args.theme,
            task_description=args.task_description,
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
