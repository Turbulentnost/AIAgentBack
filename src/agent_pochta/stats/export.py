"""Формирование и запись статистики.json / статистика.md из change_events."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from agent_pochta.config import PROJECT_ROOT, get_settings
from agent_pochta.db.message_filters import MSK
from agent_pochta.db.models import ChangeEventRow, EmailMessageRow
from agent_pochta.stats.classification_log import collect_classification_summary_for_period
from agent_pochta.db.session import get_session_factory


def _to_db_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _format_msk(dt: datetime, tz: ZoneInfo) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def _department_key(row: EmailMessageRow) -> str:
    dept_id = (row.department_id or "").strip()
    dept_name = (row.department_name or "").strip()
    if dept_id and dept_name:
        return f"{dept_id} — {dept_name}"
    if dept_id:
        return dept_id
    if dept_name:
        return dept_name
    return "(не назначен)"


def _query_email_stats(start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        received_rows = session.scalars(
            select(EmailMessageRow).where(
                EmailMessageRow.received_at >= start_utc,
                EmailMessageRow.received_at <= end_utc,
            )
        ).all()
        processed_rows = session.scalars(
            select(EmailMessageRow).where(
                EmailMessageRow.processed_at.is_not(None),
                EmailMessageRow.processed_at >= start_utc,
                EmailMessageRow.processed_at <= end_utc,
            )
        ).all()

        status_counter: Counter[str] = Counter()
        spam_counter: Counter[str] = Counter()
        dept_counter: Counter[str] = Counter()
        human_review_counter: Counter[str] = Counter()
        priority_counter: Counter[str] = Counter()

        for row in received_rows:
            status_counter[row.status or "(unknown)"] += 1
            spam_counter["spam" if row.is_spam else "not_spam"] += 1
            dept_counter[_department_key(row)] += 1
            human_review_counter["human_review" if row.human_review else "auto"] += 1
            priority_counter[(row.priority or "(не указан)").strip()] += 1

        processed_status_counter: Counter[str] = Counter()
        for row in processed_rows:
            processed_status_counter[row.status or "(unknown)"] += 1

        attachments_total = session.execute(
            text(
                """
                SELECT COALESCE(SUM(e.attachments_count), 0)
                FROM email_messages e
                WHERE e.received_at >= :start_utc AND e.received_at <= :end_utc
                """
            ),
            {"start_utc": start_utc, "end_utc": end_utc},
        ).scalar()

    return {
        "total_received_in_window": len(received_rows),
        "total_processed_in_window": len(processed_rows),
        "attachments_total": int(attachments_total or 0),
        "by_status_received": dict(sorted(status_counter.items())),
        "by_status_processed": dict(sorted(processed_status_counter.items())),
        "by_spam": dict(sorted(spam_counter.items())),
        "by_department": dict(sorted(dept_counter.items(), key=lambda item: (-item[1], item[0]))),
        "by_human_review": dict(sorted(human_review_counter.items())),
        "by_priority": dict(sorted(priority_counter.items())),
    }


def _collect_change_events(start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        rows = session.scalars(
            select(ChangeEventRow)
            .where(
                ChangeEventRow.created_at >= start_utc,
                ChangeEventRow.created_at <= end_utc,
            )
            .order_by(ChangeEventRow.created_at.asc())
        ).all()

    by_action: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []
    for row in rows:
        by_action[row.event_type] += 1
        by_source[row.source] += 1
        entries.append(
            {
                "id": str(row.id),
                "created_at": row.created_at.isoformat(sep=" "),
                "message_id": row.message_id,
                "email_id": str(row.email_id) if row.email_id else None,
                "event_type": row.event_type,
                "field": row.field,
                "old_value": row.old_value,
                "new_value": row.new_value,
                "actor": row.actor,
                "source": row.source,
            }
        )

    counts = {
        "department_change": by_action["department_change"],
        "routing_approve": by_action["routing_approve"],
        "spam_mark": by_action["spam_mark"],
        "not_spam_mark": by_action["not_spam_mark"],
        "restore_from_spam": by_action["restore_from_spam"],
        "organization_change": by_action["organization_change"],
        "partner_change": by_action["partner_change"],
        "process_change": by_action["process_change"],
    }
    counts["total_actions"] = sum(counts.values())
    counts["total_field_changes"] = (
        counts["department_change"]
        + counts["spam_mark"]
        + counts["not_spam_mark"]
        + counts["restore_from_spam"]
        + counts["organization_change"]
        + counts["partner_change"]
        + counts["process_change"]
    )

    return {
        "source": "postgresql.change_events",
        "counts": counts,
        "by_action": dict(sorted(by_action.items())),
        "by_source": dict(sorted(by_source.items())),
        "events": entries,
    }


def _build_markdown(report: dict[str, Any]) -> str:
    tw = report["time_window"]
    emails = report["emails"]
    human = report["human_changes"]
    classification = report.get("classification_changes") or {}
    counts = human["counts"]
    lines = [
        "# Статистика agent-pochta",
        "",
        f"Сформировано: {report['generated_at']}",
        "",
        "## Период",
        "",
        f"- Локальное время ({tw['timezone']}): **{tw['from_local']}** — **{tw['to_local']}**",
        f"- Запрос к БД (UTC naive): {tw['db_query_utc']['from']} — {tw['db_query_utc']['to']}",
        f"- Пояснение: {tw['note']}",
        "",
        "## Письма",
        "",
        f"- Получено за период (received_at): **{emails['total_received_in_window']}**",
        f"- Обработано за период (processed_at): **{emails['total_processed_in_window']}**",
        f"- Вложений (сумма attachments_count): **{emails['attachments_total']}**",
        "",
        "### По статусу (полученные)",
        "",
    ]
    for status, count in emails["by_status_received"].items():
        lines.append(f"- {status}: {count}")

    lines.extend(["", "### Спам / не спам (полученные)", ""])
    for label, count in emails["by_spam"].items():
        lines.append(f"- {label}: {count}")

    lines.extend(["", "### По отделу (полученные)", ""])
    if emails["by_department"]:
        for dept, count in emails["by_department"].items():
            lines.append(f"- {dept}: {count}")
    else:
        lines.append("- (нет данных)")

    lines.extend(
        [
            "",
            "## Изменения (human-in-the-loop и связанные правки)",
            "",
            f"- Источник: **{human['source']}**",
            f"- Всего событий: **{counts['total_actions']}**",
            f"- Изменений полей (без подтверждений): **{counts['total_field_changes']}**",
            f"- Смена отдела: **{counts['department_change']}**",
            f"- Подтверждение маршрута (без смены): **{counts['routing_approve']}**",
            f"- Отметка «спам»: **{counts['spam_mark']}**",
            f"- Отметка «не спам»: **{counts['not_spam_mark']}**",
            f"- Восстановление из спама: **{counts['restore_from_spam']}**",
            f"- Организация: **{counts['organization_change']}**",
            f"- Партнёр: **{counts['partner_change']}**",
            f"- Процесс: **{counts['process_change']}**",
            "",
            "### По источнику события",
            "",
        ]
    )
    for source, count in human.get("by_source", {}).items():
        lines.append(f"- {source}: {count}")

    if human.get("events"):
        lines.extend(["", "### Журнал событий", ""])
        for entry in human["events"]:
            lines.append(
                f"- [{entry['event_type']}] {entry['created_at']} "
                f"{entry.get('old_value')} → {entry.get('new_value')} "
                f"({entry.get('message_id')}, {entry.get('source')})"
            )

    accuracy = classification.get("accuracy") or {}
    approvals = classification.get("operator_approvals") or {}
    rate = approvals.get("rate")
    rate_label = f"{round(rate * 100, 1)}%" if isinstance(rate, (int, float)) else "—"
    lines.extend(
        [
            "",
            "## Классификация (отдел / спам) — classification_events",
            "",
            f"- Источник: **{classification.get('source', 'postgresql.classification_events')}**",
            f"- Всего событий: **{classification.get('total_events', 0)}**",
            f"- Назначений отдела агентом: **{accuracy.get('agent_department_assigns', 0)}**",
            f"- Коррекций отдела оператором: **{accuracy.get('operator_department_corrections', 0)}**",
            f"- Точность отдела: **{accuracy.get('department_accuracy', '—')}**",
            f"- Назначений спама агентом: **{accuracy.get('agent_spam_assigns', 0)}**",
            f"- Коррекций спама оператором: **{accuracy.get('operator_spam_corrections', 0)}**",
            f"- Точность спама: **{accuracy.get('spam_accuracy', '—')}**",
            f"- Сохранений без изменений (saved): **{approvals.get('saved', 0)}**",
            f"- Сохранений с правками (changed): **{approvals.get('changed', 0)}**",
            f"- Доля без изменений (saved/(saved+changed)): **{rate_label}**",
            "",
        ]
    )
    for event_type, count in (classification.get("by_event_type") or {}).items():
        lines.append(f"- {event_type}: {count}")

    lines.append("")
    return "\n".join(lines)


def build_statistics_report(
    *,
    from_local: datetime,
    to_local: datetime,
    tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    tz = tz or MSK
    start_utc = _to_db_naive_utc(from_local)
    end_utc = _to_db_naive_utc(to_local)
    if start_utc > end_utc:
        raise ValueError("Начало периода позже конца")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "generated_at": generated_at,
        "time_window": {
            "from_local": _format_msk(from_local, tz),
            "to_local": _format_msk(to_local, tz),
            "timezone": str(tz),
            "db_query_utc": {
                "from": start_utc.isoformat(sep=" "),
                "to": end_utc.isoformat(sep=" "),
            },
            "note": (
                "PostgreSQL хранит received_at/processed_at/change_events.created_at как naive UTC; "
                "входные даты задаются в Europe/Moscow"
            ),
        },
        "emails": _query_email_stats(start_utc, end_utc),
        "human_changes": _collect_change_events(start_utc, end_utc),
        "classification_changes": collect_classification_summary_for_period(
            start_utc=start_utc,
            end_utc=end_utc,
        ),
    }


def export_statistics_files(
    *,
    from_local: datetime | None = None,
    to_local: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    """Строит отчёт и пишет JSON/Markdown в STATS_EXPORT_DIR (+ опционально STATS_REPO_ROOT)."""
    settings = get_settings()
    tz = tz or ZoneInfo(settings.stats_timezone)

    if from_local is None:
        raw = settings.stats_start_time.strip()
        try:
            from_local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except ValueError:
            parsed = datetime.fromisoformat(raw)
            from_local = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    elif from_local.tzinfo is None:
        from_local = from_local.replace(tzinfo=tz)

    if to_local is None:
        to_local = datetime.now(tz)
    elif to_local.tzinfo is None:
        to_local = to_local.replace(tzinfo=tz)

    report = build_statistics_report(from_local=from_local, to_local=to_local, tz=tz)
    markdown = _build_markdown(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    export_dir = Path(settings.stats_export_dir)
    if not export_dir.is_absolute():
        export_dir = (PROJECT_ROOT / export_dir).resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    json_path = export_dir / "статистика.json"
    md_path = export_dir / "статистика.md"
    json_path.write_text(payload, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")

    written = {"json": str(json_path), "markdown": str(md_path)}
    if settings.stats_repo_root:
        repo_root = Path(settings.stats_repo_root)
        if not repo_root.is_absolute():
            repo_root = (PROJECT_ROOT / repo_root).resolve()
        repo_root.mkdir(parents=True, exist_ok=True)
        repo_json = repo_root / "статистика.json"
        repo_md = repo_root / "статистика.md"
        repo_json.write_text(payload, encoding="utf-8")
        repo_md.write_text(markdown, encoding="utf-8")
        written["repo_json"] = str(repo_json)
        written["repo_markdown"] = str(repo_md)

    return {
        "ok": True,
        "written": written,
        "total_received": report["emails"]["total_received_in_window"],
        "total_changes": report["human_changes"]["counts"]["total_field_changes"],
        "period_from": report["time_window"]["from_local"],
        "period_to": report["time_window"]["to_local"],
    }
