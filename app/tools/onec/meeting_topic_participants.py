"""
Участники темы совещания из регистра сведений
InformationRegister_ТД_СоответствиеТемыСовещанияИУчастниковСовещаний.

В OData-публикации 1С объект называется «Соответствие темы совещания и участников
совещаний (ТД)».

Поля регистра:
  - Совещание_Key — Ref_Key темы (Catalog_ТД_ТемыСовещаний)
  - УчастникСовещания_Key — Ref_Key участника (Catalog_Пользователи)

Пример:
  python -m app.tools.onec.meeting_topic_participants --code 000009459
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import quote

import requests

from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url, odata_get_json
from app.tools.onec.get_porucheniya import load_users_for_keys
from app.tools.onec.lookup_user_ref import is_empty_key, load_persons_for_keys, user_fio
from app.tools.onec.meeting_topics_registry import (
    build_filter_parts,
    build_list_url,
    fetch_topic_by_key,
)

REGISTER_ENTITY = "InformationRegister_ТД_СоответствиеТемыСовещанияИУчастниковСовещаний"
TOPIC_KEY_FIELD = "Совещание_Key"
PARTICIPANT_KEY_FIELD = "УчастникСовещания_Key"


def resolve_topic_ref_key(
    session: requests.Session,
    config: ODataConfig,
    *,
    topic_ref_key: str | None,
    topic_code: str | None,
) -> tuple[str, dict[str, Any] | None]:
    if topic_ref_key and not is_empty_key(topic_ref_key):
        topic = fetch_topic_by_key(session, config, topic_ref_key, expand_related=False)
        return topic_ref_key, topic

    if not topic_code or not topic_code.strip():
        raise ValueError("Нужен topic_ref_key или topic_code")

    filters = build_filter_parts(
        query=None,
        code=topic_code.strip(),
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
    if not rows:
        raise ValueError(f"Тема совещания не найдена: code={topic_code!r}")
    topic = rows[0]
    ref_key = topic.get("Ref_Key")
    if is_empty_key(ref_key):
        raise ValueError(f"У темы {topic_code!r} не заполнен Ref_Key")
    return ref_key, topic


def fetch_participant_rows(
    session: requests.Session,
    config: ODataConfig,
    topic_ref_key: str,
) -> list[dict[str, Any]]:
    filter_expr = f"{TOPIC_KEY_FIELD} eq guid'{topic_ref_key}'"
    url = (
        f"{entity_url(config.url, REGISTER_ENTITY)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$format=json"
    )
    try:
        return fetch_all(session, url, page=200, timeout=config.timeout)
    except RuntimeError as error:
        message = str(error)
        if "HTTP 401" in message:
            raise RuntimeError(
                "Доступ к регистру участников темы совещания запрещён для пользователя OData. "
                "Проверьте, что в составе REST-сервиса включён объект "
                "«Соответствие темы совещания и участников совещаний (ТД)» и у учётной "
                "записи ONEC_ODATA_USER есть права чтения."
            ) from error
        raise


def normalize_participant_row(
    row: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    participant_key = row.get(PARTICIPANT_KEY_FIELD)
    user = users.get(participant_key or "", {})
    person_key = user.get("ФизическоеЛицо_Key")
    person = persons.get(person_key or "", {}) if person_key else {}
    fio = user_fio(user, {person_key: person} if person_key and person else persons)
    if not fio and person:
        fio = (person.get("Description") or person.get("ФИО") or "").strip()
    if not fio:
        fio = (user.get("Description") or "").strip()

    return {
        "participant_ref_key": participant_key,
        "fio": fio or None,
        "topic_ref_key": row.get(TOPIC_KEY_FIELD),
    }


def get_meeting_topic_participants(
    *,
    topic_ref_key: str | None = None,
    topic_code: str | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session = create_session(config)
    resolved_topic_ref, topic = resolve_topic_ref_key(
        session,
        config,
        topic_ref_key=topic_ref_key,
        topic_code=topic_code,
    )

    rows = fetch_participant_rows(session, config, resolved_topic_ref)
    participant_keys = {
        row.get(PARTICIPANT_KEY_FIELD)
        for row in rows
        if not is_empty_key(row.get(PARTICIPANT_KEY_FIELD))
    }
    users = load_users_for_keys(session, participant_keys, config=config)
    person_keys = {
        user.get("ФизическоеЛицо_Key")
        for user in users.values()
        if not is_empty_key(user.get("ФизическоеЛицо_Key"))
    }
    persons = load_persons_for_keys(session, person_keys, config=config)

    participants = [
        normalize_participant_row(row, users=users, persons=persons)
        for row in rows
    ]
    participants.sort(key=lambda item: (item.get("fio") or "").casefold())

    return {
        "register_entity": REGISTER_ENTITY,
        "topic_ref_key": resolved_topic_ref,
        "topic_code": (topic or {}).get("Code"),
        "topic_description": (topic or {}).get("Description"),
        "participants_count": len(participants),
        "participants": participants,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Участники темы совещания из "
            "InformationRegister_ТД_СоответствиеТемыСовещанияИУчастниковСовещаний"
        )
    )
    parser.add_argument("--ref-key", help="Ref_Key темы совещания")
    parser.add_argument("--code", help="Код темы совещания, например 000009459")
    parser.add_argument("--output", help="Путь для сохранения JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    if not args.ref_key and not args.code:
        print("Укажите --ref-key или --code", file=sys.stderr)
        return 2

    try:
        result = get_meeting_topic_participants(
            topic_ref_key=args.ref_key,
            topic_code=args.code,
        )
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
