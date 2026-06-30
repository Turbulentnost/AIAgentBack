"""
Согласование служебной записки в 1С:ERP (OData).

Для СЗ по совещаниям переводит Статус из «НеСогласована» в «Согласована»
через PATCH Document_ТД_СлужебнаяЗаписка.

CLI:
  python -m app.tools.onec.approve_service_memo --number 000010430
  python -m app.tools.onec.approve_service_memo --ref-key 8f87f484-7398-11f1-9831-6cb31113810c
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any
import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    entity_url,
    fetch_document_header,
    fetch_meeting_memo_rows,
    load_metadata_xml,
    theme_matches,
)
from app.tools.onec.lookup_user_ref import resolve_user_by_fio

APPROVED_STATUS = "Согласована"
UNAPPROVED_STATUS = "НеСогласована"


class ServiceMemoApprovalError(ValueError):
    """Ошибка бизнес-валидации при согласовании СЗ."""


def resolve_memo_ref_key(
    session: requests.Session,
    config: ODataConfig,
    *,
    ref_key: str | None,
    number: str | None,
) -> str:
    ref = (ref_key or "").strip()
    if ref:
        return ref

    memo_number = (number or "").strip()
    if not memo_number:
        raise ServiceMemoApprovalError("Укажите ref_key или number служебной записки")

    safe_number = memo_number.replace("'", "''")
    rows = fetch_meeting_memo_rows(
        session,
        config,
        f"Number eq '{safe_number}'",
        limit=1,
        fetch_pool=20,
    )
    if not rows:
        raise ServiceMemoApprovalError(f"Служебная записка не найдена: {memo_number}")
    return str(rows[0]["Ref_Key"])


def ensure_meeting_memo(
    session: requests.Session,
    config: ODataConfig,
    header: dict[str, Any],
    *,
    metadata: Any | None = None,
) -> None:
    if metadata is None:
        metadata = load_metadata_xml(session, config)
    from app.tools.onec.get_meetings import resolve_theme_key

    theme_key = resolve_theme_key(session, config, metadata)
    if theme_matches(header, theme_key):
        return
    raise ServiceMemoApprovalError(
        "Документ не относится к теме служебных записок по совещаниям"
    )


def build_approval_patch(
    session: requests.Session,
    config: ODataConfig,
    *,
    approver_fio: str | None,
    comment: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"Статус": APPROVED_STATUS}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    payload["ДатаИсполненияУД"] = now

    if approver_fio:
        user_ref, _, _ = resolve_user_by_fio(session, approver_fio, config=config)
        payload["ИсполнительУД_Key"] = user_ref
        payload["ИсполнительУД_Type"] = "StandardODATA.Catalog_Пользователи"

    if comment is not None and comment.strip():
        payload["Комментарий"] = comment.strip()

    return payload


def patch_service_memo_status(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{entity_url(config.url, DOCUMENT_ENTITY)}(guid'{ref_key}')?$format=json"
    response = session.patch(url, json=payload, timeout=config.timeout)
    if not response.ok:
        raise RuntimeError(
            f"Ошибка согласования СЗ: HTTP {response.status_code}: {response.text[:800]}"
        )
    return response.json()


def approve_service_memo(
    *,
    ref_key: str | None = None,
    number: str | None = None,
    approver_fio: str | None = None,
    comment: str | None = None,
    require_unapproved: bool = True,
    validate_meeting_theme: bool = True,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session = create_session(config)
    metadata = load_metadata_xml(session, config)
    resolved_ref = resolve_memo_ref_key(session, config, ref_key=ref_key, number=number)
    before = fetch_document_header(session, config, resolved_ref)

    if validate_meeting_theme:
        ensure_meeting_memo(session, config, before, metadata=metadata)

    previous_status = str(before.get("Статус") or "")
    if previous_status == APPROVED_STATUS:
        return {
            "ref_key": resolved_ref,
            "number": before.get("Number"),
            "date": before.get("Date"),
            "posted": before.get("Posted"),
            "status": previous_status,
            "previous_status": previous_status,
            "already_approved": True,
            "changed": False,
        }

    if require_unapproved and previous_status != UNAPPROVED_STATUS:
        raise ServiceMemoApprovalError(
            f"Согласование недоступно: текущий статус «{previous_status or 'не указан'}»"
        )

    patch_payload = build_approval_patch(
        session,
        config,
        approver_fio=approver_fio,
        comment=comment,
    )
    patch_service_memo_status(session, config, resolved_ref, patch_payload)
    after = fetch_document_header(session, config, resolved_ref)

    return {
        "ref_key": resolved_ref,
        "number": after.get("Number"),
        "date": after.get("Date"),
        "posted": after.get("Posted"),
        "status": after.get("Статус"),
        "previous_status": previous_status,
        "already_approved": False,
        "changed": True,
        "approver_fio": approver_fio,
        "comment": comment,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Согласовать служебную записку по совещанию в 1С:ERP (OData).",
    )
    parser.add_argument("--ref-key", help="Ref_Key документа")
    parser.add_argument("--number", help="Номер документа, например 000010430")
    parser.add_argument(
        "--approver-fio",
        help="ФИО согласующего (Catalog_Пользователи) для поля ИсполнительУД",
    )
    parser.add_argument("--comment", help="Комментарий к согласованию")
    parser.add_argument(
        "--allow-any-status",
        action="store_true",
        help="Разрешить согласование при статусе, отличном от «НеСогласована»",
    )
    parser.add_argument(
        "--skip-theme-check",
        action="store_true",
        help="Не проверять тему «Организация совещаний»",
    )
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = approve_service_memo(
            ref_key=args.ref_key,
            number=args.number,
            approver_fio=args.approver_fio,
            comment=args.comment,
            require_unapproved=not args.allow_any_status,
            validate_meeting_theme=not args.skip_theme_check,
        )
    except (ServiceMemoApprovalError, RuntimeError, requests.RequestException) as error:
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
