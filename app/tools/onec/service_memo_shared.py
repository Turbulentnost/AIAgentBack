"""Общие функции для согласования и отклонения служебных записок в 1С."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from app.tools.onec.connection import ODataConfig, create_session
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    entity_url,
    fetch_document_header,
    fetch_meeting_memo_rows,
    load_metadata_xml,
    theme_matches,
)
from app.tools.onec.lookup_user_ref import USER_CATALOG, is_empty_key, resolve_user_by_fio

APPROVED_STATUS = "Согласована"
UNAPPROVED_STATUS = "НеСогласована"
REJECTED_STATUS = "Отклонена"


def format_rejection_comment(reason: str) -> str:
    """Текст для поля Комментарий в 1С — только причина отклонения."""
    text = " ".join((reason or "").strip().split())
    if not text:
        return text
    if text.startswith("Служебная записка") and "Причина:" in text:
        text = text.rsplit("Причина:", 1)[-1].strip()
    return text.rstrip(".")


class ServiceMemoWorkflowError(ValueError):
    """Ошибка бизнес-валидации при работе со служебной запиской."""


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
        raise ServiceMemoWorkflowError("Укажите ref_key или number служебной записки")

    safe_number = memo_number.replace("'", "''")
    rows = fetch_meeting_memo_rows(
        session,
        config,
        f"Number eq '{safe_number}'",
        limit=1,
        fetch_pool=20,
    )
    if not rows:
        raise ServiceMemoWorkflowError(f"Служебная записка не найдена: {memo_number}")
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
    raise ServiceMemoWorkflowError(
        "Документ не относится к теме служебных записок по совещаниям"
    )


def fetch_user_description_by_ref(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> str:
    response = session.get(
        f"{entity_url(config.url, USER_CATALOG)}(guid'{user_ref}')?$select=Description&$format=json",
        timeout=config.timeout,
    )
    if not response.ok:
        raise ServiceMemoWorkflowError(
            f"Пользователь не найден: HTTP {response.status_code}: {response.text[:300]}"
        )
    description = str(response.json().get("Description") or "").strip()
    if not description:
        raise ServiceMemoWorkflowError("У пользователя-инициатора не заполнено ФИО")
    return description


def resolve_initiator_from_header(
    session: requests.Session,
    config: ODataConfig,
    header: dict[str, Any],
) -> tuple[str, str]:
    user_ref = header.get("Ответственный_Key")
    if not isinstance(user_ref, str) or is_empty_key(user_ref):
        raise ServiceMemoWorkflowError("Не указан инициатор СЗ (Ответственный)")
    fio = fetch_user_description_by_ref(session, config, user_ref)
    return user_ref, fio


def patch_service_memo(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
    payload: dict[str, Any],
    *,
    action_label: str = "изменения",
) -> dict[str, Any]:
    url = f"{entity_url(config.url, DOCUMENT_ENTITY)}(guid'{ref_key}')?$format=json"
    response = session.patch(url, json=payload, timeout=config.timeout)
    if not response.ok:
        raise RuntimeError(
            f"Ошибка {action_label} СЗ: HTTP {response.status_code}: {response.text[:800]}"
        )
    return response.json()


def apply_executor_fields(
    session: requests.Session,
    config: ODataConfig,
    payload: dict[str, Any],
    *,
    executor_fio: str | None,
) -> None:
    if not executor_fio:
        return
    user_ref, _, _ = resolve_user_by_fio(session, executor_fio, config=config)
    payload["ИсполнительУД_Key"] = user_ref
    payload["ИсполнительУД_Type"] = "StandardODATA.Catalog_Пользователи"


def now_ud_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def load_memo_header(
    *,
    ref_key: str | None = None,
    number: str | None = None,
    config: ODataConfig,
) -> tuple[requests.Session, str, dict[str, Any], Any]:
    session = create_session(config)
    metadata = load_metadata_xml(session, config)
    resolved_ref = resolve_memo_ref_key(session, config, ref_key=ref_key, number=number)
    header = fetch_document_header(session, config, resolved_ref)
    return session, resolved_ref, header, metadata
