from __future__ import annotations

from typing import Any, Literal

MeetingKind = Literal["single", "series_master", "series_occurrence"]
SeriesScope = Literal["occurrence", "series"]
CancelScope = SeriesScope
RescheduleScope = SeriesScope

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


def available_series_scopes(item: Any) -> list[SeriesScope]:
    kind = meeting_kind(item)
    if kind == "single":
        return ["occurrence"]
    return ["occurrence", "series"]


def available_cancel_scopes(item: Any) -> list[CancelScope]:
    return available_series_scopes(item)


def available_reschedule_scopes(item: Any) -> list[RescheduleScope]:
    return available_series_scopes(item)


def series_master_id(item: Any) -> str | None:
    kind = meeting_kind(item)
    if kind == "series_master":
        return str(getattr(item, "id", None) or "") or None
    if kind == "series_occurrence":
        master = item.recurring_master()
        return str(getattr(master, "id", None) or "") or None
    return None


AttendeesScope = SeriesScope


def available_attendees_scopes(item: Any) -> list[AttendeesScope]:
    return available_series_scopes(item)


def _series_scope_error(*, kind: MeetingKind, scope: SeriesScope, action: str) -> RuntimeError:
    if scope == "series" and kind == "single":
        return RuntimeError(
            f"Это разовое совещание, серии нет. Используйте {action}_scope=occurrence."
        )
    if scope == "occurrence" and kind == "series_master":
        return RuntimeError(
            "Найдена серия целиком (RecurringMaster). "
            f"Для {action} одного совещания укажите start конкретного вхождения "
            f"или {action}_scope=series, чтобы обработать всю серию."
        )
    raise RuntimeError(f"Неподдерживаемая комбинация kind={kind}, scope={scope}")


def resolve_series_target(
    item: Any,
    *,
    scope: SeriesScope,
    action: Literal["cancel", "reschedule", "attendees"],
) -> tuple[Any, MeetingKind, SeriesScope]:
    """Возвращает CalendarItem для отмены/переноса и фактический scope."""
    kind = meeting_kind(item)
    if scope == "series":
        if kind == "single":
            raise _series_scope_error(kind=kind, scope=scope, action=action)
        if kind == "series_occurrence":
            master = item.recurring_master()
            master.refresh()
            return master, "series_master", "series"
        return item, kind, "series"

    if kind == "series_master":
        raise _series_scope_error(kind=kind, scope=scope, action=action)
    return item, kind, "occurrence"


def resolve_cancel_target(item: Any, *, scope: CancelScope) -> tuple[Any, MeetingKind, CancelScope]:
    return resolve_series_target(item, scope=scope, action="cancel")


def resolve_reschedule_target(
    item: Any,
    *,
    scope: RescheduleScope,
) -> tuple[Any, MeetingKind, RescheduleScope]:
    return resolve_series_target(item, scope=scope, action="reschedule")


def resolve_attendees_target(
    item: Any,
    *,
    scope: AttendeesScope,
) -> tuple[Any, MeetingKind, AttendeesScope]:
    return resolve_series_target(item, scope=scope, action="attendees")


def meeting_series_fields(item: Any) -> dict[str, Any]:
    kind = meeting_kind(item)
    scopes = available_series_scopes(item)
    return {
        "kind": kind,
        "item_type": meeting_item_type(item),
        "is_recurring": kind != "single",
        "is_series": kind != "single",
        "series_master_id": series_master_id(item),
        "cancel_scope_options": scopes,
        "reschedule_scope_options": scopes,
        "attendees_scope_options": scopes,
    }
