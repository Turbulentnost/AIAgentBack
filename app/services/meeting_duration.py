from __future__ import annotations

MIN_MEETING_DURATION_MINUTES = 15
MAX_MEETING_DURATION_MINUTES = 480
DEFAULT_MEETING_DURATION_MINUTES = 60


def normalize_request_duration_minutes(value: object) -> int | None:
    """Короткие значения из UI/СЗ (< 15 мин) сбрасываем — подставим из заявки или 60 мин."""
    if value is None or value == "":
        return None
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if minutes < MIN_MEETING_DURATION_MINUTES:
        return None
    return min(minutes, MAX_MEETING_DURATION_MINUTES)


def resolve_duration_minutes(
    explicit: int | None = None,
    *fallbacks: int | None,
    default: int = DEFAULT_MEETING_DURATION_MINUTES,
) -> int:
    for candidate in (explicit, *fallbacks):
        if isinstance(candidate, int) and candidate >= MIN_MEETING_DURATION_MINUTES:
            return min(candidate, MAX_MEETING_DURATION_MINUTES)
    return default
