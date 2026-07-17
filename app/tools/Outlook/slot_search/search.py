from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.read_calendars import read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import load_config

from .attendees import (
    _human_calendar_attendee_emails,
    _is_resource_calendar_email,
    calendar_item_attendee_emails,
    normalize_calendar_email,
)
from .availability import (
    is_free_for_all,
    partition_attendees_at_slot,
    union_busy_for_all,
    verify_slot_with_calendar,
)
from .busy import (
    coalesce_intervals,
    event_interval,
    fetch_all_busy_intervals,
    fetch_busy_intervals_calendar,
    fetch_busy_intervals_freebusy,
    fetch_freebusy_calendar_events,
    merge_busy_intervals,
)
from .conflicts import (
    attach_reschedule_hints,
    build_conflict_records,
    conflicting_calendar_items_at_slot,
    dedupe_conflict_records,
    movability_reason,
    movability_score,
    suggest_reschedule_window,
)
from .constants import (
    AvailabilitySource,
    COMPANY_CALENDAR_CHUNK_HOURS,
    COMPANY_CALENDAR_MAX_CANDIDATES,
    COMPANY_CALENDAR_MAX_ITEMS_PER_CHUNK,
    COMPANY_CALENDAR_STEP_MINUTES,
    MOVABILITY_SORT_RANK,
    QUORUM_RANK_SHORTLIST_MULTIPLIER,
)
from .iteration import (
    find_slot_via_busy_gaps,
    first_valid_slot_in_window,
    iterate_slot_candidates,
    quorum_search_start,
)
from .rules import align_preferred, intervals_overlap, not_before_now, slot_respects_rules
from .scoring import (
    _build_quorum_candidate_payload,
    _quorum_candidate_sort_key,
    _quorum_pool_sort_key,
    _slot_preference_distance,
    count_easy_reschedule_conflicts,
    count_low_movability_conflicts,
    coverage_ratios,
    preliminary_slot_impact,
    slot_impact_score,
)
from .timing import logger, log_timing_summary, reset_timing_report, setup_logging, timed_step


def _iter_company_calendar_windows(
    search_start: datetime,
    search_end: datetime,
    *,
    config: OutlookConfig,
    chunk_hours: int = COMPANY_CALENDAR_CHUNK_HOURS,
) -> Iterator[tuple[datetime, datetime]]:
    cursor = to_local(search_start, config)
    end = to_local(search_end, config)
    step = timedelta(hours=max(chunk_hours, 1))
    while cursor < end:
        window_end = min(cursor + step, end)
        yield cursor, window_end
        cursor = window_end

def _meeting_attendees_in_event(
    item: Any,
    attendee_set: set[str],
) -> list[str]:
    return [
        email
        for email in _human_calendar_attendee_emails(item)
        if email in attendee_set
    ]

def _event_blocks_target_or_busy(
    event_start: datetime,
    event_end: datetime,
    *,
    target_start: datetime,
    target_end: datetime,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    matched_attendees: list[str],
    config: OutlookConfig,
) -> bool:
    if intervals_overlap(event_start, event_end, target_start, target_end):
        return True
    for email in matched_attendees:
        for busy_start, busy_end in busy_by_attendee.get(email, []):
            busy_start_local = to_local(busy_start, config)
            busy_end_local = to_local(busy_end, config)
            if intervals_overlap(event_start, event_end, busy_start_local, busy_end_local):
                return True
    return False

def _pick_primary_blocked_attendee(
    matched_attendees: list[str],
    *,
    attendee_weights: dict[str, float] | None,
) -> str:
    if not matched_attendees:
        return ""
    weights = attendee_weights or {}
    return max(matched_attendees, key=lambda email: weights.get(email, 1.0))

def _company_calendar_candidate_sort_key(
    record: dict[str, Any],
    *,
    target_start: datetime,
    config: OutlookConfig,
) -> tuple[int, int, float]:
    movability = str(record.get("movability") or "medium")
    movability_rank = MOVABILITY_SORT_RANK.get(movability, 1)
    required_hits = int(record.get("required_attendee_hits") or 0)
    event_start_raw = record.get("event_start")
    distance = 0.0
    if event_start_raw:
        event_start = datetime.fromisoformat(str(event_start_raw))
        distance = abs((to_local(event_start, config) - target_start).total_seconds())
    return (movability_rank, required_hits, distance)

def find_company_calendar_reschedule_candidates(
    *,
    attendee_emails: list[str],
    required_attendee_emails: list[str] | None = None,
    planned_start: datetime,
    duration: timedelta,
    max_days: int,
    attendee_weights: dict[str, float] | None = None,
    max_results: int = COMPANY_CALENDAR_MAX_CANDIDATES,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Кандидаты на перенос из общего календаря компании при полном отсутствии слота."""
    config = config or load_config()
    company_calendar = (config.company_calendar or "").strip().lower()
    if not company_calendar:
        return {
            "company_calendar": None,
            "candidates": [],
            "events_scanned": 0,
            "search_window": None,
        }

    normalized_attendees = [
        email.strip().lower()
        for email in attendee_emails
        if isinstance(email, str) and email.strip()
    ]
    attendee_set = set(dict.fromkeys(normalized_attendees))
    if not attendee_set:
        return {
            "company_calendar": company_calendar,
            "candidates": [],
            "events_scanned": 0,
            "search_window": None,
        }

    required_set = {
        email.strip().lower()
        for email in (required_attendee_emails or normalized_attendees)
        if email.strip()
    }
    target_start = to_local(planned_start, config).replace(second=0, microsecond=0)
    if duration <= timedelta(0):
        duration = timedelta(minutes=30)
    target_end = target_start + duration
    search_start = quorum_search_start(target_start, config)
    search_end = search_start + timedelta(days=max(max_days, 1))
    step = timedelta(minutes=COMPANY_CALENDAR_STEP_MINUTES)

    busy_by_attendee = fetch_busy_intervals_freebusy(
        config,
        list(attendee_set),
        search_start,
        search_end,
    )

    records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    events_scanned = 0

    for window_start, window_end in _iter_company_calendar_windows(
        search_start,
        search_end,
        config=config,
    ):
        try:
            items = read_calendar_items_in_range(
                config,
                company_calendar,
                range_start=window_start,
                range_end=window_end,
                max_items=COMPANY_CALENDAR_MAX_ITEMS_PER_CHUNK,
            )
        except Exception as exc:
            logger.warning(
                "company_calendar_chunk_failed window=%s..%s error=%s",
                window_start.isoformat(),
                window_end.isoformat(),
                exc,
            )
            continue

        for item in items:
            events_scanned += 1
            interval = event_interval(item, config)
            if interval is None:
                continue
            event_start, event_end = interval
            matched_attendees = _meeting_attendees_in_event(item, attendee_set)
            if not matched_attendees:
                continue
            if not _event_blocks_target_or_busy(
                event_start,
                event_end,
                target_start=target_start,
                target_end=target_end,
                busy_by_attendee=busy_by_attendee,
                matched_attendees=matched_attendees,
                config=config,
            ):
                continue

            subject = str(getattr(item, "subject", "") or "").strip()
            busy_type = str(getattr(item, "legacy_free_busy_status", "") or "").strip()
            organizer = None
            organizer_obj = getattr(item, "organizer", None)
            if organizer_obj is not None:
                organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
            primary_email = _pick_primary_blocked_attendee(
                matched_attendees,
                attendee_weights=attendee_weights,
            )
            primary_busy = busy_by_attendee.get(primary_email, [])
            hint = suggest_reschedule_window(
                event_start=event_start,
                event_end=event_end,
                busy_intervals=primary_busy,
                config=config,
                step=step,
                search_end=search_end,
                reserved_slot=(target_start, target_end),
                owner_email=primary_email,
                meeting_attendees=matched_attendees,
            )
            record = {
                "email": primary_email,
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "organizer": organizer,
                "event_attendees": matched_attendees,
                "required_attendee_hits": sum(
                    1 for email in matched_attendees if email in required_set
                ),
                "movability": movability_score(busy_type=busy_type, subject=subject),
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source="company_calendar",
                ),
                "source": "company_calendar",
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
            dedupe_key = (
                record["event_start"],
                record["event_end"],
                str(record.get("event_subject") or ""),
            )
            existing = records_by_key.get(dedupe_key)
            if existing is None or record["required_attendee_hits"] > int(
                existing.get("required_attendee_hits") or 0
            ):
                records_by_key[dedupe_key] = record

    candidates = sorted(
        records_by_key.values(),
        key=lambda item: _company_calendar_candidate_sort_key(
            item,
            target_start=target_start,
            config=config,
        ),
    )[: max(max_results, 1)]

    return {
        "company_calendar": company_calendar,
        "candidates": candidates,
        "events_scanned": events_scanned,
        "search_window": {
            "start": search_start.isoformat(),
            "end": search_end.isoformat(),
            "target_start": target_start.isoformat(),
            "target_end": target_end.isoformat(),
        },
    }

def find_quorum_slots(
    *,
    config: OutlookConfig,
    attendees: list[str],
    preferred: datetime,
    duration: timedelta,
    max_days: int,
    step: timedelta,
    max_items: int,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    required_attendees: list[str] | None = None,
    attendee_weights: dict[str, float] | None = None,
    min_coverage_ratio: float = 0.7,
    max_results: int = 3,
    verify_top_n: int = 3,
    verify_calendar: bool = True,
    latest_allowed: datetime | None = None,
    raise_if_empty: bool = True,
) -> dict[str, Any]:
    if not attendees:
        raise ValueError("Укажите хотя бы одного участника (--attendee).")
    if duration <= timedelta(0):
        raise ValueError("Длительность должна быть больше 0.")
    if max_days < 1:
        raise ValueError("--max-days должно быть >= 1.")
    if not 0 < min_coverage_ratio <= 1:
        raise ValueError("min_coverage_ratio должно быть в диапазоне (0, 1].")

    required = [email for email in (required_attendees or attendees) if email in attendees]
    if not required:
        required = list(attendees)

    requested = to_local(preferred, config).replace(second=0, microsecond=0)
    earliest_allowed = quorum_search_start(preferred, config)
    search_end = earliest_allowed + timedelta(days=max_days)
    if latest_allowed is not None:
        latest_local = to_local(latest_allowed, config).replace(second=0, microsecond=0)
        search_end = min(search_end, latest_local)

    busy_by_attendee = fetch_all_busy_intervals(
        config,
        attendees,
        earliest_allowed,
        search_end,
        source=source,
        max_items=max_items,
        workers=workers,
    )

    checked = 0
    scored: list[dict[str, Any]] = []
    scored_fallback: list[dict[str, Any]] = []
    required_set = set(required)
    with timed_step("scan.quorum_slots", max_days=max_days, step_minutes=int(step.total_seconds() // 60)):
        for candidate in iterate_slot_candidates(
            earliest_allowed,
            search_end,
            duration=duration,
            step=step,
            config=config,
        ):
            checked += 1
            if latest_allowed is not None:
                latest_local = to_local(latest_allowed, config).replace(second=0, microsecond=0)
                if candidate >= latest_local:
                    continue
            free_attendees, busy_attendees = partition_attendees_at_slot(
                candidate,
                duration,
                attendees=attendees,
                busy_by_attendee=busy_by_attendee,
                config=config,
            )
            if not free_attendees:
                continue
            required_ok = all(email in free_attendees for email in required_set)
            weighted_ratio, flat_ratio = coverage_ratios(
                free_attendees,
                attendees,
                attendee_weights,
            )
            score_ratio = weighted_ratio if attendee_weights else flat_ratio
            payload = {
                "slot_start": candidate,
                "slot_end": candidate + duration,
                "free_attendees": free_attendees,
                "busy_attendees": busy_attendees,
                "coverage_ratio": flat_ratio,
                "weighted_coverage_ratio": weighted_ratio,
                "score_ratio": score_ratio,
                "required_ok": required_ok,
                "preliminary_impact": preliminary_slot_impact(
                    score_ratio=score_ratio,
                    busy_attendees=busy_attendees,
                    required=required,
                    required_ok=required_ok,
                    attendee_weights=attendee_weights,
                ),
            }
            scored_fallback.append(payload)
            if not required_ok or score_ratio < min_coverage_ratio:
                continue
            scored.append(payload)

    use_fallback = not scored
    pool = scored_fallback if use_fallback else scored
    pool.sort(key=lambda item: _quorum_pool_sort_key(item, preferred=requested, config=config))
    shortlist_size = min(
        len(pool),
        max(max_results * QUORUM_RANK_SHORTLIST_MULTIPLIER, max_results + 10),
    )
    shortlisted = pool[: max(shortlist_size, 1)]

    verify_count = max(verify_top_n, 0) if verify_calendar else 0
    candidates: list[dict[str, Any]] = []
    reschedule_assisted = False

    def append_candidate(
        item: dict[str, Any],
        *,
        index: int,
        allow_required_failures: bool,
    ) -> None:
        slot_start: datetime = item["slot_start"]
        verified = False
        free_attendees = list(item["free_attendees"])
        busy_attendees = list(item["busy_attendees"])
        if index < verify_count:
            calendar_ok, calendar_busy = verify_slot_with_calendar(
                config=config,
                attendees=attendees,
                slot_start=slot_start,
                duration=duration,
                max_items=max_items,
                workers=workers,
            )
            verified = calendar_ok
            free_attendees, busy_attendees = partition_attendees_at_slot(
                slot_start,
                duration,
                attendees=attendees,
                busy_by_attendee=calendar_busy,
                config=config,
            )
            if not allow_required_failures and not all(email in free_attendees for email in required_set):
                return
            if not free_attendees:
                return
        candidates.append(
            _build_quorum_candidate_payload(
                item=item,
                attendees=attendees,
                required=required,
                required_set=required_set,
                busy_by_attendee=busy_by_attendee,
                attendee_weights=attendee_weights,
                config=config,
                duration=duration,
                step=step,
                search_end=search_end,
                verified=verified,
                free_attendees=free_attendees,
                busy_attendees=busy_attendees,
            )
        )

    for index, item in enumerate(shortlisted):
        append_candidate(item, index=index, allow_required_failures=use_fallback)

    if not candidates and scored_fallback:
        reschedule_pool = sorted(
            scored_fallback,
            key=lambda item: (
                item["preliminary_impact"],
                -item["score_ratio"],
                _slot_preference_distance(item["slot_start"], requested, config),
            ),
        )
        for index, item in enumerate(reschedule_pool[: max(shortlist_size, 1)]):
            append_candidate(item, index=index, allow_required_failures=True)
        reschedule_assisted = bool(candidates)
        use_fallback = use_fallback or reschedule_assisted

    candidates.sort(key=lambda item: _quorum_candidate_sort_key(item, preferred=requested, config=config))
    candidates = candidates[: max(max_results, 1)]

    if not candidates:
        if not raise_if_empty:
            return {
                "preferred": requested.isoformat(),
                "earliest_allowed": earliest_allowed.isoformat(),
                "search_until": search_end.isoformat(),
                "min_coverage_ratio": min_coverage_ratio,
                "required_attendees": required,
                "attendees": attendees,
                "checked_candidates": checked,
                "availability_source": source,
                "search_mode": "empty",
                "partial_fallback": use_fallback,
                "candidates": [],
                **_availability_snapshot_fields(
                    attendees=attendees,
                    window_start=earliest_allowed,
                    window_end=search_end,
                    busy_by_attendee=busy_by_attendee,
                ),
            }
        raise RuntimeError(
            f"Quorum-слот не найден: min_coverage={min_coverage_ratio:.0%}, "
            f"required={len(required)}, search_days={max_days}."
        )

    return {
        "preferred": requested.isoformat(),
        "earliest_allowed": earliest_allowed.isoformat(),
        "search_until": search_end.isoformat(),
        "min_coverage_ratio": min_coverage_ratio,
        "required_attendees": required,
        "attendees": attendees,
        "checked_candidates": checked,
        "availability_source": source,
        "search_mode": (
            "reschedule_assisted"
            if reschedule_assisted
            else ("quorum_fallback" if use_fallback else "quorum")
        ),
        "partial_fallback": use_fallback,
        "candidates": candidates,
        **_availability_snapshot_fields(
            attendees=attendees,
            window_start=earliest_allowed,
            window_end=search_end,
            busy_by_attendee=busy_by_attendee,
        ),
    }


def _availability_snapshot_fields(
    *,
    attendees: list[str],
    window_start: datetime,
    window_end: datetime,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
) -> dict[str, Any]:
    from app.services.slot_availability_cache import serialize_busy_snapshot

    return {
        "availability_snapshot": serialize_busy_snapshot(
            memo_ref_key="",
            attendee_emails=attendees,
            window_start=window_start,
            window_end=window_end,
            busy_by_attendee=busy_by_attendee,
        ),
    }

def _slot_search_result(
    *,
    requested: datetime,
    earliest_allowed: datetime,
    candidate: datetime,
    duration: timedelta,
    attendees: list[str],
    checked: int,
    search_end: datetime,
    source: AvailabilitySource,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] | None = None,
    memo_ref_key: str | None = None,
) -> dict[str, Any]:
    end = candidate + duration
    result: dict[str, Any] = {
        "preferred": requested.isoformat(),
        "earliest_allowed": earliest_allowed.isoformat(),
        "slot_start": candidate.isoformat(),
        "slot_end": end.isoformat(),
        "duration_minutes": int(duration.total_seconds() // 60),
        "attendees": attendees,
        "checked_candidates": checked,
        "search_until": search_end.isoformat(),
        "availability_source": source,
    }
    if busy_by_attendee is not None:
        from app.services.slot_availability_cache import serialize_busy_snapshot

        result["availability_snapshot"] = serialize_busy_snapshot(
            memo_ref_key=memo_ref_key or "",
            attendee_emails=attendees,
            window_start=earliest_allowed,
            window_end=search_end,
            busy_by_attendee=busy_by_attendee,
        )
    return result


def find_nearest_slot(
    *,
    config: OutlookConfig,
    attendees: list[str],
    preferred: datetime,
    duration: timedelta,
    max_days: int,
    step: timedelta,
    max_items: int,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    verify_calendar: bool = True,
) -> dict[str, Any]:
    if not attendees:
        raise ValueError("Укажите хотя бы одного участника (--attendee).")
    if duration <= timedelta(0):
        raise ValueError("Длительность должна быть больше 0.")
    if max_days < 1:
        raise ValueError("--max-days должно быть >= 1.")

    with timed_step("align.preferred"):
        requested = to_local(preferred, config).replace(second=0, microsecond=0)
        search_start = align_preferred(requested, config)
        earliest_allowed = max(requested, search_start, not_before_now(config))
    search_end = earliest_allowed + timedelta(days=max_days)
    logger.info(
        "Поиск: requested=%s, earliest=%s, until=%s, attendees=%d, step=%s, duration=%s, source=%s",
        requested.isoformat(),
        earliest_allowed.isoformat(),
        search_end.isoformat(),
        len(attendees),
        step,
        duration,
        source,
    )

    logger.info(
        "Загрузка занятости (%d участников, %d дн., метод=%s) ...",
        len(attendees),
        max_days,
        source,
    )
    busy_by_attendee = fetch_all_busy_intervals(
        config,
        attendees,
        earliest_allowed,
        search_end,
        source=source,
        max_items=max_items,
        workers=workers,
    )

    union_busy = union_busy_for_all(
        busy_by_attendee,
        config,
        earliest_allowed,
        search_end,
    )
    logger.info(
        "Объединённая занятость: %d блоков (если 0 — все свободны в диапазоне)",
        len(union_busy),
    )

    checked = 0
    union_busy_search = list(union_busy)
    max_calendar_verifications = max(
        500,
        int((search_end - earliest_allowed).total_seconds() // max(step.total_seconds(), 60)) + 10,
    )
    verification_attempts = 0
    with timed_step("scan.slots", max_days=max_days, step_minutes=int(step.total_seconds() // 60)):
        while True:
            slot, step_checked = find_slot_via_busy_gaps(
                earliest_allowed=earliest_allowed,
                search_end=search_end,
                duration=duration,
                step=step,
                union_busy=union_busy_search,
                config=config,
            )
            checked += step_checked
            if slot is None:
                break
            if slot < earliest_allowed:
                logger.warning(
                    "Пропуск слота раньше earliest_allowed: %s < %s",
                    slot.isoformat(),
                    earliest_allowed.isoformat(),
                )
                union_busy_search = coalesce_intervals(
                    union_busy_search + [(slot, slot + duration)],
                    config,
                    clip_start=earliest_allowed,
                    clip_end=search_end,
                )
                verification_attempts += 1
                if verification_attempts >= max_calendar_verifications:
                    break
                continue
            if not verify_calendar or source == "calendar":
                if not verify_calendar:
                    logger.info("Слот найден после %d проверок (free/busy, по промежуткам)", checked)
                else:
                    logger.info("Слот найден после %d проверок (calendar)", checked)
                return _slot_search_result(
                    requested=requested,
                    earliest_allowed=earliest_allowed,
                    candidate=slot,
                    duration=duration,
                    attendees=attendees,
                    checked=checked,
                    search_end=search_end,
                    source=source,
                    busy_by_attendee=busy_by_attendee,
                )
            calendar_ok, _calendar_busy = verify_slot_with_calendar(
                config=config,
                attendees=attendees,
                slot_start=slot,
                duration=duration,
                max_items=max_items,
                workers=workers,
            )
            if calendar_ok:
                logger.info("Слот найден после %d проверок (free/busy + events)", checked)
                return _slot_search_result(
                    requested=requested,
                    earliest_allowed=earliest_allowed,
                    candidate=slot,
                    duration=duration,
                    attendees=attendees,
                    checked=checked,
                    search_end=search_end,
                    source=source,
                    busy_by_attendee=busy_by_attendee,
                )
            logger.info(
                "Слот %s свободен по merged, но занят по free/busy events — ищем следующий",
                slot.isoformat(),
            )
            union_busy_search = coalesce_intervals(
                union_busy_search + [(slot, slot + duration)],
                config,
                clip_start=earliest_allowed,
                clip_end=search_end,
            )
            verification_attempts += 1
            if verification_attempts >= max_calendar_verifications:
                logger.warning(
                    "Достигнут лимит проверок calendar_events=%d",
                    max_calendar_verifications,
                )
                break

    busy_summary = ", ".join(
        f"{email}: {len(intervals)} интервалов"
        for email, intervals in busy_by_attendee.items()
    )
    logger.info(
        "Слот не найден: проверено=%d; объединённых блоков занятости=%d; %s",
        checked,
        len(union_busy),
        busy_summary,
    )
    raise RuntimeError(
        f"Свободный слот не найден в течение {max_days} дн. от {earliest_allowed.isoformat()}."
    )

