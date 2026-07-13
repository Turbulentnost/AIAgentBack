from __future__ import annotations

from typing import Any, Literal

MeetingKind = Literal["single", "series_master", "series_occurrence"]
CancelScope = Literal["occurrence", "series"]

SERIES_MASTER_TYPE = "RecurringMaster"
SERIES_OCCURRENCE_TYPES = frozenset({"Occurrence", "Exception"})


def meeting_item_type(item: Any) -> str:
    return str(getattr(item, "type", None) or "Single")


def meeting_kind(item: Any) -> MeetingKind:
    item_type = meeting_item_type(item)
    if item_type == SERIES_MASTER_TYPE:
        return "series_master"
    if item_type in SERIES_OCCURRENCE_TYPES:
        return "series_occurrence"
    return "single"


def is_recurring_meeting(item: Any) -> bool:
    return meeting_kind(item) != "single"


def available_cancel_scopes(item: Any) -> list[CancelScope]:
    kind = meeting_kind(item)
    if kind == "single":
        return ["occurrence"]
    return ["occurrence", "series"]


def series_master_id(item: Any) -> str | None:
    kind = meeting_kind(item)
    if kind == "series_master":
        return str(getattr(item, "id", None) or "") or None
    if kind == "series_occurrence":
        master = item.recurring_master()
        return str(getattr(master, "id", None) or "") or None
    return None


def resolve_cancel_target(item: Any, *, scope: CancelScope) -> tuple[Any, MeetingKind, CancelScope]:
    """Возвращает CalendarItem для отмены и фактический scope."""
    kind = meeting_kind(item)
    if scope == "series":
        if kind == "single":
            raise RuntimeError(
                "Это разовое совещание, серии нет. Используйте cancel_scope=occurrence."
            )
        if kind == "series_occurrence":
            master = item.recurring_master()
            master.refresh()
            return master, "series_master", "series"
        return item, kind, "series"

    if kind == "series_master":
        raise RuntimeError(
            "Найдена серия целиком (RecurringMaster). "
            "Для отмены одного совещания укажите start конкретного вхождения "
            "или cancel_scope=series, чтобы отменить всю серию."
        )
    return item, kind, "occurrence"


def meeting_series_fields(item: Any) -> dict[str, Any]:
    kind = meeting_kind(item)
    scopes = available_cancel_scopes(item)
    return {
        "kind": kind,
        "item_type": meeting_item_type(item),
        "is_recurring": kind != "single",
        "is_series": kind != "single",
        "series_master_id": series_master_id(item),
        "cancel_scope_options": scopes,
    }
