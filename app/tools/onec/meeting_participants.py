from __future__ import annotations

from typing import Any

from app.tools.onec.connection import ODataConfig

_MEMO_FIELDS = (
    "Ref_Key",
    "Number",
    "Date",
    "Posted",
    "DeletionMark",
    "ТемаСлужебнойЗаписки",
    "ТемаСлужебнойЗаписки_Key",
    "Комментарий",
    "Ответственный",
    "Автор",
)


def _extract_memo(header: dict[str, Any]) -> dict[str, Any]:
    memo = {field: header.get(field) for field in _MEMO_FIELDS if field in header}
    if not memo:
        return dict(header)
    return memo


def collect_participants_for_memo(
    header: dict[str, Any],
    *,
    session: Any,
    config: ODataConfig,
) -> list[dict[str, Any]]:
    """Собирает участников из шапки документа и табличных полей (базовая версия)."""
    del session, config
    participants: list[dict[str, Any]] = []
    for key, value in header.items():
        if "Участник" not in key:
            continue
        if isinstance(value, dict):
            participants.append(value)
        elif isinstance(value, str) and value.strip():
            participants.append({"Description": value.strip(), "source_field": key})
    return participants


def build_combined_document(
    header: dict[str, Any],
    participants: list[dict[str, Any]],
    *,
    tabular_sections: dict[str, list[dict[str, Any]]] | None = None,
    include_full_header: bool = True,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "memo": _extract_memo(header),
        "participants": participants,
    }
    if tabular_sections:
        document["tabular_sections"] = tabular_sections
    if include_full_header:
        document["header"] = header
    return document
