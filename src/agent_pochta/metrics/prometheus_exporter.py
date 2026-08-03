"""Сбор метрик agent-pochta из PostgreSQL / JSON и экспорт в Prometheus."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from prometheus_client import Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_pochta.config import get_settings
from agent_pochta.db.message_filters import MSK
from agent_pochta.db.models import ChangeEventRow, ClassificationEventRow, EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.corrections import load_corrections
from agent_pochta.stats.classification_log import operator_approval_rate

# Изменения полей без подтверждений маршрута (как в stats/export.py).
FIELD_CHANGE_EVENT_TYPES = (
    "department_change",
    "spam_mark",
    "not_spam_mark",
    "restore_from_spam",
    "organization_change",
    "partner_change",
    "process_change",
)

DEPARTMENT_CHANGE_EVENT = "department_change"
SPAM_MARK_EVENT = "spam_mark"
NOT_SPAM_EVENTS = ("not_spam_mark", "restore_from_spam")
OPERATOR_APPROVE_EVENT = "operator_approve"
OPERATOR_CHANGE_EVENT = "operator_change"
# Статусы «полезной» маршрутизации задач (без spam / processing).
ROUTED_DEPARTMENT_STATUSES = ("done", "awaiting_human", "error")
_MAX_DEPARTMENT_LABEL_LEN = 120

CHANGES_LAST_24H = Gauge(
    "agent_pochta_changes_last_24h",
    "Количество изменений полей за последние 24 часа (MSK, без routing_approve)",
)
MESSAGES_LAST_24H = Gauge(
    "agent_pochta_messages_last_24h",
    "Количество полученных писем за последние 24 часа (MSK, received_at)",
)
CHANGE_PERCENT_LAST_24H = Gauge(
    "agent_pochta_change_percent_last_24h",
    "Доля изменений от числа писем за 24ч, % (changes / messages * 100)",
)
MESSAGES_TOTAL = Gauge(
    "agent_pochta_messages_total",
    "Всего полученных писем (cumulative, received_at)",
)
CHANGES_TOTAL = Gauge(
    "agent_pochta_changes_total",
    "Всего изменений полей (cumulative, без routing_approve)",
)
DEPARTMENT_CHANGES_LAST_24H = Gauge(
    "agent_pochta_department_changes_last_24h",
    "Смены отдела за последние 24 часа (MSK)",
)
DEPARTMENT_CHANGES_TOTAL = Gauge(
    "agent_pochta_department_changes_total",
    "Смены отдела за всё время",
)
SPAM_MARK_LAST_24H = Gauge(
    "agent_pochta_spam_mark_last_24h",
    "Переход not_spam → spam (spam_mark) за последние 24 часа (MSK)",
)
SPAM_MARK_TOTAL = Gauge(
    "agent_pochta_spam_mark_total",
    "Переход not_spam → spam (spam_mark) за всё время",
)
NOT_SPAM_MARK_LAST_24H = Gauge(
    "agent_pochta_not_spam_mark_last_24h",
    "Переход spam → not_spam (not_spam_mark + restore_from_spam) за 24ч (MSK)",
)
NOT_SPAM_MARK_TOTAL = Gauge(
    "agent_pochta_not_spam_mark_total",
    "Переход spam → not_spam (not_spam_mark + restore_from_spam) за всё время",
)
OPERATOR_SAVED_TOTAL = Gauge(
    "agent_pochta_operator_saved_total",
    "Подтверждений оператора без правок (operator_approve) за всё время",
)
OPERATOR_CHANGED_TOTAL = Gauge(
    "agent_pochta_operator_changed_total",
    "Подтверждений оператора с правками (operator_change) за всё время",
)
OPERATOR_KEEP_RATE = Gauge(
    "agent_pochta_operator_keep_rate",
    "Доля сохранений без правок: saved / (saved + changed), 0..1",
)
ROUTED_BY_DEPARTMENT = Gauge(
    "agent_pochta_routed_by_department",
    "Письма, направленные отделу (status: done/awaiting_human/error), по department_name",
    ["department"],
)
ROUTING_CORRECTIONS_BY_DEPARTMENT = Gauge(
    "agent_pochta_routing_corrections_by_department",
    "Исправления маршрутизации из routing_corrections.json, группировка по department_name",
    ["department"],
)

_LABELED_GAUGE_MAP: dict[str, Gauge] = {
    "agent_pochta_routed_by_department": ROUTED_BY_DEPARTMENT,
    "agent_pochta_routing_corrections_by_department": ROUTING_CORRECTIONS_BY_DEPARTMENT,
}

_GAUGE_MAP: dict[str, Gauge] = {
    "agent_pochta_changes_last_24h": CHANGES_LAST_24H,
    "agent_pochta_messages_last_24h": MESSAGES_LAST_24H,
    "agent_pochta_change_percent_last_24h": CHANGE_PERCENT_LAST_24H,
    "agent_pochta_messages_total": MESSAGES_TOTAL,
    "agent_pochta_changes_total": CHANGES_TOTAL,
    "agent_pochta_department_changes_last_24h": DEPARTMENT_CHANGES_LAST_24H,
    "agent_pochta_department_changes_total": DEPARTMENT_CHANGES_TOTAL,
    "agent_pochta_spam_mark_last_24h": SPAM_MARK_LAST_24H,
    "agent_pochta_spam_mark_total": SPAM_MARK_TOTAL,
    "agent_pochta_not_spam_mark_last_24h": NOT_SPAM_MARK_LAST_24H,
    "agent_pochta_not_spam_mark_total": NOT_SPAM_MARK_TOTAL,
    "agent_pochta_operator_saved_total": OPERATOR_SAVED_TOTAL,
    "agent_pochta_operator_changed_total": OPERATOR_CHANGED_TOTAL,
    "agent_pochta_operator_keep_rate": OPERATOR_KEEP_RATE,
}


def stats_timezone() -> ZoneInfo:
    settings = get_settings()
    try:
        return ZoneInfo(settings.stats_timezone)
    except Exception:
        return MSK


def rolling_24h_window_utc(tz: ZoneInfo | None = None) -> tuple[datetime, datetime]:
    """Скользящее окно 24 часа от текущего момента в заданном часовом поясе."""
    tz = tz or stats_timezone()
    now_local = datetime.now(tz)
    start_local = now_local - timedelta(hours=24)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _count_messages(
    session: Session,
    *,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(EmailMessageRow)
    if start_utc is not None:
        stmt = stmt.where(EmailMessageRow.received_at >= start_utc)
    if end_utc is not None:
        stmt = stmt.where(EmailMessageRow.received_at <= end_utc)
    return int(session.scalar(stmt) or 0)


def _count_change_events(
    session: Session,
    *,
    event_types: tuple[str, ...] | list[str],
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(ChangeEventRow)
        .where(ChangeEventRow.event_type.in_(event_types))
    )
    if start_utc is not None:
        stmt = stmt.where(ChangeEventRow.created_at >= start_utc)
    if end_utc is not None:
        stmt = stmt.where(ChangeEventRow.created_at <= end_utc)
    return int(session.scalar(stmt) or 0)


def _count_operator_events(
    session: Session,
    *,
    event_type: str,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> int:
    """Считает operator_approve / operator_change из classification_events."""
    stmt = (
        select(func.count())
        .select_from(ClassificationEventRow)
        .where(
            ClassificationEventRow.category == "department",
            ClassificationEventRow.event_type == event_type,
            ClassificationEventRow.actor == "operator",
        )
    )
    if start_utc is not None:
        stmt = stmt.where(ClassificationEventRow.created_at >= start_utc)
    if end_utc is not None:
        stmt = stmt.where(ClassificationEventRow.created_at <= end_utc)
    return int(session.scalar(stmt) or 0)


def _change_percent(changes: int, messages: int) -> float:
    if messages <= 0:
        return 0.0
    return round(changes / messages * 100.0, 4)


def _keep_rate(saved: int, changed: int) -> float:
    rate = operator_approval_rate(saved, changed)
    return float(rate) if rate is not None else 0.0


def _department_label(name: str) -> str:
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        return "(пусто)"
    if len(cleaned) > _MAX_DEPARTMENT_LABEL_LEN:
        return cleaned[: _MAX_DEPARTMENT_LABEL_LEN - 1] + "…"
    return cleaned


def _count_by_department_name(
    session: Session,
    *,
    statuses: tuple[str, ...] | None = None,
) -> dict[str, float]:
    """COUNT(*) GROUP BY trim(department_name); исключает NULL/пустые."""
    dept_col = func.trim(EmailMessageRow.department_name)
    stmt = (
        select(dept_col, func.count())
        .select_from(EmailMessageRow)
        .where(
            EmailMessageRow.department_name.isnot(None),
            dept_col != "",
        )
        .group_by(dept_col)
    )
    if statuses is not None:
        stmt = stmt.where(EmailMessageRow.status.in_(statuses))
    rows = session.execute(stmt).all()
    result: dict[str, float] = {}
    for name, count in rows:
        label = _department_label(name or "")
        if label == "(пусто)":
            continue
        result[label] = result.get(label, 0.0) + float(count or 0)
    return result


def _count_routing_corrections_by_department(
    path: Path | str | None = None,
) -> dict[str, float]:
    """COUNT entries из routing_corrections.json GROUP BY department_name.

    Отсутствующий/битый файл → пустой dict (через load_corrections).
    """
    store = load_corrections(path)
    result: dict[str, float] = {}
    for entry in store.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("department_name") or "").strip()
        if not name:
            dept_id = str(entry.get("department_id") or "").strip()
            name = dept_id
        label = _department_label(name)
        if label == "(пусто)":
            continue
        result[label] = result.get(label, 0.0) + 1.0
    return result


def collect_department_distributions(
    session: Session | None = None,
) -> dict[str, dict[str, float]]:
    """Распределения по отделам для labeled gauges (pie charts)."""

    def _collect(db_session: Session) -> dict[str, dict[str, float]]:
        return {
            "agent_pochta_routed_by_department": _count_by_department_name(
                db_session,
                statuses=ROUTED_DEPARTMENT_STATUSES,
            ),
            "agent_pochta_routing_corrections_by_department": (
                _count_routing_corrections_by_department()
            ),
        }

    if session is not None:
        return _collect(session)

    factory = get_session_factory()
    with factory() as own_session:
        return _collect(own_session)


def _set_labeled_gauge(gauge: Gauge, values: dict[str, float]) -> None:
    """Полностью перезаписывает labelsets (сбрасывает устаревшие отделы)."""
    gauge.clear()
    for department, count in values.items():
        gauge.labels(department=department).set(float(count))


def collect_metrics_snapshot(session: Session | None = None) -> dict[str, float]:
    """Считает метрики из БД; удобно для тестов и обновления Gauges."""
    start_24h, end_24h = rolling_24h_window_utc()

    def _collect(db_session: Session) -> dict[str, float]:
        changes_24h = _count_change_events(
            db_session,
            event_types=FIELD_CHANGE_EVENT_TYPES,
            start_utc=start_24h,
            end_utc=end_24h,
        )
        messages_24h = _count_messages(db_session, start_utc=start_24h, end_utc=end_24h)
        changes_total = _count_change_events(db_session, event_types=FIELD_CHANGE_EVENT_TYPES)
        messages_total = _count_messages(db_session)
        department_24h = _count_change_events(
            db_session,
            event_types=(DEPARTMENT_CHANGE_EVENT,),
            start_utc=start_24h,
            end_utc=end_24h,
        )
        department_total = _count_change_events(
            db_session,
            event_types=(DEPARTMENT_CHANGE_EVENT,),
        )
        spam_mark_24h = _count_change_events(
            db_session,
            event_types=(SPAM_MARK_EVENT,),
            start_utc=start_24h,
            end_utc=end_24h,
        )
        spam_mark_total = _count_change_events(db_session, event_types=(SPAM_MARK_EVENT,))
        not_spam_24h = _count_change_events(
            db_session,
            event_types=NOT_SPAM_EVENTS,
            start_utc=start_24h,
            end_utc=end_24h,
        )
        not_spam_total = _count_change_events(db_session, event_types=NOT_SPAM_EVENTS)
        operator_saved = _count_operator_events(db_session, event_type=OPERATOR_APPROVE_EVENT)
        operator_changed = _count_operator_events(db_session, event_type=OPERATOR_CHANGE_EVENT)

        return {
            "agent_pochta_changes_last_24h": float(changes_24h),
            "agent_pochta_messages_last_24h": float(messages_24h),
            "agent_pochta_change_percent_last_24h": _change_percent(changes_24h, messages_24h),
            "agent_pochta_messages_total": float(messages_total),
            "agent_pochta_changes_total": float(changes_total),
            "agent_pochta_department_changes_last_24h": float(department_24h),
            "agent_pochta_department_changes_total": float(department_total),
            "agent_pochta_spam_mark_last_24h": float(spam_mark_24h),
            "agent_pochta_spam_mark_total": float(spam_mark_total),
            "agent_pochta_not_spam_mark_last_24h": float(not_spam_24h),
            "agent_pochta_not_spam_mark_total": float(not_spam_total),
            "agent_pochta_operator_saved_total": float(operator_saved),
            "agent_pochta_operator_changed_total": float(operator_changed),
            "agent_pochta_operator_keep_rate": _keep_rate(operator_saved, operator_changed),
        }

    if session is not None:
        return _collect(session)

    factory = get_session_factory()
    with factory() as own_session:
        return _collect(own_session)


def refresh_prometheus_metrics(session: Session | None = None) -> dict[str, Any]:
    """Обновляет Gauges Prometheus актуальными значениями из БД."""

    def _refresh(db_session: Session) -> dict[str, Any]:
        snapshot = collect_metrics_snapshot(session=db_session)
        for name, value in snapshot.items():
            _GAUGE_MAP[name].set(value)

        distributions = collect_department_distributions(session=db_session)
        for metric_name, values in distributions.items():
            _set_labeled_gauge(_LABELED_GAUGE_MAP[metric_name], values)

        return {**snapshot, **distributions}

    if session is not None:
        return _refresh(session)

    factory = get_session_factory()
    with factory() as own_session:
        return _refresh(own_session)


def metrics_snapshot_for_tests() -> dict[str, Any]:
    """Вспомогательный экспорт имён метрик (для документации и тестов)."""
    return {
        "metrics": sorted(_GAUGE_MAP.keys()),
        "labeled_metrics": sorted(_LABELED_GAUGE_MAP.keys()),
    }
