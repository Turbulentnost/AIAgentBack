"""Графики ошибок/правок агента: смена отдела, спам-статус, точность.

Источники (приоритет):
  - classification_events — агент + оператор, метрики точности
  - change_events — fallback, если classification_events пуста

Пример:
  python scripts/plot_agent_errors.py --days 7
  python scripts/plot_agent_errors.py --days 30 --output-dir data/stats/charts
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.message_filters import msk_day_end_exclusive_utc, msk_day_start_utc  # noqa: E402
from agent_pochta.db.models import ChangeEventRow, EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.stats.classification_log import collect_classification_summary_for_period  # noqa: E402
from agent_pochta.stats.export import _collect_change_events  # noqa: E402

CHART_DPI = 180

# Спокойная палитра для всех графиков
COLORS = {
    "primary": "#4A7EBB",
    "secondary": "#C75146",
    "success": "#3D9970",
    "purple": "#7B6FAE",
    "accent": "#E09F3E",
}
STACK_COLORS = [
    "#4A7EBB",
    "#3D9970",
    "#C75146",
    "#7B6FAE",
    "#E09F3E",
    "#5B8FA8",
    "#8B6F47",
    "#6B7280",
    "#2E7D6F",
    "#9B4F96",
]

LABELS_RU = {
    "department_change": "Смена отдела",
    "routing_approve": "Подтверждение маршрута",
    "spam_mark": "Отметка «спам»",
    "not_spam_mark": "Отметка «не спам»",
    "restore_from_spam": "Восстановление из спама",
    "organization_change": "Организация",
    "partner_change": "Партнёр",
    "process_change": "Процесс",
    "agent_assign": "Назначение агентом",
    "agent_change": "Изменение агентом",
    "operator_change": "Коррекция оператором",
    "operator_approve": "Подтверждение оператором",
    "operator_mark_spam": "Спам (оператор)",
    "operator_mark_not_spam": "Не спам (оператор)",
}


def _to_db_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_created_at(value: str, tz: ZoneInfo) -> datetime:
    naive = datetime.fromisoformat(value.replace("Z", ""))
    if naive.tzinfo is None:
        naive = naive.replace(tzinfo=timezone.utc)
    return naive.astimezone(tz)


def _label(event_type: str) -> str:
    return LABELS_RU.get(event_type, event_type)


def _setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": ["Segoe UI", "DejaVu Sans", "Arial", "sans-serif"],
            "font.size": 12,
            "axes.titlesize": 16,
            "axes.titleweight": "bold",
            "axes.labelsize": 13,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "legend.fontsize": 11,
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#CCCCCC",
            "axes.labelcolor": "#333333",
            "text.color": "#333333",
            "grid.color": "#D0D0D0",
            "grid.alpha": 0.6,
        }
    )


def _style_axes(ax, *, grid_axis: str = "y") -> None:
    ax.grid(axis=grid_axis, alpha=0.45, linestyle="-", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _format_date_axis(ax, num_days: int) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    interval = max(1, num_days // 12)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=interval))
    if num_days > 7:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")


def _save(fig: Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=1.2)
    fig.savefig(path, dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _daily_timeline_from_classification(
    events: list[dict[str, Any]],
    tz: ZoneInfo,
) -> dict[str, Counter[str]]:
    by_day: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in events:
        day = _parse_created_at(entry["created_at"], tz).strftime("%Y-%m-%d")
        key = f"{entry['category']}:{entry['event_type']}"
        by_day[day][key] += 1
    return by_day


def _daily_timeline_from_changes(
    events: list[dict[str, Any]],
    tz: ZoneInfo,
) -> dict[str, Counter[str]]:
    by_day: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in events:
        day = _parse_created_at(entry["created_at"], tz).strftime("%Y-%m-%d")
        by_day[day][entry["event_type"]] += 1
    return by_day


def _plot_timeline(
    by_day: dict[str, Counter[str]],
    *,
    title: str,
    output_path: Path,
) -> None:
    if not by_day:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.text(0.5, 0.5, "Нет событий за период", ha="center", va="center", fontsize=14)
        ax.axis("off")
        ax.set_title(title, fontsize=16, pad=12)
        _save(fig, output_path)
        return

    days = sorted(by_day)
    all_keys = sorted({key for counter in by_day.values() for key in counter})
    x = [datetime.strptime(day, "%Y-%m-%d") for day in days]

    fig, ax = plt.subplots(figsize=(14, 6))
    bottom = [0.0] * len(days)
    for idx, key in enumerate(all_keys):
        values = [by_day[day].get(key, 0) for day in days]
        label = _label(key.split(":", 1)[-1]) if ":" in key else _label(key)
        color = STACK_COLORS[idx % len(STACK_COLORS)]
        ax.bar(x, values, bottom=bottom, label=label, width=0.75, color=color, edgecolor="white", linewidth=0.5)
        bottom = [b + v for b, v in zip(bottom, values)]

    _format_date_axis(ax, len(days))
    ax.set_title(title, fontsize=16, pad=14)
    ax.set_xlabel("Дата", labelpad=8)
    ax.set_ylabel("Количество событий", labelpad=8)
    ax.legend(
        fontsize=11,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=min(3, max(1, len(all_keys))),
        frameon=True,
        edgecolor="#CCCCCC",
    )
    _style_axes(ax)
    fig.subplots_adjust(bottom=0.22)
    _save(fig, output_path)


def _plot_bar_counts(
    counts: dict[str, int],
    *,
    title: str,
    output_path: Path,
    color: str = COLORS["primary"],
) -> None:
    row_height = 0.55
    fig, ax = plt.subplots(figsize=(10, max(4, len(counts) * row_height + 1.5)))
    if not counts:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", fontsize=14)
        ax.axis("off")
        ax.set_title(title, fontsize=16, pad=12)
        _save(fig, output_path)
        return

    labels = [_label(key) for key in counts]
    values = list(counts.values())
    y_pos = range(len(labels))
    bars = ax.barh(list(y_pos), values, color=color, height=0.6, edgecolor="white", linewidth=0.5)
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=12)
    ax.set_title(title, fontsize=16, pad=14)
    ax.set_xlabel("Количество", labelpad=8)
    max_val = max(values) if values else 0
    offset = max(max_val * 0.02, 0.3)
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_width() + offset,
            bar.get_y() + bar.get_height() / 2,
            str(value),
            va="center",
            fontsize=12,
            fontweight="bold",
            color="#333333",
        )
    ax.set_xlim(0, max_val * 1.15 + offset if max_val else 1)
    _style_axes(ax, grid_axis="x")
    _save(fig, output_path)


def _plot_accuracy(accuracy: dict[str, Any], *, title: str, output_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    dept_assigns = accuracy.get("agent_department_assigns", 0)
    dept_corr = accuracy.get("operator_department_corrections", 0)
    spam_assigns = accuracy.get("agent_spam_assigns", 0)
    spam_corr = accuracy.get("operator_spam_corrections", 0)

    for ax, assigns, corrections, acc_key, caption in (
        (axes[0], dept_assigns, dept_corr, "department_accuracy", "Отдел"),
        (axes[1], spam_assigns, spam_corr, "spam_accuracy", "Спам"),
    ):
        ok = max(assigns - corrections, 0)
        bars = [ok, corrections]
        labels = ["Без коррекции", "Коррекция"]
        colors = [COLORS["success"], COLORS["secondary"]]
        if assigns == 0 and corrections == 0:
            ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", fontsize=14)
            ax.set_title(caption, fontsize=14)
            ax.axis("off")
            continue
        bar_objs = ax.bar(labels, bars, color=colors, width=0.55, edgecolor="white", linewidth=0.5)
        acc = accuracy.get(acc_key)
        acc_text = f"{acc * 100:.1f}%" if acc is not None else "—"
        ax.set_title(f"{caption} (точность: {acc_text})", fontsize=14, pad=10)
        ax.set_ylabel("События агента", labelpad=8)
        ax.set_xticklabels(labels, fontsize=12)
        ymax = max(bars) if bars else 1
        for bar, value in zip(bar_objs, bars):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + ymax * 0.04,
                str(value),
                ha="center",
                fontsize=13,
                fontweight="bold",
            )
        ax.set_ylim(0, ymax * 1.18 if ymax else 1)
        _style_axes(ax)

    fig.suptitle(title, fontsize=16, fontweight="bold", y=1.02)
    _save(fig, output_path)


def _classification_department_counts(by_event_type: dict[str, int]) -> dict[str, int]:
    return {
        k: by_event_type[k]
        for k in ("operator_change", "operator_approve", "agent_assign", "agent_change")
        if by_event_type.get(k)
    }


def _classification_spam_counts(by_event_type: dict[str, int]) -> dict[str, int]:
    return {
        k: by_event_type[k]
        for k in (
            "operator_mark_spam",
            "operator_mark_not_spam",
            "restore_from_spam",
            "agent_assign",
            "agent_change",
        )
        if by_event_type.get(k)
    }


def _change_department_counts(counts: dict[str, int]) -> dict[str, int]:
    return {k: counts[k] for k in ("department_change", "routing_approve") if counts.get(k)}


def _change_spam_counts(counts: dict[str, int]) -> dict[str, int]:
    return {
        k: counts[k]
        for k in ("spam_mark", "not_spam_mark", "restore_from_spam")
        if counts.get(k)
    }


def _naive_utc_to_local_day(dt: datetime, tz: ZoneInfo) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d")


def _collect_daily_change_percent_stats(
    *,
    days: list[date],
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    if not days:
        return []

    start_utc = msk_day_start_utc(days[0])
    end_utc = msk_day_end_exclusive_utc(days[-1])
    factory = get_session_factory()
    with factory() as session:
        processed_rows = session.scalars(
            select(EmailMessageRow.processed_at).where(
                EmailMessageRow.processed_at.is_not(None),
                EmailMessageRow.processed_at >= start_utc,
                EmailMessageRow.processed_at < end_utc,
            )
        ).all()
        change_rows = session.execute(
            select(ChangeEventRow.created_at, ChangeEventRow.message_id).where(
                ChangeEventRow.created_at >= start_utc,
                ChangeEventRow.created_at < end_utc,
            )
        ).all()

    messages_by_day: dict[str, int] = defaultdict(int)
    for processed_at in processed_rows:
        messages_by_day[_naive_utc_to_local_day(processed_at, tz)] += 1

    changes_by_day: dict[str, set[str]] = defaultdict(set)
    for created_at, message_id in change_rows:
        changes_by_day[_naive_utc_to_local_day(created_at, tz)].add(message_id)

    stats: list[dict[str, Any]] = []
    for day in days:
        day_key = day.isoformat()
        total_messages = messages_by_day.get(day_key, 0)
        changes_count = len(changes_by_day.get(day_key, set()))
        change_percent = (changes_count / total_messages * 100) if total_messages else 0.0
        stats.append(
            {
                "day": day_key,
                "total_messages": total_messages,
                "changes_count": changes_count,
                "change_percent": change_percent,
            }
        )
    return stats


def _plot_daily_change_percent(
    stats: list[dict[str, Any]],
    *,
    title: str,
    output_path: Path,
) -> None:
    fig, ax1 = plt.subplots(figsize=(14, 6))
    if not stats:
        ax1.text(0.5, 0.5, "Нет данных", ha="center", va="center", fontsize=14)
        ax1.axis("off")
        ax1.set_title(title, fontsize=16, pad=12)
        _save(fig, output_path)
        return

    days = [datetime.strptime(item["day"], "%Y-%m-%d") for item in stats]
    totals = [item["total_messages"] for item in stats]
    percents = [item["change_percent"] for item in stats]

    ax1.bar(
        days,
        totals,
        color=COLORS["primary"],
        alpha=0.85,
        width=0.75,
        label="Сообщений за день",
        edgecolor="white",
        linewidth=0.5,
        zorder=2,
    )
    ax1.set_ylabel("Сообщений за день", color=COLORS["primary"], labelpad=8)
    ax1.tick_params(axis="y", labelcolor=COLORS["primary"])
    ax1.set_xlabel("Дата", labelpad=8)
    _format_date_axis(ax1, len(days))
    _style_axes(ax1)

    ax2 = ax1.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.plot(
        days,
        percents,
        color=COLORS["secondary"],
        marker="o",
        markersize=7,
        linewidth=2.5,
        label="% изменений",
        zorder=3,
    )
    ax2.set_ylabel("% изменений (уник. письма)", color=COLORS["secondary"], labelpad=8)
    ax2.tick_params(axis="y", labelcolor=COLORS["secondary"])

    for day, total in zip(days, totals):
        if total > 0:
            ax1.text(day, total, str(total), ha="center", va="bottom", fontsize=10, fontweight="bold")

    show_pct_labels = len(days) <= 14
    for day, percent in zip(days, percents):
        if show_pct_labels and (percent > 0 or len(days) <= 7):
            ax2.annotate(
                f"{percent:.1f}%",
                (day, percent),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=10,
                color=COLORS["secondary"],
                fontweight="bold",
            )

    ax1.set_title(title, fontsize=16, pad=14)
    legend_handles = [
        Patch(facecolor=COLORS["primary"], alpha=0.85, label="Сообщений за день"),
        Line2D(
            [0],
            [0],
            color=COLORS["secondary"],
            marker="o",
            markersize=7,
            linewidth=2.5,
            label="% изменений (уник. письма)",
        ),
    ]
    ax1.legend(handles=legend_handles, loc="upper left", frameon=True, edgecolor="#CCCCCC")
    fig.subplots_adjust(bottom=0.15 if len(days) > 7 else 0.1)
    _save(fig, output_path)


CHART_TITLES_RU = {
    "daily_change_percent.png": "Сообщения и % коррекций по дням",
    "timeline_changes_by_type.png": "Динамика изменений по типу",
    "department_corrections.png": "Отдел: назначения и коррекции",
    "spam_status_changes.png": "Спам-статус: изменения",
    "accuracy_metrics.png": "Точность классификации",
}


def _write_html_summary(
    output_dir: Path,
    *,
    period_from: str,
    period_to: str,
    source: str,
    written: list[str],
    accuracy: dict[str, Any] | None,
) -> Path:
    html_path = output_dir / "index.html"
    lines = [
        "<!DOCTYPE html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Графики ошибок agent-pochta</title>",
        "<style>",
        "  :root { color-scheme: light; }",
        "  body { font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 2rem; "
        "background: #f5f6f8; color: #1f2937; line-height: 1.5; }",
        "  .container { max-width: 1100px; margin: 0 auto; }",
        "  h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }",
        "  .meta { color: #4b5563; margin-bottom: 1.5rem; }",
        "  .meta code { background: #e5e7eb; padding: 0.1rem 0.4rem; border-radius: 4px; }",
        "  .accuracy { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; "
        "padding: 1rem 1.25rem; margin-bottom: 1.5rem; }",
        "  .accuracy ul { margin: 0.5rem 0 0; padding-left: 1.25rem; }",
        "  .chart { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; "
        "padding: 1.25rem; margin-bottom: 1.5rem; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }",
        "  .chart h2 { font-size: 1.15rem; margin: 0 0 1rem; color: #111827; }",
        "  .chart img { display: block; max-width: 100%; height: auto; border-radius: 4px; }",
        "</style>",
        "</head><body>",
        '<div class="container">',
        "<h1>Ошибки и правки агента</h1>",
        f'<p class="meta">Период: <b>{period_from}</b> — <b>{period_to}</b><br>'
        f'Источник: <code>{source}</code></p>',
    ]
    if accuracy:
        dept = accuracy.get("department_accuracy")
        spam = accuracy.get("spam_accuracy")
        lines.append('<div class="accuracy"><h2>Точность</h2><ul>')
        lines.append(
            f"<li>Отдел: {dept * 100:.1f}%</li>" if dept is not None else "<li>Отдел: —</li>"
        )
        lines.append(f"<li>Спам: {spam * 100:.1f}%</li>" if spam is not None else "<li>Спам: —</li>")
        lines.append("</ul></div>")

    for rel in written:
        name = Path(rel).name
        heading = CHART_TITLES_RU.get(name, name)
        lines.append(f'<div class="chart"><h2>{heading}</h2><img src="{name}" alt="{heading}"></div>')

    lines.extend(["</div>", "</body></html>"])
    html_path.write_text("\n".join(lines), encoding="utf-8")
    return html_path


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Графики ошибок/правок агента из PostgreSQL")
    parser.add_argument("--days", type=int, default=7, help="Глубина периода в днях (по умолчанию 7)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/stats/charts"),
        help="Каталог для PNG/HTML (по умолчанию data/stats/charts)",
    )
    parser.add_argument(
        "--timezone",
        default=settings.stats_timezone,
        help="Часовой пояс (по умолчанию STATS_TIMEZONE)",
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Не создавать index.html",
    )
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)
    to_local = datetime.now(tz)
    from_local = to_local - timedelta(days=max(args.days, 1))
    start_utc = _to_db_naive_utc(from_local)
    end_utc = _to_db_naive_utc(to_local)

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    _setup_style()

    classification = collect_classification_summary_for_period(start_utc=start_utc, end_utc=end_utc)
    changes = _collect_change_events(start_utc, end_utc)

    use_classification = classification.get("total_events", 0) > 0
    source = classification["source"] if use_classification else changes["source"]

    if use_classification:
        timeline = _daily_timeline_from_classification(classification["events"], tz)
        dept_counts = _classification_department_counts(classification.get("by_event_type", {}))
        spam_counts = _classification_spam_counts(classification.get("by_event_type", {}))
        accuracy = classification.get("accuracy")
    else:
        timeline = _daily_timeline_from_changes(changes["events"], tz)
        dept_counts = _change_department_counts(changes["counts"])
        spam_counts = _change_spam_counts(changes["counts"])
        accuracy = None

    period_from = from_local.strftime("%Y-%m-%d %H:%M")
    period_to = to_local.strftime("%Y-%m-%d %H:%M")
    title_suffix = f"({period_from} — {period_to}, {tz})"

    today = to_local.date()
    calendar_days = [today - timedelta(days=args.days - 1 - i) for i in range(args.days)]
    daily_stats = _collect_daily_change_percent_stats(days=calendar_days, tz=tz)

    written: list[str] = []
    charts = [
        (
            "daily_change_percent.png",
            lambda p: _plot_daily_change_percent(
                daily_stats,
                title=f"Сообщения и % коррекций по дням {title_suffix}",
                output_path=p,
            ),
        ),
        (
            "timeline_changes_by_type.png",
            lambda p: _plot_timeline(
                timeline,
                title=f"Динамика изменений по типу {title_suffix}",
                output_path=p,
            ),
        ),
        (
            "department_corrections.png",
            lambda p: _plot_bar_counts(
                dept_counts,
                title=f"Отдел: назначения и коррекции {title_suffix}",
                output_path=p,
                color=COLORS["purple"],
            ),
        ),
        (
            "spam_status_changes.png",
            lambda p: _plot_bar_counts(
                spam_counts,
                title=f"Спам-статус: изменения {title_suffix}",
                output_path=p,
                color=COLORS["secondary"],
            ),
        ),
    ]

    if accuracy and (
        accuracy.get("agent_department_assigns") or accuracy.get("agent_spam_assigns")
    ):
        charts.append(
            (
                "accuracy_metrics.png",
                lambda p: _plot_accuracy(
                    accuracy,
                    title=f"Точность классификации {title_suffix}",
                    output_path=p,
                ),
            )
        )

    for filename, draw_fn in charts:
        path = output_dir / filename
        draw_fn(path)
        written.append(filename)
        print(f"PNG: {path}")

    if not args.no_html:
        html_path = _write_html_summary(
            output_dir,
            period_from=period_from,
            period_to=period_to,
            source=source,
            written=written,
            accuracy=accuracy,
        )
        print(f"HTML: {html_path}")

    total_events = classification.get("total_events", 0) or changes["counts"]["total_actions"]
    print(f"Источник: {source}, событий: {total_events}")
    print("Дневная статистика (сообщения / изменения / %):")
    for item in daily_stats:
        print(
            f"  {item['day']}: {item['total_messages']} / "
            f"{item['changes_count']} / {item['change_percent']:.1f}%"
        )


if __name__ == "__main__":
    main()
