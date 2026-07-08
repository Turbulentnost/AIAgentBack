"""Обратная совместимость: импорты из app.services.meeting_backend."""

from app.services.meeting_backend import *  # noqa: F403
from app.services.meeting_backend import (  # noqa: F401
    _duration_from_memo,
    _extract_participant_fio,
    _find_memo_document,
    _normalize_memo,
    _preferred_from_memo,
    _slot_conflict_from_payload,
)
